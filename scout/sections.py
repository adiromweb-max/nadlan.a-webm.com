"""
שלושת המדורים שתמיד יש בהם תוכן.

הבעיה שזה פותר: השער המחמיר ("לבדוק דחוף" = פער אמיתי + אות תומך + קומפים
מספיקים) נועד למנוע התראות שווא, והוא עושה את זה היטב — אבל בשבוע רגיל הוא
מחזיר אפס. פלט שבועי ריק נראה כמו מערכת מתה, גם כשהיא עבדה מצוין.

לכן הפלט תמיד נושא שלושה מדורים שנגזרים מנתונים אמיתיים של הריצה, ואף אחד
מהם **אינו** דורש את השער המחמיר:

  א. מכ"ם ירידות מחיר — כל מודעה שהמחיר שלה ירד, לפי גודל הירידה המצטברת.
     זה התוכן הראשי: ירידת מחיר היא עובדה מדודה, לא הערכה.
  ב. הערך היחסי הטוב ביותר — 5 המודעות הרחוקות ביותר מתחת לחציון ההשוואה
     שלהן, כל אחת עם רמת הביטחון שלה. מודעה זולה שאי אפשר לאמת מופיעה כאן
     מסומנת, ולא נעלמת.
  ג. אזורים מתחממים — 3 האזורים המובילים בעליית ערך (CAGR) בריצה הזו.

הפונקציות כאן טהורות: מקבלות את מה שכבר חושב ומחזירות רשימות מסודרות.
"""
import logging

from .comps import CONF_HIGH, CONF_LOW, CONF_MEDIUM

log = logging.getLogger(__name__)

CONF_ORDER = {CONF_HIGH: 0, CONF_MEDIUM: 1, CONF_LOW: 2}


def _val(s, key, default=None):
    v = (s.get("value") or {}).get(key)
    return default if v is None else v


def price_drop_radar(scored, ladders, cfg, context=None):
    """
    כל המודעות שהמחיר שלהן ירד, ממוינות לפי הירידה המצטברת.

    ‎sharp_drop_pct‎ (ברירת מחדל 7%) מסמן "ירידה חדה". ירידה שחורגת מחסם
    השפיות מסומנת ‎suspect‎ ויורדת לתחתית הרשימה — לא מוסתרת, אבל גם לא
    תופסת את הכותרת: ירידה של 80% היא כמעט תמיד ספרה שנפלה, לא מציאה.

    ‎context‎ — המודעות הפעילות ב-DB. בריצה עם סריקה, מודעה שירדה במחיר
    בעבר עשויה לא להופיע בעמודים שנמשכו הפעם; בלי ה-context היא הייתה
    מופיעה במכ"ם בלי עיר ובלי קישור. מודעה שאינה פעילה כלל (ירדה מהאוויר)
    אינה מוצגת — אין טעם לדווח על ירידת מחיר של דירה שכבר לא למכירה.
    """
    sharp_at = float(cfg.get("sharp_drop_pct", 7.0))
    by_id = {s.get("id"): s for s in scored or []}
    ctx = dict(context or {})

    rows = []
    for lid, lad in (ladders or {}).items():
        if not lad.get("total_drop_pct") or lad["total_drop_pct"] <= 0:
            continue
        s = by_id.get(lid) or ctx.get(lid)
        if not s:
            continue
        value = s.get("value") or {}
        rows.append({
            "id": lid,
            "city": s.get("city"),
            "neighborhood": s.get("neighborhood"),
            "url": s.get("url"),
            "nadlan_link": s.get("nadlan_link"),
            "rooms": s.get("rooms"),
            "size_sqm": s.get("size_sqm"),
            "original_price": lad.get("original_price"),
            "current_price": lad.get("current_price"),
            "total_drop_pct": lad.get("total_drop_pct"),
            "num_drops": lad.get("num_drops"),
            "num_rises": lad.get("num_rises"),
            "last_drop_pct": lad.get("last_drop_pct"),
            "last_change_at": lad.get("last_change_at"),
            "history_text": lad.get("history_text"),
            "points": lad.get("points"),
            "sharp": (lad.get("total_drop_pct") or 0) >= sharp_at and not lad.get("suspect"),
            "suspect": bool(lad.get("suspect")),
            "score": s.get("score"),
            "tier_he": s.get("tier_he"),
            "value_gap_pct": value.get("value_gap_pct"),
            "comp_level_he": value.get("comp_level_he"),
            "confidence": value.get("confidence"),
            "confidence_he": value.get("confidence_he"),
            "in_db_only": lid not in by_id,
            "chart": s.get("chart_ladder"),
        })

    rows.sort(key=lambda r: (r["suspect"], -(r["total_drop_pct"] or 0)))
    log.info('מכ"ם ירידות: %d מודעות (%d חדות מעל %.0f%%, %d חשודות)',
             len(rows), sum(1 for r in rows if r["sharp"]), sharp_at,
             sum(1 for r in rows if r["suspect"]))
    return rows


