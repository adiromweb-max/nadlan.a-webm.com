"""
חישובי אחוזים — מקור אמת יחיד, כדי שאותה נוסחה לא תיכתב פעמיים בגרסאות שונות.

הכלל: **כל** אחוז במערכת הוא מספר בסולם 0..100 (3.7 = 3.7%), לעולם לא שבר
(0.037) ולעולם לא מוכפל פעמיים. פורמט האקסל הוא ‎0.0"%"‎ (סימן אחוז מילולי)
ולא ‎0.0%‎ — פורמט האחוז האמיתי של Excel מכפיל ב-100 בתצוגה, ו-3.7 היה
מוצג כ-370%. אל תחליף אותו.
"""
import logging

log = logging.getLogger(__name__)

# ירידה גדולה מזה אינה ירידת מחיר אמיתית אלא שגיאת נתונים (מחיר שכירות
# שנכתב בשדה המכירה, ספרה שנפלה, מודעה שהוחלפה תחת אותו token).
MAX_PLAUSIBLE_DROP_PCT = 60.0

# ── בקרת שפיות על גודל דירה ו-₪/מ"ר ──────────────────────────────────
# הפרסר של יד2 מוציא לעיתים גודל שגוי: 1 מ"ר, 13 מ"ר, או אפילו את המחיר
# עצמו בשדה הגודל (755000). ₪/מ"ר שנגזר מגודל כזה אבסורדי, והפער מתפוצץ
# ל-‎-7000%‎. לכן גודל ו-₪/מ"ר חייבים ליפול בתוך טווח סביר לדירה; אחרת
# ה-₪/מ"ר אינו אמין ואסור לגזור ממנו פער. הגבולות ניתנים לדריסה מה-config.
MIN_PLAUSIBLE_SIZE_SQM = 20.0        # פחות מזה = שגיאת פרסור (מרפסת/מחסן/טעות)
MAX_PLAUSIBLE_SIZE_SQM = 1000.0      # יותר מזה = המחיר נפל לשדה הגודל
MIN_PLAUSIBLE_PPM = 2500.0           # ₪/מ"ר נמוך מזה לא קיים בשוק דירות אמיתי
MAX_PLAUSIBLE_PPM = 70000.0          # ₪/מ"ר גבוה מזה = גודל שגוי (דירה זעירה מדי)


def size_is_plausible(size, cfg=None):
    """האם גודל הדירה במ\"ר סביר? None/מחוץ לטווח = לא אמין."""
    cfg = cfg or {}
    lo = float(cfg.get("min_plausible_size_sqm", MIN_PLAUSIBLE_SIZE_SQM))
    hi = float(cfg.get("max_plausible_size_sqm", MAX_PLAUSIBLE_SIZE_SQM))
    try:
        s = float(size)
    except (TypeError, ValueError):
        return False
    return lo <= s <= hi


def ppm_is_plausible(ppm, cfg=None):
    """האם ₪/מ\"ר סביר לדירת מגורים?"""
    cfg = cfg or {}
    lo = float(cfg.get("min_plausible_ppm", MIN_PLAUSIBLE_PPM))
    hi = float(cfg.get("max_plausible_ppm", MAX_PLAUSIBLE_PPM))
    try:
        v = float(ppm)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def clean_ppm(price, size, cfg=None):
    """
    ₪/מ"ר נקי ואמין — או None אם הגודל/המחיר לא סבירים.

    מחזיר (ppm_or_None, ok_bool). כשהגודל מחוץ לטווח, או שה-₪/מ"ר שנגזר
    ממנו מחוץ לטווח, מחזיר (None, False) — כדי שאף פער לא ייגזר מנתון שבור.
    """
    try:
        p = float(price)
        s = float(size)
    except (TypeError, ValueError):
        return None, False
    if p <= 0 or not size_is_plausible(s, cfg):
        return None, False
    ppm = p / s
    if not ppm_is_plausible(ppm, cfg):
        return None, False
    return ppm, True


def drop_pct(old_price, new_price):
    """
    אחוז ירידת המחיר: (ישן − חדש) / ישן × 100.

    חיובי = המחיר ירד. שלילי = המחיר עלה. מחזיר None כשאין בסיס לחישוב.
    """
    try:
        old = float(old_price)
        new = float(new_price)
    except (TypeError, ValueError):
        return None
    if old <= 0:
        return None
    return (old - new) / old * 100.0


def is_drop_sane(pct, limit=MAX_PLAUSIBLE_DROP_PCT):
    """
    האם הירידה סבירה? |pct| > limit מעיד על שגיאת נתונים ולא על הזדמנות.
    ערך None נחשב סביר (פשוט אין ירידה).
    """
    if pct is None:
        return True
    try:
        return abs(float(pct)) <= float(limit)
    except (TypeError, ValueError):
        return False


def gap_pct(benchmark_ppsqm, listing_ppsqm):
    """
    פער מול בסיס ההשוואה: (בסיס − מודעה) / בסיס × 100.
    חיובי = המודעה זולה מהבסיס = טוב.
    """
    try:
        base = float(benchmark_ppsqm)
        mine = float(listing_ppsqm)
    except (TypeError, ValueError):
        return None
    if base <= 0 or mine <= 0:
        return None
    return (base - mine) / base * 100.0


def cagr_pct(first_value, last_value, years):
    """
    שיעור צמיחה שנתי מורכב באחוזים. מחזיר None כשאין בסיס.
    """
    try:
        first = float(first_value)
        last = float(last_value)
        n = float(years)
    except (TypeError, ValueError):
        return None
    if first <= 0 or last <= 0 or n <= 0:
        return None
    return ((last / first) ** (1.0 / n) - 1.0) * 100.0


def gross_yield_pct(annual_rent, price):
    """תשואת שכירות ברוטו: שכ"ד שנתי / מחיר × 100."""
    try:
        rent = float(annual_rent)
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0 or rent <= 0:
        return None
    return rent / p * 100.0


def assert_drop_math():
    """
    בדיקת שפיות לנוסחת ירידת המחיר, נקראת בתחילת כל ריצה ונרשמת ללוג.
    400,000 → 385,000 הוא ירידה של 3.75% ומעוגל לתצוגה 3.8 — לא 375 ולא 0.0375.
    מחזיר dict לדו"ח הריצה.
    """
    raw = drop_pct(400000, 385000)
    shown = round(raw, 1)
    ok = (shown == 3.8)

    checks = [
        ("400000→385000", raw, shown, 3.8),
        ("1000000→963000", drop_pct(1000000, 963000), round(drop_pct(1000000, 963000), 1), 3.7),
    ]
    for label, r, s, want in checks:
        if round(s, 1) != want:
            ok = False
            log.error("בדיקת ירידת מחיר נכשלה: %s → %.4f (מוצג %.1f), ציפינו %.1f",
                      label, r, s, want)

    sane = is_drop_sane(3.75) and not is_drop_sane(370.0) and not is_drop_sane(-95.0)
    if not sane:
        ok = False
        log.error("בדיקת חסם השפיות של ירידת מחיר נכשלה")

    result = {
        "ok": bool(ok),
        "raw": raw,
        "shown": shown,
        "expected": 3.8,
        "sanity_limit": MAX_PLAUSIBLE_DROP_PCT,
        "text": (f"400,000→385,000 = {shown:.1f}% (גולמי {raw:.4f}) — "
                 f"{'תקין' if ok else 'נכשל'}; חסם שגיאת נתונים {MAX_PLAUSIBLE_DROP_PCT:.0f}%"),
    }
    log.info("בדיקת נוסחת ירידת מחיר: %s", result["text"])
    return result
