"""
מודל הניקוד — "ציון אטרקטיביות" 0..100, דטרמיניסטי לחלוטין.
כל המשקלים והפרמטרים מגיעים מ-config.yaml. אין קריאות LLM ואין רנדומליות.

  price_gap             (0..50) — כמה מתחת לחציון ₪/מ"ר של הבסיס להשוואה
  price_drop            (0..20) — ירידת מחיר במודעה (עוצמה + עד כמה טרייה)
  days_on_market        (0..10) — ותק המודעה לפי first_seen
  opportunity_keywords  (0..10) — מילות הזדמנות בטקסט/בתגיות/במצב הנכס
  area_liquidity        (0..10) — נזילות/ביקוש באזור

שני שלבים (זרימת listings-first):
  שלב א' — סינון מקדים: הבסיס להשוואה הוא **חציון מחירי המבוקש של יד2**
           באותה עיר+חדרים. חינם, בלי אף בקשה נוספת.
  שלב ב' — סופי, רק למועמדות שעברו: הבסיס הוא **עסקאות שנסגרו בפועל**
           באזור הספציפי (שכונה, ואם אין — יישוב) לפי נדל"ן ממשלתי.

חשוב: price_gap שווה 50 מתוך 100, ולכן אי אפשר לעבור סף 70 בלי בסיס השוואה
כלשהו. זו הסיבה שהסינון המקדים משתמש בחציון המבוקש כפרוקסי — ראה
prescreen_gate() ואת ההסבר ב-CLAUDE.md.
"""
import logging
import statistics
from datetime import date, datetime

from .pricing import is_drop_sane

log = logging.getLogger(__name__)

# ---- דירוג (מחליף את סף ההתראה הבודד) ----
TIER_URGENT = "check-urgent"
TIER_WORTH = "worth-checking"
TIER_WATCH = "watch"
TIER_NONE = None

TIER_HE = {
    TIER_URGENT: "לבדוק דחוף",
    TIER_WORTH: "שווה בדיקה",
    TIER_WATCH: "למעקב",
    TIER_NONE: "—",
}
TIER_ORDER = {TIER_URGENT: 3, TIER_WORTH: 2, TIER_WATCH: 1, TIER_NONE: 0}

# ---- סוג ההזדמנות ----
TYPE_YIELD = "yield"
TYPE_APPRECIATION = "appreciation"
TYPE_COMBINED = "combined"
TYPE_PRICE_FIND = "price-find"

