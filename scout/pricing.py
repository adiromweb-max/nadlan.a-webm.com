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


# ------------------------------------------------------------------
# עלויות רכישה בפועל — "פער נטו"
# ------------------------------------------------------------------
# למה זה כאן: פער של 8% מול חציון האזור אינו עסקה. מס רכישה למשקיע לבדו
# הוא 8%, ולפניו עוד עו"ד ותיווך. כלומר מודעה שעברה את הסף המינימלי הקודם
# הייתה מובטחת כהפסד ברגע החתימה. כל ההכתרות נגזרות מכאן והלאה מהפער
# **אחרי** עלויות העסקה, לא מהפער המוצג.
#
# מס רכישה — שני מסלולים, וברירת המחדל היא **דירה ראשונה**.
#
# למה זה קריטי: במדרגות דירה יחידה יש פטור מלא עד ~1.98 מיליון ₪, וכל
# טווח המחירים שהמנוע סורק בדרום נמצא מתחת לתקרה הזאת. כלומר לקונה דירה
# ראשונה מס הרכישה הוא **אפס**, ועלויות העסקה יורדות מ-11% לכ-3%.
# הנחה שכל המשתמשים קונים דירה שנייה מעוותת את הפער נטו של כל מודעה
# בדרום בשמונה נקודות אחוז. המסלול נקבע ב-‎buyer_track‎ ב-config, וניתן
# לחישוב לכל מסלול בנפרד לצורך תצוגה.
FIRST_HOME_BRACKETS = [(1_978_745.0, 0.0), (2_347_040.0, 3.5),
                       (6_055_070.0, 5.0), (20_183_565.0, 8.0),
                       (None, 10.0)]
SECOND_HOME_BRACKETS = [(6_055_070.0, 8.0), (None, 10.0)]
DEFAULT_TRACK = "first_home"
LEGAL_PCT = 0.5
BROKERAGE_PCT = 2.0
VAT_PCT = 18.0
TRACK_HE = {"first_home": "דירה ראשונה", "second_home": "דירה שנייה ומעלה"}


def _cfg_float(cfg, key, default):
    try:
        return float((cfg or {}).get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _brackets(cfg, track):
    """מדרגות מס רכישה למסלול. ניתנות לדריסה מה-config כשהחוק משתנה."""
    conf = ((cfg or {}).get("purchase_tax_brackets") or {}).get(track)
    if conf:
        out = []
        for row in conf:
            cap = row[0]
            out.append((None if cap in (None, "", 0) else float(cap), float(row[1])))
        return out
    return FIRST_HOME_BRACKETS if track == "first_home" else SECOND_HOME_BRACKETS


def buyer_track(cfg=None, track=None):
    """המסלול בתוקף: מה שהועבר במפורש, אחרת מה-config, אחרת דירה ראשונה."""
    t = track or (cfg or {}).get("buyer_track") or DEFAULT_TRACK
    return t if t in ("first_home", "second_home") else DEFAULT_TRACK


def purchase_tax_ils(price, cfg=None, track=None):
    """
    מס רכישה בשקלים, מדרגתי, לפי המסלול.

    דירה ראשונה על 599,000 ₪ → 0 ₪.
    דירה שנייה על אותו מחיר   → 47,920 ₪.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    tax, prev = 0.0, 0.0
    for cap, rate in _brackets(cfg, buyer_track(cfg, track)):
        top = p if cap is None else min(p, cap)
        if top > prev:
            tax += (top - prev) * rate / 100.0
            prev = top
        if cap is not None and p <= cap:
            break
    return tax


def acquisition_costs(price, cfg=None, is_agency=False, track=None):
    """
    עלויות הרכישה שנשלמות במזומן בנוסף למחיר: מס רכישה, עו"ד, תיווך.

    תיווך נספר **רק** במודעת תיווך — מודעה פרטית חוסכת אותו, וזה הפרש
    אמיתי של יותר מ-2% בין שתי מודעות באותו מחיר.
    שיפוץ אינו נכלל כאן: הוא אינו עלות עסקה ודאית אלא רזרבה, ומדווח בנפרד.

    מחזיר dict עם הסכומים בשקלים והאחוז הכולל מהמחיר.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        p = 0.0
    tr = buyer_track(cfg, track)
    if p <= 0:
        return {"total_ils": None, "total_pct": None, "tax_ils": None,
                "legal_ils": None, "brokerage_ils": None,
                "is_agency": bool(is_agency), "track": tr,
                "track_he": TRACK_HE[tr]}

    vat = 1.0 + _cfg_float(cfg, "vat_pct", VAT_PCT) / 100.0
    tax = purchase_tax_ils(p, cfg, tr) or 0.0
    legal = p * _cfg_float(cfg, "legal_pct", LEGAL_PCT) / 100.0 * vat
    brokerage = (p * _cfg_float(cfg, "brokerage_pct", BROKERAGE_PCT) / 100.0 * vat
                 if is_agency else 0.0)
    total = tax + legal + brokerage
    return {
        "tax_ils": round(tax),
        "legal_ils": round(legal),
        "brokerage_ils": round(brokerage),
        "total_ils": round(total),
        "total_pct": round(total / p * 100.0, 2),
        "is_agency": bool(is_agency),
        "track": tr,
        "track_he": TRACK_HE[tr],
    }


def net_gap_pct(gap, cost_pct):
    """
    הפער שנשאר אחרי עלויות הרכישה.

    הפער המוצג משווה מחיר מבוקש לחציון. אבל מה שהקונה משלם בפועל הוא
    המחיר כפול (1+עלויות), ולכן:
        נטו = 1 − (1 − פער) × (1 + עלויות)
    פער 21% עם עלויות 11% → 12.3% נטו, לא 21%. זו הנוסחה שמונעת את
    "נראתה עסקה, התפוגגה כשחפרנו".
    """
    try:
        g = float(gap) / 100.0
        c = float(cost_pct) / 100.0
    except (TypeError, ValueError):
        return None
    return round((1.0 - (1.0 - g) * (1.0 + c)) * 100.0, 2)


def sqm_per_room(size_sqm, rooms):
    """מ"ר לחדר — אצבע לזיהוי אנומליית גודל/חדרים. None כשחסר נתון."""
    try:
        s = float(size_sqm)
        r = float(rooms)
    except (TypeError, ValueError):
        return None
    if s <= 0 or r <= 0:
        return None
    return s / r


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