def best_relative_value(scored, cfg, top_n=None):
    """
    ‎top_n‎ המודעות עם הפער הגדול ביותר מתחת לחציון ההשוואה שלהן.

    **בלי תלות בשער המחמיר** — מודעה נכנסת לכאן לפי הפער ורמת הביטחון
    בלבד, גם אם הציון שלה נמוך מסף ההתראה ואפילו אם אין עליה עסקאות
    מקומיות. סדר המיון: קודם מה שאינו חשוד, ואז לפי רמת ביטחון, ואז לפי
    גודל הפער — כך שמודעה מאומתת עם פער 12% מקדימה מודעה לא-מאומתת עם 20%.
    """
    top_n = int(top_n or cfg.get("best_value_top_n", 5))
    gap_min = float(cfg.get("comps_gap_min_pct", 8))

    cands = []
    for s in scored or []:
        v = s.get("value") or {}
        gap = v.get("value_gap_pct")
        if gap is None or gap <= 0:
            continue
        cands.append({
            "id": s.get("id"),
            "city": s.get("city"),
            "neighborhood": s.get("neighborhood"),
            "url": s.get("url"),
            "nadlan_link": s.get("nadlan_link"),
            "rooms": s.get("rooms"),
            "size_sqm": s.get("size_sqm"),
            "price": s.get("price"),
            "price_per_sqm": s.get("price_per_sqm"),
            "value_gap_pct": gap,
            "value_median_ppm": v.get("value_median_ppm"),
            "value_count": v.get("value_count"),
            "value_area": v.get("value_area"),
            "comp_level": v.get("comp_level"),
            "comp_level_he": v.get("comp_level_he"),
            "confidence": v.get("confidence"),
            "confidence_he": v.get("confidence_he"),
            "value_tag": v.get("value_tag"),
            "suspect": bool(v.get("suspect")),
            "unverified_attractive": bool(v.get("unverified_attractive")),
            "meaningful_gap": gap >= gap_min,
            "score": s.get("score"),
            "tier_he": s.get("tier_he"),
            "stage": s.get("stage"),
            "yield_pct": s.get("yield_pct"),
            "area_cagr_pct": s.get("area_cagr_pct"),
            "drop_pct": s.get("drop_pct"),
            "days_on_market": s.get("days_on_market"),
            "chart": s.get("chart_comps"),
        })

    cands.sort(key=lambda c: (c["suspect"],
                              CONF_ORDER.get(c["confidence"], 3),
                              -(c["value_gap_pct"] or 0)))

    # יד2 מציג לא פעם את אותה דירה פעמיים (פרטי + תיווך, או שני מתווכים).
    # בלי הכיווץ הזה שתי רשומות זהות תופסות שני מקומות מתוך חמישה.
    out, seen = [], set()
    for c in cands:
        key = (c.get("city"), c.get("price"), c.get("size_sqm"), c.get("rooms"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= top_n:
            break
    log.info("ערך יחסי: %d מועמדות, נבחרו %d (%d באמון גבוה, %d לא מאומתות)",
             len(cands), len(out),
             sum(1 for c in out if c["confidence"] == CONF_HIGH),
             sum(1 for c in out if c["unverified_attractive"]))
    return out


def hot_areas(areas, cfg, top_n=None):
    """‎top_n‎ האזורים המובילים בעליית ערך שנתית (CAGR) בריצה הזו."""
    top_n = int(top_n or cfg.get("hot_areas_top_n", 3))
    rows = []
    for a in (areas or {}).values():
        if a.get("cagr_pct") is None:
            continue
        years = [y for y in (a.get("years") or []) if y.get("median_ppm")]
        # מחיר דירה חציוני (₪ לעסקה, לא למ"ר) בתחילת ובסוף תקופת ה-CAGR —
        # זה המספר ש"מרגיש" כמו עליית מחירי נדל"ן באזור, יותר מ-₪ למ"ר.
        by_year = {y.get("year"): y for y in years}
        f_row = by_year.get(a.get("cagr_from_year")) or (years[0] if years else {})
        l_row = by_year.get(a.get("cagr_to_year")) or (years[-1] if years else {})
        first_price = (f_row or {}).get("median_price")
        latest_price = (l_row or {}).get("median_price")
        cagr = a.get("cagr_pct")
        yrs = a.get("years_covered") or 0
        # עלייה כוללת על פני כל התקופה (לא לשנה) — קריא יותר במבט מהיר.
        total_rise = None
        if cagr is not None and yrs:
            total_rise = ((1.0 + cagr / 100.0) ** yrs - 1.0) * 100.0
        elif first_price and latest_price and first_price > 0:
            total_rise = (latest_price - first_price) / first_price * 100.0
        rows.append({
            "area_key": a.get("area_key"),
            "area_name": a.get("area_name"),
            "area_level": a.get("area_level"),
            "area_level_he": "שכונה" if a.get("area_level") == "neighborhood" else "יישוב",
            "city": a.get("city"),
            "cagr_pct": cagr,
            "total_rise_pct": round(total_rise, 1) if total_rise is not None else None,
            "years_covered": yrs,
            "cagr_from_year": a.get("cagr_from_year"),
            "cagr_to_year": a.get("cagr_to_year"),
            "latest_median_ppm": years[-1]["median_ppm"] if years else None,
            "first_median_ppm": years[0]["median_ppm"] if years else None,
            "latest_median_price": latest_price,
            "first_median_price": first_price,
            "deal_quarters": sum(y.get("deal_quarters") or 0 for y in years),
            "data_version": a.get("data_version"),
            "yearly": years,
            "chart": a.get("chart_trend"),
        })
    rows.sort(key=lambda r: -(r["cagr_pct"] or 0))
    out = rows[:top_n]
    log.info("אזורים מתחממים: %d אזורים עם CAGR, המוביל %s (%.1f%%)",
             len(rows), out[0]["area_name"] if out else "—",
             out[0]["cagr_pct"] if out else 0.0)
    return out


def build(scored, ladders, areas, cfg, context=None):
    """כל שלושת המדורים במכה אחת. תמיד מחזיר את שלושת המפתחות."""
    return {
        "price_drop_radar": price_drop_radar(scored, ladders, cfg, context),
        "best_relative_value": best_relative_value(scored, cfg),
        "hot_areas": hot_areas(areas, cfg),
    }


def counts(sections):
    """מונים קצרים לדו"ח ולמייל."""
    radar = sections.get("price_drop_radar") or []
    return {
        "drops": len(radar),
        "sharp_drops": sum(1 for r in radar if r.get("sharp")),
        "best_value": len(sections.get("best_relative_value") or []),
        "hot_areas": len(sections.get("hot_areas") or []),
    }
