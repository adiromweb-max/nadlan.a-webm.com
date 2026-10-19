#!/usr/bin/env python
"""
בדיקה עצמית של הצינור — בלי תלות ביד2 (שדורש ScraperAPI) וללא שליחת מייל.

מוכיחה שהמנגנונים הקריטיים עובדים:
  1. נוסחת ירידת המחיר + חסם שגיאת הנתונים
  2. upsert + זיהוי ירידת מחיר בין ריצות (price_history)
  3. דדופ התראות (first_alerted_at / last_alerted_score)
  4. השוואה like-for-like מול בסיס עסקאות רשמי אמיתי
  5. תשואה, עליית ערך (CAGR) ודירוג
  6. אקסל (5 גיליונות, קישורים), latest.json וגרפים

הרצה:  ./venv/bin/python selftest.py
משתמשת ב-DB זמני — לא נוגעת ב-data/nadlan.db האמיתי.
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from scout import (alerts, charts, comps, dashboard, db, excel, jsonout,
                   nadlan, pricing, scoring, sections)
from scout.config import load_config
from scout.http import Fetcher

FAILURES = []


def check(name, cond, detail=""):
    status = "עובר" if cond else "נכשל"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)
    return cond


def test_market_age():
    """
    ותק בשוק: תאריך הפרסום מתאריכי התמונות, וכיוון הניקוד.

    שני הבאגים שהבדיקות כאן נועלות:
    1. ‎priceBeforeTag‎ הוא שם השדה האמיתי של המחיר הקודם בפיד. בגרסה
       קודמת הוא לא היה ברשימת המועמדים, ולכן תיקון "ירידת הרפאים" לא
       תפס אף ירידה בפרודקשן.
    2. הוותק חושב מ-‎first_seen‎ (מתי המנוע ראה), ולכן מודעה ששוכבת
       שנים בשוק נראתה טרייה — ובנוסף נקנסה כ"מחיר שהשוק דחה".
    """
    from scout import yad2
    from scout.scoring import score_days_on_market, supporting_signal
    print("\n— ותק בשוק ותאריך הפרסום —")

    url = "https://img.yad2.co.il/Pic/202607/21/2_1/o/y2_1_02041_20260721181739.jpeg"
    check("תאריך פרסום מחולץ מנתיב התמונה",
          yad2._img_date(url) == "2026-07-21", yad2._img_date(url))
    check("כתובת בלי חתימת תאריך לא מייצרת תאריך",
          yad2._img_date("https://img.yad2.co.il/Pic/none.jpeg") is None)

    item = {"token": "t1", "price": 490000,
            "priceBeforeTag": 560000,
            "address": {"city": {"text": "באר שבע"}},
            "additionalDetails": {"roomsCount": 3, "squareMeter": 70,
                                  "property": {"text": "דירה"}},
            "metaData": {"coverImage": url,
                         "images": [
                             "https://img.yad2.co.il/Pic/202608/02/2_1/o/y2_1_1_20260802101010.jpeg",
                             url]}}
    check("priceBeforeTag נתפס כמחיר קודם",
          yad2._previous_price(item) == 560000, str(yad2._previous_price(item)))
    parsed = yad2.parse_item(item, "באר שבע")
    check("parse_item מחזיר previous_price", parsed["previous_price"] == 560000)
    # המוקדם מכל התמונות — תמונה שנוספה מאוחר לא מאפסת את הוותק
    check("listed_at = התמונה המוקדמת מכולן",
          parsed["listed_at"] == "2026-07-21", str(parsed["listed_at"]))
    check("תאריך תמונה עתידי נדחה",
          yad2.listed_at(item, "2026-01-01") is None)

    cfg = {"stale_suspect_days": 180, "max_plausible_days_on_market": 1825}
    t = date(2026, 10, 18)
    # 3 שנים בשוק, בלי ירידת מחיר — הגרסה הקודמת נתנה כאן 0
    s_old_ad, d_old_ad, stale = score_days_on_market(
        "2026-08-01", 14, 270, t, drop_pct=None, cfg=cfg, listed_at="2023-10-18")
    check("ותק נמדד מ-listed_at ולא מ-first_seen", d_old_ad == 1096, str(d_old_ad))
    check("ותק ארוך בלי ירידת מחיר מקבל ניקוד מלא (הכיוון התהפך)",
          s_old_ad == 14.0, str(s_old_ad))
    check("עומד בלי ירידה עדיין מסומן כדגל תצוגה", stale is True)
    # בלי תמונות — נפילה חזרה ל-first_seen, בלי לשבור
    s_fb, d_fb, _ = score_days_on_market(
        "2026-08-18", 14, 270, t, drop_pct=None, cfg=cfg, listed_at=None)
    check("בלי listed_at נופלים חזרה ל-first_seen", d_fb == 61, str(d_fb))
    # חסם שפיות: תאריך תמונה שגוי לא מייצר מודעה בת 20 שנה
    _s, d_cap, _ = score_days_on_market(
        "2026-08-01", 14, 270, t, cfg=cfg, listed_at="2001-01-01")
    check("תאריך שגוי נחסם בחסם השפיות", d_cap == 1825, str(d_cap))
    # ותק ארוך הוא אות תומך בזכות עצמו, בלי לדרוש ירידת מחיר
    has, sigs = supporting_signal(None, 400, 0, {"signal_min_days_on_market": 60}, 0)
    check("ותק ארוך לבדו הוא אות תומך", has and any("בשוק" in x for x in sigs),
          "; ".join(sigs))

    # הדגל "מוערך" מסייג רק כשתאריך התמונות אכן הוסיף ותק. באג שנתפס
    # בבדיקה: אחרי ההשלמה אחורה ל-DB יש ‎listed_at‎ לכל שורה, ולכן תנאי
    # "קיים ערך" סימן את כל 1,102 המודעות כמוערכות — כולל אלה בלי תמונות.
    from scout.scoring import score_listing as _sl
    scfg = {"weights": {"price_gap": 38, "price_drop": 15, "days_on_market": 14,
                        "opportunity_keywords": 5, "area_liquidity": 3,
                        "rental_yield": 15, "area_appreciation": 10},
            "price_gap_full_at_pct": 25, "days_on_market_full_at": 270,
            "max_plausible_days_on_market": 1825, "stale_suspect_days": 180,
            "tiers": {}, "keywords": []}
    lst = {"price": 920000, "size_sqm": 80, "price_per_sqm": 11500,
           "city": "באר שבע", "text": ""}
    r_est = _sl(lst, scfg, benchmark_ppsqm=12000, first_seen="2026-08-28",
                listed_at="2023-07-02", today=date(2026, 10, 18))
    r_obs = _sl(lst, scfg, benchmark_ppsqm=12000, first_seen="2026-08-28",
                listed_at="2026-08-28", today=date(2026, 10, 18))
    check("ותק מתאריך התמונות מסומן כמוערך",
          r_est["dom_estimated"] and r_est["days_on_market"] == 1204,
          f'{r_est["days_on_market"]} ימים')
    check("ותק מתצפית שלנו אינו מסומן כמוערך",
          not r_obs["dom_estimated"] and r_obs["days_on_market"] == 51,
          f'{r_obs["days_on_market"]} ימים')


def test_mlm():
    """
    מחיר למשתכן: זיהוי מרחבי, כרית המו"מ, לחץ ההיצע ושני המסלולים.

    הבאג שהבדיקות כאן נועלות: הפיתוי לחבר את שכבת משרד השיכון ליד2 לפי
    שם שכונה. זה נמדד ונכשל — 28 התאמות מול 53 כשלונות, ועם זיווגי שווא
    ("סיגליות" ל"שכונה ג'", כי האות ג' מופיעה במילה). הזיהוי חייב להיות
    נקודה-בתוך-פוליגון, ולכן כאן נבדקת הגאומטריה ולא טקסט.
    """
    from scout import mlm
    from scout import yad2 as _y2
    from scout.scoring import score_mlm, supporting_signal
    print("\n— מחיר למשתכן —")

    layer = Path(__file__).resolve().parent / "data" / "mlm_projects.geojson"
    check("שכבת הפרויקטים קיימת בריפו", layer.exists(), str(layer))
    P = mlm.Projects()
    check("השכבה נטענת", bool(P), f"{len(P.features)} מתחמים")

    # ריבוע יחידה מלאכותי — בודק את הגאומטריה עצמה, בלי תלות בנתון אמיתי
    import json as _json, tempfile as _tf
    sq = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"city": "בדיקה", "project": "ריבוע", "units": 100,
                       "orig_ppm": 6000.0, "release": "2020-01-01"},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}}]}
    tmpf = Path(_tf.mkdtemp()) / "sq.geojson"
    tmpf.write_text(_json.dumps(sq), encoding="utf-8")
    T = mlm.Projects(tmpf)
    check("נקודה בתוך הפוליגון מזוהה", T.find(0.5, 0.5) is not None)
    check("נקודה מחוץ לפוליגון אינה מזוהה", T.find(2.0, 2.0) is None)
    check("נקודה בלי קואורדינטות אינה מפילה", T.find(None, None) is None)

    # כרית: 90 מ"ר שנקנו ב-6,000 ₪/מ"ר = 540,000, מבוקש 900,000
    L = {"lat": 0.5, "lon": 0.5, "price": 900000, "size_sqm": 90}
    d = mlm.describe(L, T, date(2026, 10, 18))
    check("המגבלה מזוהה כפגה", d.get("mlm_released") is True)
    check("אומדן מה ששולם נכון", d.get("mlm_paid_est") == 540000, str(d.get("mlm_paid_est")))
    check("הכרית מחושבת נכון", d.get("mlm_cushion") == 360000, str(d.get("mlm_cushion")))
    check("הכרית באחוזים נכונה", d.get("mlm_cushion_pct") == 40.0, str(d.get("mlm_cushion_pct")))
    # גודל חריג לא מייצר כרית אבסורדית
    big = mlm.describe({"lat": 0.5, "lon": 0.5, "price": 900000, "size_sqm": 900},
                       T, date(2026, 10, 18))
    check("גודל לא-סביר אינו מייצר כרית", "mlm_cushion" not in big)
    # מגבלה שטרם פגה אינה מזכה בניקוד
    sq["features"][0]["properties"]["release"] = "2031-01-01"
    tmpf.write_text(_json.dumps(sq), encoding="utf-8")
    T2 = mlm.Projects(tmpf)
    d2 = mlm.describe(L, T2, date(2026, 10, 18))
    check("מגבלה שטרם פגה מסומנת", d2.get("mlm_released") is False)
    check("פרויקט שטרם שוחרר אינו מזכה בניקוד",
          score_mlm({**L, **d2}, 12, {}) == 0.0)

    # לחץ היצע
    group = [{"mlm": True, "mlm_project": "ריבוע", "city": "בדיקה", "mlm_units": 100}
             for _ in range(4)]
    mlm.supply_pressure(group)
    check("לחץ ההיצע נספר", group[0]["mlm_on_sale"] == 4)
    check("לחץ ההיצע באחוזים", group[0]["mlm_supply_pct"] == 4.0,
          str(group[0]["mlm_supply_pct"]))

    # הניקוד המלא נבדק בוודאות גבוהה במפורש: הפיקסצ'ר המלאכותי יושב
    # בשכבה אחת בלבד ובלי מצב נכס, ולכן ודאותו האמיתית נמוכה — וזה נבדק
    # בנפרד למטה. כאן נבדקת נוסחת הכרית והלחץ עצמה.
    scored = score_mlm({**L, **d, "mlm_supply_pct": 3.0, "mlm_confidence": "גבוהה"},
                       12, {"mlm_cushion_full_at_pct": 40, "mlm_supply_full_at_pct": 3.0})
    check("כרית מלאה + לחץ מלא = ניקוד מלא", scored == 12.0, str(scored))
    has, sigs = supporting_signal(None, 0, 0, {"signal_min_cushion_pct": 20}, 0,
                                  listing={**L, **d, "mlm_confidence": "גבוהה"})
    check("כרית משמעותית היא אות תומך",
          has and any("מחיר למשתכן" in s for s in sigs), "; ".join(sigs))

    # ── ודאות הזיהוי ──
    # זו הנקודה הקריטית: אם חלק מהמודעות שזוהו אינן מחיר למשתכן, הטענה
    # שגל השחרור מרכך את המחירים מתפרקת ל"אזור מוצף משום מה".
    from scout.mlm import confidence as _conf
    from scout.yad2 import CONDITION_TEXT as _CT
    check("קוד 6 ממופה — 'חדש (גרו בנכס)'",
          _CT.get(6) == "חדש (גרו בנכס)", str(_CT.get(6)))
    check("קוד 1 ממופה — חדש מקבלן", _CT.get(1) == "חדש מקבלן (לא גרו בנכס)")
    check("שתי שכבות + בניין חדש = ודאות גבוהה",
          _conf(True, True, 6) == "גבוהה", str(_conf(True, True, 6)))
    check("שתי שכבות בלי בניין חדש = בינונית",
          _conf(True, True, 2) == "בינונית", str(_conf(True, True, 2)))
    check("שכבה אחת + בניין חדש = בינונית", _conf(True, False, 6) == "בינונית")
    check("שכבה אחת בלבד = נמוכה", _conf(False, True, 3) == "נמוכה")
    check("מחוץ לשתי השכבות = ללא זיהוי", _conf(False, False, 6) is None)

    lotsp = Path(__file__).resolve().parent / "data" / "mlm_lots.json"
    check("שכבת המגרשים קיימת בריפו", lotsp.exists())
    LT = mlm.Lots()
    check("שכבת המגרשים נטענת", bool(LT), f"{LT.n} מגרשים")
    # מגרש שלא קיים ברשת לא מפיל ולא מחזיר תוצאת שווא
    check("נקודה באמצע הים אינה בתוך מגרש", LT.find(32.0, 33.0) is None)

    # ודאות נמוכה מקטינה את הניקוד — לא רק מוצגת
    base = {**L, **d, "mlm_supply_pct": 3.0}
    scfg2 = {"mlm_cushion_full_at_pct": 40, "mlm_supply_full_at_pct": 3.0}
    hi = score_mlm({**base, "mlm_confidence": "גבוהה"}, 12, scfg2)
    lo = score_mlm({**base, "mlm_confidence": "נמוכה"}, 12, scfg2)
    check("ודאות נמוכה מקטינה את ניקוד המינוף", lo < hi, f"{lo} מול {hi}")
    # ורק ודאות גבוהה נחשבת אות תומך
    _h1, _s1 = supporting_signal(None, 0, 0, {"signal_min_cushion_pct": 20}, 0,
                                 listing={**base, "mlm_confidence": "נמוכה"})
    check("ודאות נמוכה אינה אות תומך",
          not any("מחיר למשתכן" in s for s in _s1), "; ".join(_s1) or "אין אותות")

    # שני המסלולים
    c = load_config()
    w = c["weights"]
    check("סכום המשקלים הוא 100", sum(w.values()) == 100, str(sum(w.values())))
    check("mlm_leverage נספר בנרמול השער המקדים",
          "mlm_leverage" in str(scoring_src()),
          "אחרת השער היה דורש אחוז גבוה יותר וחוסם את הצינור")
    wide = _y2.price_band(c, "באר שבע")
    narrow = _y2.price_band(c, "ירוחם")
    check("עיר במסלול הארצי מקבלת טווח רחב", wide[1] > narrow[1],
          f"באר שבע {wide} מול ירוחם {narrow}")
    union = list(dict.fromkeys(list(c["cities"]) + (c.get("mlm_cities") or [])))
    check("עיר שבשתי הרשימות נסרקת פעם אחת",
          union.count("באר שבע") == 1 and len(union) < len(c["cities"]) + len(c["mlm_cities"]),
          f"{len(union)} ערים בסך הכול")
    credits = len(union) * 30
    check("הסריקה בתוך תקציב הקרדיטים",
          credits <= int(c["scraperapi_max_credits_per_run"]),
          f"{credits} מתוך {c['scraperapi_max_credits_per_run']}")


def scoring_src():
    from scout import scoring as _s
    import inspect
    return inspect.getsource(_s.prescreen_reachable_max)


def main():
    cfg = load_config()
    today = date.today()
    test_market_age()
    test_mlm()
    tmp = Path(tempfile.mkdtemp(prefix="nadlan-selftest-"))
    cfg["paths"]["db"] = tmp / "test.db"
    cfg["paths"]["out"] = tmp / "out"

    print("\n=== 1. נוסחת ירידת המחיר ===")
    a = pricing.assert_drop_math()
    check("400,000→385,000 מוצג כ-3.8", a["ok"], a["text"])
    check("1,000,000→963,000 מוצג כ-3.7",
          round(pricing.drop_pct(1000000, 963000), 1) == 3.7)
    check("ירידה של 90% מסומנת כשגיאת נתונים",
          not pricing.is_drop_sane(90.0))
    check("ירידה של 3.7% נחשבת תקינה", pricing.is_drop_sane(3.7))

    print("\n=== 1ב. עלויות רכישה והפער נטו ===")
    # ברירת המחדל היא דירה ראשונה: בטווח הדרומי המס הוא אפס
    check("ברירת המחדל היא דירה ראשונה",
          pricing.buyer_track(cfg) == "first_home", pricing.buyer_track(cfg))
    check("דירה ראשונה על 599,000 — מס רכישה אפס",
          round(pricing.purchase_tax_ils(599000, cfg)) == 0,
          f"{pricing.purchase_tax_ils(599000, cfg):,.0f} ₪")
    check("דירה ראשונה על 2.5 מיליון — כבר יש מס",
          pricing.purchase_tax_ils(2_500_000, cfg) > 20000,
          f"{pricing.purchase_tax_ils(2_500_000, cfg):,.0f} ₪")
    tax = pricing.purchase_tax_ils(599000, cfg, "second_home")
    check("דירה שנייה על אותו מחיר — 8%", round(tax) == 47920, f"{tax:,.0f} ₪")
    c_fh = pricing.acquisition_costs(599000, cfg, is_agency=True)
    check("עלויות דירה ראשונה בתיווך ≈ 2.9%",
          2.5 < c_fh["total_pct"] < 3.2, f'{c_fh["total_pct"]}%')
    c_ag = pricing.acquisition_costs(599000, cfg, is_agency=True, track="second_home")
    c_pr = pricing.acquisition_costs(599000, cfg, is_agency=False, track="second_home")
    check("מודעת תיווך יקרה ביותר מ-2% ממודעה פרטית",
          c_ag["total_pct"] - c_pr["total_pct"] > 2.0,
          f'תיווך {c_ag["total_pct"]}% מול פרטי {c_pr["total_pct"]}%')
    ng = pricing.net_gap_pct(21.0, c_ag["total_pct"])
    check("פער 21% מצטמק אחרי עלויות", ng is not None and 10 < ng < 15,
          f'21% גולמי → {ng}% נטו (עלויות {c_ag["total_pct"]}%)')
    check("פער 8% (הסף הישן) הופך שלילי",
          pricing.net_gap_pct(8.0, c_ag["total_pct"]) < 0,
          f'8% גולמי → {pricing.net_gap_pct(8.0, c_ag["total_pct"])}% נטו')
    check('אנומליית 144 מ"ר / 3 חדרים מזוהה',
          pricing.sqm_per_room(144, 3) > cfg.get("sqm_per_room_max", 45),
          f'{pricing.sqm_per_room(144, 3):.0f} מ"ר לחדר')

    print("\n=== 1ד. אימות ברמת רחוב ===")
    subject = {"id": "t:s0", "city": "באר שבע", "street": "דרך מצדה",
               "rooms": 3, "size_sqm": 75, "price_per_sqm": 7987}
    pool_conf = [{"id": f"t:s{i}", "city": "באר שבע", "street": "דרך מצדה",
                  "rooms": 3, "price_per_sqm": p}
                 for i, p in enumerate([10133, 10135, 13462], start=1)]
    sc = comps.street_check(subject, pool_conf, cfg)
    check("הפער נשמר מול הרחוב → אישור", sc["street_status"] == "confirm",
          f'{sc["street_gap_pct"]:.0f}% מול {sc["street_peers"]} מודעות')
    pool_veto = [dict(x, price_per_sqm=8100) for x in pool_conf]
    sv = comps.street_check(subject, pool_veto, cfg)
    check("הרחוב זול כמו הדירה → וטו", sv["street_status"] == "veto",
          sv["street_note"])
    sn = comps.street_check(subject, pool_conf[:1], cfg)
    check("מעט מדי שכנים → נאמר במפורש שאין אימות",
          sn["street_status"] == "no_data", sn["street_note"])
    r_veto = scoring.score_listing(
        {**subject, "price": 599000, "text": "", "listing_type": "private"},
        cfg, stage="final",
        comp={"sufficient": True, "can_crown": True, "gap_pct": 25.0,
              "suspect": False, "comp_median_ppm": 10113,
              "comp_match_level": "שכונה", "comp_area": "יא"},
        street=sv, first_seen=(today - timedelta(days=70)).isoformat(),
        today=today)
    check("וטו רחוב חוסם הכתרה", r_veto["tier"] != scoring.TIER_URGENT,
          r_veto["data_quality"])

    print("\n=== 1ג. חסימת הכתרה על סיכון בנכס ===")
    risky = {"id": "t:risk", "city": "דימונה", "price": 500000, "rooms": 3,
             "size_sqm": 75, "price_per_sqm": 500000 / 75,
             "text": "דירה 3 חדרים עם דייר מוגן", "listing_type": "private"}
    good_comp = {"sufficient": True, "can_crown": True, "gap_pct": 25.0,
                 "suspect": False, "comp_median_ppm": 8888.0,
                 "comp_match_level": "שכונה", "comp_area": "מרכז"}
    r_risk = scoring.score_listing(risky, cfg, stage="final", comp=good_comp,
                                   first_seen=(today - timedelta(days=70)).isoformat(),
                                   today=today, drop=None)
    check("דייר מוגן חוסם הכתרה", r_risk["tier"] != scoring.TIER_URGENT
          and r_risk["asset_risk"], r_risk["data_quality"])
    city_comp = dict(good_comp, can_crown=False,
                     crown_block_reason="חציון ברמת יישוב")
    r_city = scoring.score_listing(
        {**risky, "text": "דירה 3 חדרים"}, cfg, stage="final", comp=city_comp,
        first_seen=(today - timedelta(days=70)).isoformat(), today=today)
    check("השוואה שאסור לה להכתיר לא מגיעה ל'לבדוק דחוף'",
          r_city["tier"] != scoring.TIER_URGENT, r_city["tier_he"])

    print("\n=== 2. התמדה וזיהוי ירידת מחיר ===")
    conn = db.connect(cfg["paths"]["db"])

    first_seen = (today - timedelta(days=100)).isoformat()
    listing = {
        "id": "test:1", "source": "test", "city": "באר שבע",
        "url": "https://example.invalid/1", "rooms": 3, "size_sqm": 72,
        "price": 700000, "price_per_sqm": 700000 / 72,
        "text": "דירה דורשת שיפוץ, המחיר גמיש, להשקעה", "raw": {},
    }
    r1 = db.upsert_listing(conn, listing, first_seen)
    conn.commit()
    check("ריצה 1: המודעה זוהתה כחדשה", r1["is_new"] is True)

    changed_at = (today - timedelta(days=10)).isoformat()
    listing2 = dict(listing, price=616000, price_per_sqm=616000 / 72)
    r2 = db.upsert_listing(conn, listing2, changed_at)
    conn.commit()
    check("ריצה 2: זוהה מחיר קודם", r2["old_price"] == 700000.0)
    check("ריצה 2: first_seen נשמר", r2["first_seen"] == first_seen)

    drops = db.price_drop_history(conn)
    check("זוהתה ירידת מחיר אחת", len(drops) == 1)
    if drops:
        check("אחוז הירידה נכון (12%)", abs(drops[0]["drop_pct"] - 12.0) < 0.01,
              f'{drops[0]["drop_pct"]:.2f}%')
        check("הירידה לא סומנה כחשודה", not drops[0]["suspect"])

    # ירידה בלתי-סבירה — חייבת להיות מסומנת
    db.upsert_listing(conn, dict(listing, id="test:2", price=1000000), first_seen)
    db.upsert_listing(conn, dict(listing, id="test:2", price=90000), changed_at)
    conn.commit()
    bogus = [d for d in db.price_drop_history(conn) if d["listing_id"] == "test:2"]
    check("ירידה של 91% סומנה כשגיאת נתונים",
          bool(bogus) and bogus[0]["suspect"] is True,
          f'{bogus[0]["drop_pct"]:.1f}%' if bogus else "")

    print("\n=== 3. דדופ התראות ===")
    cands = [{"id": "test:1", "score": 80, "price": 616000},
             {"id": "test:2", "score": 75, "price": 500000}]
    send, supp, stats = alerts.decide(cands, db.alert_state(conn, ["test:1", "test:2"]))
    check("שתי המודעות חדשות ונשלחות", len(send) == 2 and stats["new"] == 2)
    db.record_alerts(conn, send, today.isoformat())
    conn.commit()
    send2, supp2, stats2 = alerts.decide(
        [dict(c) for c in cands], db.alert_state(conn, ["test:1", "test:2"]))
    check("בריצה שנייה שתיהן מדוכאות", len(send2) == 0 and stats2["repeat"] == 2,
          alerts.summary_he(stats2))
    up = [dict(cands[0], score=95)]
    send3, _s3, stats3 = alerts.decide(up, db.alert_state(conn, ["test:1"]))
    check("עליית ציון של 15 מחזירה להתראה",
          len(send3) == 1 and stats3["score_up"] == 1)
    down = [dict(cands[1], price=450000)]
    send4, _s4, stats4 = alerts.decide(down, db.alert_state(conn, ["test:2"]))
    check("ירידת מחיר מחזירה להתראה",
          len(send4) == 1 and stats4["price_drop"] == 1)

    print("\n=== 4. השוואה like-for-like מול עסקאות אמיתיות ===")
    fetcher = Fetcher(delay=1.0, timeout=cfg["request_timeout_seconds"])
    index = nadlan.fetch_settlement_index(fetcher)
    check("אינדקס היישובים נטען", bool(index), f"{len(index)} יישובים")
    setl = nadlan.resolve_city(index, "באר שבע") if index else None
    check("קוד יישוב לבאר שבע = 9000", bool(setl) and setl["code"] == 9000)

    baseline = nadlan.fetch_city_baseline(fetcher, "באר שבע", setl) if setl else None
    check("בסיס עסקאות נטען", bool(baseline))
    if not baseline:
        conn.close()
        return 1

    observed = {3: 78.0, 4: 95.0, 5: 120.0}
    c = comps.like_for_like(baseline, listing2, cfg, observed)
    check("נמצאו קומפים מספיקים", c["sufficient"],
          f'{c["comp_count"]} תצפיות, חציון {c["comp_median_ppm"]:,.0f} ₪/מ"ר')
    check("רמת ההשוואה דווחה", c["comp_match_level"] in ("שכונה", "יישוב"),
          c["comp_match_level"])
    check("הפער חושב", c["gap_pct"] is not None, f'{c["gap_pct"]:.1f}%')

    # דירה של 2 חדרים — אין קטגוריה תואמת ברשומות הרשמיות (3/4/5)
    c2 = comps.like_for_like(baseline, dict(listing2, rooms=2), cfg, observed)
    check("2 חדרים → אין מספיק קומפים", not c2["sufficient"], c2["data_quality"])

    # פער מוגזם → חשוד
    cheap = dict(listing2, price=350000, price_per_sqm=350000 / 72)
    c3 = comps.like_for_like(baseline, cheap, cfg, observed)
    check("פער מעל 30% מסומן כחשוד", c3["suspect"], c3["data_quality"])

    print("\n=== 5. תשואה, עליית ערך ודירוג ===")
    area = comps.area_series(baseline, cfg, observed)
    check("חושב CAGR", area["cagr_pct"] is not None,
          f'{area["cagr_pct"]:.2f}% לשנה, {area["cagr_from_year"]}–{area["cagr_to_year"]}')
    check("יש חציונים שנתיים", len(area["years"]) >= 3,
          f'{len(area["years"])} שנים')

    rent = nadlan.fetch_rent(fetcher, "settlement", 9000)
    check("נתוני שכירות נטענו", bool(rent), str((rent or {}).get("rooms")))
    yinfo = comps.rental_yield(listing2, rent, cfg, "settlement")
    check("חושבה תשואה ברוטו", yinfo["yield_pct"] is not None,
          f'{yinfo["yield_pct"]:.2f}% ({yinfo["rent_basis"]})')
    check("התשואה מסומנת כהערכה", yinfo["is_estimate"] is True)

    res = scoring.score_listing(
        listing2, cfg, baseline=baseline, drop=drops[0] if drops else None,
        first_seen=first_seen, today=today, stage="final",
        comp=c, yield_info=yinfo, area_info=area)
    b = res["breakdown"]
    print(f"       ציון {res['score']} · דירוג {res['tier_he']} · "
          f"סוג {res['opportunity_type_he']}")
    print(f"       פירוט: {b}")
    print(f"       הסבר: {res['reason']}")
    check("הציון בטווח 0..100", 0 <= res["score"] <= 100, str(res["score"]))
    check("סכום הפירוט = הציון", abs(sum(b.values()) - res["score"]) < 0.02)
    check("price_gap חושב מהקומפים", b["price_gap"] > 0, f'gap={res["gap_pct"]:.1f}%')
    check("יש אות תומך", res["has_signal"], str(res["signals"]))
    check("הוקצה דירוג", res["tier"] in (scoring.TIER_URGENT, scoring.TIER_WORTH,
                                         scoring.TIER_WATCH), str(res["tier"]))
    check("הוקצה סוג הזדמנות", bool(res["opportunity_type"]),
          res["opportunity_type"])
    check("דגל איכות נתונים קיים", bool(res["data_quality"]), res["data_quality"])

    # בלי קומפים מספיקים — אין ניקוד פער, ולא יכול להיות "לבדוק דחוף"
    res_nc = scoring.score_listing(
        listing2, cfg, baseline=baseline, first_seen=first_seen, today=today,
        stage="final", comp=c2, yield_info=yinfo, area_info=area)
    check("בלי קומפים — אין ניקוד פער", res_nc["breakdown"]["price_gap"] == 0)
    check("בלי קומפים — לא 'לבדוק דחוף'", res_nc["tier"] != scoring.TIER_URGENT,
          str(res_nc["tier"]))

    print("\n=== 5ב. סולם המחירים המצטבר ===")
    # שלוש מדרגות: 700,000 → 660,000 → 616,000
    db.upsert_listing(conn, dict(listing, id="test:3", price=700000),
                      first_seen)
    db.upsert_listing(conn, dict(listing, id="test:3", price=660000),
                      (today - timedelta(days=40)).isoformat())
    db.upsert_listing(conn, dict(listing, id="test:3", price=616000), changed_at)
    conn.commit()
    ladders = db.price_ladder(conn, sanity_limit=cfg.get("max_plausible_drop_pct"))
    lad = ladders.get("test:3") or {}
    check("סולם: מחיר מקורי נשמר", lad.get("original_price") == 700000.0,
          str(lad.get("original_price")))
    check("סולם: מחיר נוכחי נכון", lad.get("current_price") == 616000.0)
    check("סולם: ירידה מצטברת 12%",
          abs((lad.get("total_drop_pct") or 0) - 12.0) < 0.01,
          f'{lad.get("total_drop_pct", 0):.2f}%')
    check("סולם: נספרו 2 ירידות", lad.get("num_drops") == 2,
          str(lad.get("num_drops")))
    check("סולם: יש מחרוזת היסטוריה עם 3 מחירים",
          (lad.get("history_text") or "").count("→") == 2, lad.get("history_text"))
    bogus_lad = ladders.get("test:2") or {}
    check("סולם: ירידה חריגה מסומנת כחשודה", bogus_lad.get("suspect") is True)

    print("\n=== 5ג. סולם ההשוואה המדורג ===")
    market = comps.MarketIndex(cfg)
    market.add_city("באר שבע", baseline, observed)
    # יישוב נוסף אמיתי כדי שתהיה מדרגת "יישובים דומים" ומרחב
    setl2 = nadlan.resolve_city(index, "אשקלון")
    base2 = nadlan.fetch_city_baseline(fetcher, "אשקלון", setl2) if setl2 else None
    if base2:
        market.add_city("אשקלון", base2, observed)
    market.build()

    v = comps.cascade(listing2, c, market, cfg)
    check("סולם: נבחרה רמת השוואה", v["comp_level"] is not None,
          f'{v["comp_level_he"]} ({v["value_count"]} תצפיות)')
    # ביטחון גבוה נשמר לרמת שכונה בלבד. חציון עירוני בבאר שבע (יותר
    # מ-200 אלף תושבים, 23 שכונות) מערבב שכונות שאין ביניהן קשר, ולכן
    # הוא הקשר — לא ראיה. הבדיקה מאמתת שהמנוע לא מנפח ביטחון.
    if v["comp_level"] == comps.LEVEL_NEIGHBORHOOD:
        check("סולם: רמת שכונה → ביטחון גבוה",
              v["confidence"] == comps.CONF_HIGH,
              f'{v["confidence_he"]} — {v.get("confidence_reason")}')
    else:
        check("סולם: חציון עירוני ביישוב גדול אינו ביטחון גבוה",
              v["confidence"] != comps.CONF_HIGH,
              f'{v["comp_level_he"]} → {v["confidence_he"]} — {v.get("confidence_reason")}')
    check("סולם: רמה לא-מקומית אינה מכתירה",
          all(not r.get("can_crown") for r in v["ladder"]
              if r["level"] in (comps.LEVEL_TIER, comps.LEVEL_REGION)),
          "מדרגה/מרחב חסומים להכתרה")
    check("סולם: יש 4 שלבים בסולם", len(v["ladder"]) >= 3,
          str([f'{r["level_he"]}:{r["count"]}' for r in v["ladder"]]))
    check("סולם: חושב פער", v["value_gap_pct"] is not None,
          f'{v["value_gap_pct"]:.1f}%')

    # דירה של 2 חדרים — אין לה קטגוריה רשמית, ובכל זאת אסור שתיעלם
    small = dict(listing2, rooms=2, price=300000, price_per_sqm=300000 / 72)
    v2 = comps.cascade(small, c2, market, cfg)
    check("מודעה בלי קומפים מקומיים אינה נפסלת",
          v2.get("value_tag") is not None or v2["comp_level"] is not None,
          f'רמה={v2["comp_level_he"]}, ביטחון={v2["confidence_he"]}, '
          f'תגית={v2.get("value_tag")}')

    # פער מוגזם — עדיין חשוד, גם בסולם
    v3 = comps.cascade(cheap, c3, market, cfg)
    check("סולם: פער מעל 30% מסומן כחשוד", v3["suspect"] is True,
          v3.get("value_tag"))

    print("\n=== 6. פלט: אקסל, JSON, דשבורד וגרפים ===")
    scored = [{**listing2, **res, "comp": c, "yield_info": yinfo,
               "area_info": area, "value": v,
               "nadlan_link": nadlan.search_link("באר שבע", c.get("comp_area"))}]

    print("      מדורים:")
    secs = sections.build(scored, ladders, areas_placeholder := {
        baseline["area_key"]: area}, cfg)
    check("מדור מכ\"ם ירידות אינו ריק", len(secs["price_drop_radar"]) >= 1,
          f'{len(secs["price_drop_radar"])} מודעות')
    check("מדור ערך יחסי אינו ריק", len(secs["best_relative_value"]) >= 1)
    check("מדור אזורים מתחממים אינו ריק", len(secs["hot_areas"]) >= 1)
    sharp = [r for r in secs["price_drop_radar"] if r.get("sharp")]
    check("ירידה של 12% סומנה כירידה חדה", len(sharp) >= 1,
          f'{len(sharp)} חדות מתוך {len(secs["price_drop_radar"])}')
    check("ירידה חשודה נדחקת לסוף הרשימה",
          not secs["price_drop_radar"][0].get("suspect"))
    city_rows = [{
        "city": "באר שבע", "setl_code": 9000,
        "median_ppsqm": c["comp_median_ppm"],
        "avg_price_12m": baseline["all_rooms_avg_price"],
        "price_change_pct": baseline["price_change_pct"],
        "cagr_pct": area["cagr_pct"], "years_covered": area["years_covered"],
        "deals_12m_display": "4/4 רבעונים עם עסקאות", "active_listings": 1,
        "scanned_now": 1, "population": baseline["population"],
        "data_version": baseline["data_version"],
        "nadlan_link": nadlan.search_link("באר שבע"),
    }]
    areas = {baseline["area_key"]: area}
    meta = {"run_date": today.isoformat(), "tier_counts": {},
            "drop_assertion": a, "dedup_stats": stats}

    meta["section_counts"] = sections.counts(secs)
    meta["summary_lines"] = [("מודעות", len(scored))]
    meta["credits_used"] = 0

    made = charts.build_all(scored, areas, cfg["paths"]["out"] / "charts",
                            ladder_rows=secs["price_drop_radar"])
    check("נוצר גרף מגמת אזור", made["area"] >= 1, str(made))
    check("נוצר גרף התפלגות קומפים", made["comps"] >= 1)
    check("נוצר גרף סולם מחירים", made["ladder"] >= 1, str(made["ladder"]))

    dated, latest = excel.build_workbook(
        scored, drops, city_rows, meta, cfg["paths"]["out"], threshold=0,
        areas=areas, sections=secs)
    check("קובץ אקסל נוצר", dated.is_file() and dated.stat().st_size > 4000,
          f"{dated.stat().st_size} bytes")

    from openpyxl import load_workbook
    wb = load_workbook(dated)
    check("6 גיליונות", len(wb.sheetnames) == 6, str(wb.sheetnames))
    ws_radar = wb['מכ"ם ירידות מחיר']
    hdr_r = [ws_radar.cell(row=3, column=i).value
             for i in range(1, ws_radar.max_column + 1)]
    for col in ("מחיר מקורי", "מחיר נוכחי", "ירידה מצטברת %", "מס' ירידות",
                "מסלול המחיר", "ירידה חדה"):
        check(f"עמודת מכ\"ם '{col}' קיימת", col in hdr_r)
    check("שורת מכ\"ם מכילה מסלול מחיר",
          "→" in str(ws_radar.cell(row=4, column=hdr_r.index("מסלול המחיר") + 1).value))
    ws_val = wb["ערך יחסי"]
    hdr_v = [ws_val.cell(row=3, column=i).value
             for i in range(1, ws_val.max_column + 1)]
    for col in ("פער מתחת לחציון %", "רמת ההשוואה", "רמת ביטחון"):
        check(f"עמודת ערך יחסי '{col}' קיימת", col in hdr_v)
    ws = wb["כל המודעות"]
    hdr = [ws.cell(row=3, column=i).value for i in range(1, ws.max_column + 1)]
    for col in ("חציון ₪/מ\"ר בהשוואה", "מס' קומפים", "רמת ההשוואה", "פער %",
                "ירידת מחיר %", "ימים באוויר", "תשואה ברוטו %",
                "עליית ערך שנתית %", "דירוג", "סוג ההזדמנות", "הסבר",
                "איכות הנתונים"):
        check(f"עמודה '{col}' קיימת", col in hdr)
    link_cell = ws.cell(row=4, column=hdr.index("קישור למודעה") + 1)
    check("קישור למודעה הוא היפר-קישור אמיתי",
          link_cell.hyperlink is not None, str(link_cell.hyperlink and
                                               link_cell.hyperlink.target))
    nad_cell = ws.cell(row=4, column=hdr.index('נדל"ן ממשלתי') + 1)
    check("קישור לנדל\"ן ממשלתי הוא היפר-קישור אמיתי",
          nad_cell.hyperlink is not None,
          str(nad_cell.hyperlink and nad_cell.hyperlink.target))
    check("גיליון מגמת אזורים מכיל נתונים",
          wb["מגמת אזורים"].cell(row=4, column=1).value is not None)

    jpath = jsonout.write(scored, areas, drops, city_rows, meta,
                          cfg["paths"]["out"], sections_data=secs,
                          ladders=ladders)
    check("latest.json נוצר", jpath.is_file())
    import json
    doc = json.loads(jpath.read_text(encoding="utf-8"))
    check("ל-JSON יש בלוק areas", bool(doc.get("areas")),
          f'{len(doc.get("areas", []))} אזורים')
    check("לאזור יש חציונים שנתיים ו-CAGR",
          bool(doc["areas"][0].get("yearly")) and
          doc["areas"][0].get("cagr_pct") is not None)
    rec = doc["listings"][0]
    for f in ("comp_median_ppm", "comp_count", "comp_match_level", "gap_pct",
              "drop_pct", "days_on_market", "yield_pct", "area_cagr_pct",
              "tier", "opportunity_type", "reason", "data_quality",
              "original_price", "current_price", "total_drop_pct", "num_drops",
              "price_history_text", "comp_level", "confidence",
              "value_gap_pct"):
        check(f"ל-JSON יש שדה {f}", f in rec and rec[f] is not None)
    check("ל-JSON יש היסטוריית מחירים מלאה",
          len(rec.get("price_history") or []) >= 2,
          f'{len(rec.get("price_history") or [])} נקודות')
    check("ל-JSON יש סולם השוואה מלא",
          len(rec.get("comp_ladder") or []) >= 3)
    for key in ("price_drop_radar", "best_relative_value", "hot_areas"):
        check(f"ל-JSON יש מדור {key}", bool((doc.get("sections") or {}).get(key)))

    dpath = dashboard.write(secs, meta, cfg, cfg["paths"]["out"], areas=areas,
                            listings_n=len(scored))
    check("dashboard.html נוצר", dpath.is_file() and dpath.stat().st_size > 3000,
          f"{dpath.stat().st_size} bytes")
    dhtml = dpath.read_text(encoding="utf-8")
    check("הדשבורד עברי ו-RTL", 'dir="rtl"' in dhtml and 'lang="he"' in dhtml)
    # פונט רשת (Google Fonts) מותר כשיפור פרוגרסיבי — נטען אונליין, נופל
    # לפונט מערכת אופליין. אסורים: תמונות, וכל CSS/JS מקומי או חיצוני אחר
    # (הם שוברים את הקובץ כשמעבירים אותו).
    import re as _re
    _refs = (_re.findall(r'<link[^>]+href="([^"]+)"', dhtml)
             + _re.findall(r'<script[^>]+src="([^"]+)"', dhtml))
    _allowed = ("https://fonts.googleapis.com", "https://fonts.gstatic.com")
    _bad = [u for u in _refs if not any(u.startswith(a) for a in _allowed)]
    check("הדשבורד בלי משאבים שבירים (רק פונט רשת חיצוני מותר)",
          not _bad and "<img" not in dhtml,
          f"חיצוניים לא-מאושרים: {_bad}" if _bad else "רק פונטים")
    for title in ('מכ"ם ירידות מחיר', "הערך היחסי הטוב ביותר",
                  "מגמת אזורים", "לוח עליית הערך"):
        check(f"הדשבורד מכיל '{title}'", title in dhtml)
    check("הדשבורד מכיל גרף SVG", "<svg" in dhtml and "</svg>" in dhtml)

    conn.close()
    print(f"\nקבצי הבדיקה: {tmp}")
    if FAILURES:
        print(f"\n*** {len(FAILURES)} בדיקות נכשלו: {FAILURES}")
        return 1
    print("\n*** כל הבדיקות עברו ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