TYPE_HE = {
    TYPE_YIELD: "תשואה",
    TYPE_APPRECIATION: "עליית ערך",
    TYPE_COMBINED: "משולב",
    TYPE_PRICE_FIND: "מציאת מחיר",
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------------
# רכיבי הניקוד
# ------------------------------------------------------------------

def score_price_gap(listing_ppsqm, benchmark_ppsqm, weight, full_at_pct):
    """
    לינארי: 0% מתחת לבסיס → 0 ; full_at_pct% ומעלה → ניקוד מלא.
    (ברירת מחדל 25% → 50 נק', כך ש-15% נותן ~30 נק').
    מחזיר (score, gap_pct) כאשר gap_pct חיובי = מתחת לבסיס = טוב.
    """
    if not listing_ppsqm or not benchmark_ppsqm or benchmark_ppsqm <= 0:
        return 0.0, None
    gap_pct = (benchmark_ppsqm - listing_ppsqm) / benchmark_ppsqm * 100.0
    if gap_pct <= 0:
        return 0.0, gap_pct
    return round(_clamp(gap_pct / float(full_at_pct), 0.0, 1.0) * weight, 2), gap_pct


def score_price_drop(drop, weight, today=None, sanity_limit=None):
    """
    ירידה ≥10% ובתוך 30 יום → ניקוד מלא. קטן/ישן יותר → מדורג.

    ירידה שסומנה כשגיאת נתונים (‎suspect‎) מקבלת 0 נקודות ואינה מוחזרת
    כאחוז — אחרת "ירידה" של 90% שנובעת מספרה שנפלה הייתה מזנקת לראש הרשימה.
    """
    if not drop:
        return 0.0, None
    pct = drop.get("drop_pct") or 0.0
    sane = is_drop_sane(pct, sanity_limit) if sanity_limit else is_drop_sane(pct)
    if drop.get("suspect") or not sane:
        return 0.0, pct          # מוחזר לתצוגה, אבל בלי ניקוד ובלי התראה
    if pct <= 0:
        return 0.0, None
    today = today or date.today()
    try:
        changed = datetime.fromisoformat(drop["changed_at"]).date()
        age_days = (today - changed).days
    except (KeyError, TypeError, ValueError):
        age_days = 999

    magnitude = _clamp(pct / 10.0, 0.0, 1.0)
    freshness = 1.0 if age_days <= 30 else _clamp(1.0 - (age_days - 30) / 60.0, 0.0, 1.0)
    return round(magnitude * freshness * weight, 2), pct


def score_days_on_market(first_seen, weight, full_at_days, today=None):
    """מעל full_at_days (90 כברירת מחדל) → ניקוד מלא; מדורג לינארית מתחתיו."""
    if not first_seen:
        return 0.0, 0
    today = today or date.today()
    try:
        seen = datetime.fromisoformat(str(first_seen)).date()
    except (TypeError, ValueError):
        return 0.0, 0
    days = max((today - seen).days, 0)
    return round(_clamp(days / float(full_at_days), 0.0, 1.0) * weight, 2), days


def score_keywords(listing, keywords, weight):
    """
    מילות הזדמנות → יותר מרווח למו"מ. 3 מילים ומעלה = ניקוד מלא.
    מקורות: התגיות של יד2 ("בהזדמנות", "גמיש במחיר"), התיאור החופשי
    (זמין רק אחרי העשרה), ומצב הנכס הרשמי ("דרוש שיפוץ").
    """
    text = listing.get("text") or ""
    found = [k for k in (keywords or []) if k and k in text]

    # ניקוי חפיפות: "הזדמנות" מוכלת ב"בהזדמנות", "גמיש" ב"גמיש במחיר" —
    # בלי זה תגית אחת של יד2 הייתה מזכה בניקוד כפול. שומרים את הארוכה.
    found = [k for k in found if not any(k != o and k in o for o in found)]

    # מצב נכס שדורש שיפוץ הוא אינדיקציית הזדמנות בפני עצמו
    cond = (listing.get("condition_text") or "")
    if "שיפוץ" in cond and not any(cond in f or f in cond for f in found):
        found.append(cond)

    if not found:
        return 0.0, []
    return round(_clamp(len(found) / 3.0, 0.0, 1.0) * weight, 2), found


def score_area_liquidity(baseline, weight, active_listings=0):
    """
    נזילות אזור. מספר העסקאות שנסגרו אינו נגיש ציבורית (api.nadlan.gov.il
    חוסם — ראה scout/nadlan.py), ולכן פרוקסי משני רכיבים רשמיים:
      * אוכלוסייה — שוק גדול = נזילות יציאה טובה יותר (50k+ → מלא).
      * צפיפות הנתונים הרשמיים — כמה רבעונים ב-12ח' יש בהם די עסקאות
        לפרסום מחיר (4/4 = שוק פעיל).
    כשאין עדיין בסיס רשמי (שלב הסינון המקדים) — נופלים על מספר המודעות
    הפעילות בעיר כפרוקסי גס לעומק השוק.
    """
    if not baseline:
        return round(_clamp(active_listings / 40.0, 0.0, 1.0) * weight * 0.7, 2)

    try:
        pop = float(baseline.get("population") or 0)
    except (TypeError, ValueError):
        pop = 0.0
    pop_factor = _clamp(pop / 50000.0, 0.0, 1.0)
    data_factor = _clamp((baseline.get("quarters_with_data") or 0) / 4.0, 0.0, 1.0)
    return round((0.65 * pop_factor + 0.35 * data_factor) * weight, 2)


def score_rental_yield(yield_pct, weight, floor_pct, full_at_pct):
    """
    תשואת שכירות ברוטו. מתחת ל-floor (ברירת מחדל 2%) → 0 ;
    ב-full_at (ברירת מחדל 5%) ומעלה → ניקוד מלא. לינארי בין לבין.
    """
    if yield_pct is None:
        return 0.0
    try:
        y = float(yield_pct)
    except (TypeError, ValueError):
        return 0.0
    span = float(full_at_pct) - float(floor_pct)
    if span <= 0:
        return 0.0
    return round(_clamp((y - float(floor_pct)) / span, 0.0, 1.0) * weight, 2)


def score_appreciation(cagr, weight, full_at_pct):
    """
    עליית ערך אזורית (CAGR רב-שנתי). 0% ומטה → 0 ; full_at (ברירת מחדל 6%)
    ומעלה → ניקוד מלא. אזור שנשחק במחיר לא מקבל נקודות שליליות — פשוט אפס.
    """
    if cagr is None:
        return 0.0
    try:
        c = float(cagr)
    except (TypeError, ValueError):
        return 0.0
    if c <= 0 or float(full_at_pct) <= 0:
        return 0.0
    return round(_clamp(c / float(full_at_pct), 0.0, 1.0) * weight, 2)


# ------------------------------------------------------------------
# בסיס ההשוואה של שלב א' — חציון המבוקש של יד2
# ------------------------------------------------------------------

def observed_median_sizes(listings):
    """{חדרים: גודל חציוני} — לחישוב ₪/מ"ר רשמי מדויק יותר."""
    buckets = {}
    for l in listings:
        r, s = l.get("rooms"), l.get("size_sqm")
        if not r or not s or s <= 0:
            continue
        try:
            buckets.setdefault(int(round(float(r))), []).append(float(s))
        except (TypeError, ValueError):
            continue
    return {k: statistics.median(v) for k, v in buckets.items() if v}


def peer_benchmarks(listings, min_sample=4):
    """
    חציון ₪/מ"ר של המודעות עצמן, לכל עיר: לפי מספר חדרים ולעיר כולה.
    זהו בסיס ההשוואה של שלב הסינון המקדים — מחירי *מבוקש*, לא עסקאות,
    ולכן הוא פרוקסי בלבד; הפער הסופי נקבע מול עסקאות אמיתיות.
    מחזיר {city: {"rooms": {n: median}, "all": median, "count": n}}
    """
    by_city = {}
    for l in listings:
        ppsqm = l.get("price_per_sqm")
        if not ppsqm or ppsqm <= 0:
            continue
        c = by_city.setdefault(l.get("city"), {"rooms": {}, "all": []})
        c["all"].append(ppsqm)
        r = l.get("rooms")
        if r:
            try:
                c["rooms"].setdefault(int(round(float(r))), []).append(ppsqm)
            except (TypeError, ValueError):
                pass

    out = {}
    for city, data in by_city.items():
        rooms_med = {k: statistics.median(v) for k, v in data["rooms"].items()
                     if len(v) >= min_sample}
        out[city] = {
            "rooms": rooms_med,
            "all": statistics.median(data["all"]) if data["all"] else None,
            "count": len(data["all"]),
        }
    return out


def peer_ppsqm(benchmark, rooms):
    """חציון עמיתים למספר חדרים נתון, עם נפילה חזרה לחציון העיר."""
    if not benchmark:
        return None, None
    if rooms is not None:
        try:
            key = int(round(float(rooms)))
        except (TypeError, ValueError):
            key = None
        if key is not None and key in (benchmark.get("rooms") or {}):
            return benchmark["rooms"][key], f"{key} חדרים בעיר"
    return benchmark.get("all"), "כלל העיר"


# ------------------------------------------------------------------
# ניקוד מלא — משמש בשני השלבים, נבדל רק בבסיס ההשוואה
# ------------------------------------------------------------------

def supporting_signal(drop_pct, dom_days, liq_score, cfg, liq_weight):
    """
    "אות תומך" — ראיה עצמאית לכך שהפער אמיתי ולא ארטיפקט של הנתונים.

    פער מחיר לבדו לא מספיק להכתיר מודעה: הוא יכול לנבוע מקומה, מצב, כיוון
    או פשוט מרעש בנתון הרשמי. דורשים לפחות אחד מאלה — ירידת מחיר בפועל,
    ותק ארוך באוויר (המוכר לא מצליח למכור), או אזור נזיל.
    מחזיר (bool, [שמות האותות בעברית]).

    ירידה בלתי-סבירה **אינה** אות תומך. בלי הבדיקה הזאת "ירידה" של 370%
    שנובעת מספרה שנפלה הייתה מספקת את דרישת האות התומך ומקדמת מודעה
    שגויה ל"לבדוק דחוף" — בדיוק מה שחסם השפיות נועד למנוע.
    """
    signals = []
    sane_drop = is_drop_sane(drop_pct, cfg.get("max_plausible_drop_pct") or 60)
    if (drop_pct and sane_drop
            and drop_pct >= float(cfg.get("signal_min_drop_pct", 3))):
        signals.append(f"ירידת מחיר {drop_pct:.1f}%")
    if dom_days and dom_days >= int(cfg.get("signal_min_days_on_market", 60)):
        signals.append(f"{dom_days} יום באוויר")
    if liq_weight and liq_score >= float(cfg.get("signal_liquidity_frac", 0.7)) * liq_weight:
        signals.append("אזור נזיל")
    return bool(signals), signals


def classify(gap_pct, yield_pct, cagr, cfg):
    """סוג ההזדמנות: תשואה / עליית ערך / משולב / מציאת מחיר."""
    good_yield = yield_pct is not None and yield_pct >= float(
        cfg.get("yield_good_pct", 3.5))
    good_cagr = cagr is not None and cagr >= float(cfg.get("cagr_good_pct", 3.0))
    if good_yield and good_cagr:
        return TYPE_COMBINED
    if good_yield:
        return TYPE_YIELD
    if good_cagr:
        return TYPE_APPRECIATION
    return TYPE_PRICE_FIND


def assign_tier(score, comp, has_signal, cfg, manual_check=False):
    """
    הדירוג שמחליף את סף ההתראה הבודד.

    הכללים, לפי סדר קדימות:
      * בלי קומפים מספיקים — אין ראיית מחיר, ולכן לכל היותר "למעקב".
      * פער חשוד (>30%) — כמעט תמיד שגיאת נתונים או נכס לא-השוואתי.
        לעולם לא בכותרת; לכל היותר "שווה בדיקה".
      * ‎manual_check‎ (למשל ירידת מחיר חריגה) — גם כן לא בכותרת.
      * "לבדוק דחוף" דורש **גם** פער אמיתי (8–30%) **וגם** אות תומך.
    """
    tiers = cfg.get("tiers") or {}
    t_urgent = float(tiers.get("check_urgent", 78))
    t_worth = float(tiers.get("worth_checking", 66))
    t_watch = float(tiers.get("watch", 55))
    gap_min = float(cfg.get("comps_gap_min_pct", 8))

    comp = comp or {}
    gap = comp.get("gap_pct")
    sufficient = bool(comp.get("sufficient"))
    suspect = bool(comp.get("suspect"))

    if not sufficient:
        return TIER_WATCH if score >= t_watch else TIER_NONE

    real_gap = gap is not None and gap >= gap_min and not suspect

    if real_gap and has_signal and score >= t_urgent and not manual_check:
        return TIER_URGENT
    if (real_gap or suspect) and score >= t_worth:
        return TIER_WORTH
    if score >= t_watch:
        return TIER_WATCH
    return TIER_NONE


def plain_reason(city, area, comp, drop_pct, dom_days, yield_info, area_info,
                 signals, tier, opp_type):
    """
    שורת הסבר בעברית פשוטה — למה השורה הזאת כאן, בלי ז'רגון.
    זו העמודה שמישהו קורא לפני שהוא מרים טלפון.
    """
    comp = comp or {}
    bits = []

    gap = comp.get("gap_pct")
    if comp.get("sufficient") and gap is not None:
        level = comp.get("comp_match_level") or "אזור"
        n = comp.get("comp_count")
        if gap >= 0:
            bits.append(f"מבוקש {gap:.0f}% מתחת לחציון העסקאות ב{level} "
                        f"{area or city} ({n} תצפיות)")
        else:
            bits.append(f"מבוקש {abs(gap):.0f}% מעל חציון העסקאות ב{level} "
                        f"{area or city}")
    else:
        bits.append(comp.get("data_quality") or "אין מספיק עסקאות להשוואה")

    if signals:
        bits.append("אות תומך: " + ", ".join(signals[:3]))
    elif comp.get("sufficient"):
        bits.append("אין אות תומך (בלי ירידת מחיר / ותק / נזילות)")

    y = (yield_info or {}).get("yield_pct")
    if y is not None:
        bits.append(f"תשואה ברוטו מוערכת {y:.1f}%")

    c = (area_info or {}).get("cagr_pct")
    if c is not None:
        span = (area_info or {}).get("years_covered") or 0
        bits.append(f"עליית ערך באזור {c:+.1f}% לשנה ({span} שנים)")

    if comp.get("suspect"):
        bits.append("הפער גדול מדי מכדי להיות אמיתי — לבדוק ידנית")

    bits.append(f"דירוג: {TIER_HE.get(tier, '—')} / {TYPE_HE.get(opp_type, '—')}")
    return " | ".join(bits)


def score_listing(listing, cfg, benchmark_ppsqm=None, benchmark_label="",
                  baseline=None, active_listings=0, drop=None, first_seen=None,
                  today=None, stage="prescreen", comp=None, yield_info=None,
                  area_info=None):
    """
    מחשב ציון 0..100 למודעה אחת ומחזיר dict עם הציון, הדירוג והפירוט.

    בשלב הסינון המקדים (stage='prescreen') מגיע ‎benchmark_ppsqm‎ מחציון
    המבוקש של יד2 ואין ‎comp‎. בשלב הסופי מגיע ‎comp‎ מ-comps.like_for_like
    והוא **הקובע** — פער בלי קומפים מספיקים לא מזכה בנקודות מחיר כלל.
    """
    w = cfg["weights"]
    today = today or date.today()
    sanity = cfg.get("max_plausible_drop_pct")

    # ── פער מחיר ──
    if stage == "final":
        comp = comp or {}
        if comp.get("sufficient"):
            base_ppsqm = comp.get("comp_median_ppm")
            label = (f"חציון עסקאות ב{comp.get('comp_match_level')} "
                     f"{comp.get('comp_area') or ''}".strip())
            gap_score, gap_pct = score_price_gap(
                listing.get("price_per_sqm"), base_ppsqm,
                w["price_gap"], cfg["price_gap_full_at_pct"])
            # פער חשוד לא מזכה בניקוד מלא — הוא כנראה לא אמיתי
            if comp.get("suspect"):
                gap_score = round(gap_score * float(
                    cfg.get("suspect_gap_score_factor", 0.5)), 2)
        else:
            base_ppsqm, label = comp.get("comp_median_ppm"), "אין מספיק עסקאות להשוואה"
            gap_score, gap_pct = 0.0, comp.get("gap_pct")
    else:
        base_ppsqm, label = benchmark_ppsqm, benchmark_label
        gap_score, gap_pct = score_price_gap(
            listing.get("price_per_sqm"), benchmark_ppsqm,
            w["price_gap"], cfg["price_gap_full_at_pct"])

    drop_score, drop_pct = score_price_drop(drop, w["price_drop"], today, sanity)
    dom_score, dom_days = score_days_on_market(
        first_seen, w["days_on_market"], cfg["days_on_market_full_at"], today)
    kw_score, kw_found = score_keywords(
        listing, cfg.get("keywords") or [], w["opportunity_keywords"])
    liq_score = score_area_liquidity(baseline, w["area_liquidity"], active_listings)

    y_pct = (yield_info or {}).get("yield_pct")
    cagr = (area_info or {}).get("cagr_pct")
    yield_score = score_rental_yield(
        y_pct, w.get("rental_yield", 0),
        cfg.get("yield_floor_pct", 2.0), cfg.get("yield_full_at_pct", 5.0))
    apprec_score = score_appreciation(
        cagr, w.get("area_appreciation", 0), cfg.get("cagr_full_at_pct", 6.0))

    total = round(gap_score + drop_score + dom_score + kw_score + liq_score
                  + yield_score + apprec_score, 2)

    has_signal, signals = supporting_signal(
        drop_pct, dom_days, liq_score, cfg, w["area_liquidity"])

    # ── דגל איכות נתונים — נקבע לפני הדירוג, כי הוא חוסם הכתרה ──
    bad_drop = drop_pct is not None and not is_drop_sane(
        drop_pct, sanity or 60)
    if stage == "final":
        quality = (comp or {}).get("data_quality") or "אין נתוני השוואה"
    else:
        quality = "סינון מקדים — טרם הושווה לעסקאות"
    if bad_drop:
        quality = f"ירידת מחיר חריגה ({drop_pct:.0f}%) — בדיקה ידנית"

    tier = assign_tier(total, comp if stage == "final" else None, has_signal,
                       cfg, manual_check=bad_drop)
    opp_type = classify(gap_pct, y_pct, cagr, cfg)

    # ── שומר תצוגה לפער שלילי חריג ──
    # פער שלילי עמוק ("‎-103%‎") נובע כמעט תמיד מהשוואת ₪/מ"ר של דירה קטנה
    # מול חציון רב-גודל, ולא ממחיר גבוה אמיתי — הנתון הרשמי מפרסם רק
    # קטגוריות 3/4/5 חדרים, כך שדירת 2 חדרים מושווית כלפי מעלה. הוא מעוות
    # את התצוגה ("מאות אחוזים") ואינו אינפורמטיבי; הניקוד ממילא 0 בפער
    # שלילי, לכן מנטרלים אותו לתצוגה ומסמנים את הסיבה.
    gap_floor = float(cfg.get("gap_display_floor_pct", -60.0))
    if gap_pct is not None and gap_pct < gap_floor:
        if quality in ("תקין", "סינון מקדים — טרם הושווה לעסקאות",
                       "אין נתוני השוואה"):
            quality = "מחיר מעל החציון — ייתכן הבדל גודל/קטגוריה"
        gap_pct = None

    # ── פילטר מניפולציות מפרסם ──
    # (1) מחיר לא-סביר: פער עצום מהשוק = כמעט תמיד מחיר-פיתיון או "זכויות/על
    #     הנייר", לא דירה אמיתית במחיר הזה. אדם שמבין מסנן מיד — כך גם המנוע.
    manip_gap = float(cfg.get("manipulation_gap_pct", 48.0))
    suspicious_price = bool(gap_pct is not None and gap_pct > manip_gap)
    # (2) מילות פסילה: זכויות/קבוצת רכישה/מגרש/מסחרי/ללא טאבו וכו' —
    #     אינדיקציה שזו לא דירת יד-שנייה סטנדרטית.
    blob = " ".join(str(x) for x in (listing.get("text"),
                    listing.get("description"), listing.get("condition_text")) if x)
    bad_kw = [k for k in (cfg.get("disqualify_keywords") or []) if k and k in blob]
    disqualified = suspicious_price or bool(bad_kw)
    if disqualified:
        tier = TIER_NONE          # לעולם לא הזדמנות
        if suspicious_price and quality in ("תקין", "סינון מקדים — טרם הושווה לעסקאות"):
            quality = "מחיר חשוד — נמוך בצורה לא-סבירה מהשוק (מחיר-פיתיון/זכויות?)"
        elif bad_kw:
            quality = "לא דירת יד-שנייה סטנדרטית (%s)" % ", ".join(bad_kw[:2])

    area_name = (comp or {}).get("comp_area") or (baseline or {}).get("area_name")
    reason = plain_reason(listing.get("city"), area_name, comp if stage == "final" else None,
                          drop_pct, dom_days, yield_info, area_info, signals,
                          tier, opp_type)

    return {
        "score": total,
        "stage": stage,
        "tier": tier,
        "tier_he": TIER_HE.get(tier, "—"),
        "opportunity_type": opp_type,
        "opportunity_type_he": TYPE_HE.get(opp_type, "—"),
        "gap_pct": gap_pct,
        "benchmark_ppsqm": base_ppsqm,
        "benchmark_label": label,
        "has_signal": has_signal,
        "signals": signals,
        "data_quality": quality,
        "breakdown": {
            "price_gap": gap_score,
            "price_drop": drop_score,
            "days_on_market": dom_score,
            "opportunity_keywords": kw_score,
            "area_liquidity": liq_score,
            "rental_yield": yield_score,
            "area_appreciation": apprec_score,
        },
        "days_on_market": dom_days,
        "drop_pct": drop_pct,
        "disqualified": disqualified,
        "suspicious_price": suspicious_price,
        "disqualify_keywords": bad_kw,
        "keywords_found": kw_found,
        "yield_pct": y_pct,
        "monthly_rent_est": (yield_info or {}).get("monthly_rent"),
        "rent_basis": (yield_info or {}).get("rent_basis"),
        "area_cagr_pct": cagr,
        "reason": reason,
    }


def prescreen_reachable_max(cfg):
    """
    הציון המקסימלי שמודעה יכולה לקבל **בשלב הסינון המקדים**.

    תשואה ועליית ערך נמדדות רק מול הנתונים הרשמיים של האזור, שנשלפים רק
    בשלב הסופי — ולכן המשקלים שלהן אינם ברי-השגה בשלב א'.
    """
    w = cfg["weights"]
    return float(sum(w.get(k, 0) for k in (
        "price_gap", "price_drop", "days_on_market",
        "opportunity_keywords", "area_liquidity")))


def prescreen_gate(cfg):
    """
    הסף לשלב הסינון המקדים.

    למה לא פשוט "ציון ≥ סף"? כי בשלב א' הבסיס הוא מחירי מבוקש, ובשלב ב'
    עסקאות אמיתיות — ומודעה יכולה לקבל 62 מול עמיתיה ו-80 מול עסקאות
    שנסגרו בפועל. לכן פותחים את השער במרווח מתחת לסף (prescreen_margin),
    כדי לא לפספס בדיוק את המקרים שהשלב השני נועד לגלות.

    ובנוסף — השער **מנורמל לסולם שבאמת זמין בשלב א'**. תשואה ועליית ערך
    שוות יחד 25 נקודות שאי אפשר לצבור לפני שליפת הנתונים הרשמיים, ולכן
    שער "מוחלט" של 55 היה למעשה דורש 73% מהניקוד האפשרי במקום 55% —
    והיה חוסם כמעט את כל הצינור. הנרמול שומר על הכוונה המקורית.
    """
    raw = max(0.0, float(cfg["alert_threshold"])
              - float(cfg.get("prescreen_margin", 15)))
    reachable = prescreen_reachable_max(cfg)
    if reachable <= 0:
        return raw
    return round(raw * reachable / 100.0, 2)
