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

from . import peers, tracklist
from .pricing import (acquisition_costs, is_drop_sane, net_gap_pct,
                      sqm_per_room)

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


def score_days_on_market(first_seen, weight, full_at_days, today=None,
                         drop_pct=None, cfg=None, listed_at=None):
    """
    כמה זמן המודעה יושבת בשוק — וותק ארוך הוא הזדמנות, לא פסול.

    למה הכיוון התהפך: הגרסה הקודמת קנסה מודעה שעומדת חודשים בלי שהמחיר
    זז, בהנחה שהשוק דחה אותה. ההנחה הזאת נכונה למי שקונה במחיר המבוקש —
    אבל קונה שמגיש הצעה נמוכה מחפש בדיוק את המוכר הזה: מי שפרסם לפני
    שנה, לא מכר, ועדיין משלם ארנונה. מוכר כזה לא חייב להוריד את המחיר
    בפרסום כדי להסכים להוריד אותו במשא ומתן.
    לכן ותק מזכה בניקוד לפי עצמו, וירידת מחיר היא תוספת ולא תנאי.

    הוותק נמדד מ-‎listed_at‎ — תאריך הפרסום בשוק, מוערך מתאריכי התמונות —
    ולא מ-‎first_seen‎, שהוא רק מתי *אנחנו* ראינו לראשונה. ההבדל נמדד על
    המודעות הפעילות: ממוצע 79 ימי ותק שפוספסו, במקרה הקצה מעל שלוש שנים.
    ‎first_seen‎ נשאר נפילה-חזרה למודעה בלי תמונות.

    ‎stale_no_drop‎ עדיין מוחזר — אבל כדגל תצוגה בלבד ("עומד זמן רב והמחיר
    לא זז"), לא כקנס. זו אזהרה שהקונה שוקל, לא פסילה אוטומטית.

    מחזיר (ניקוד, ימים, דגל_עומד_בלי_ירידה).
    """
    cfg = cfg or {}
    basis = listed_at or first_seen
    if not basis:
        return 0.0, 0, False
    today = today or date.today()
    try:
        seen = datetime.fromisoformat(str(basis)).date()
    except (TypeError, ValueError):
        return 0.0, 0, False
    days = max((today - seen).days, 0)
    # חסם שפיות: תאריך תמונה שגוי לא ייצר "מודעה בת 20 שנה"
    cap = int(cfg.get("max_plausible_days_on_market", 1825))
    if days > cap:
        days = cap
    score = _clamp(days / float(full_at_days), 0.0, 1.0) * weight

    has_drop = bool(drop_pct and float(drop_pct) > 0)
    stale_at = int(cfg.get("stale_suspect_days", 180))
    stale_no_drop = bool(days >= stale_at and not has_drop)
    return round(score, 2), days, stale_no_drop


def score_mlm(listing, weight, cfg=None):
    """
    מינוף מול מוכר של דירת מחיר למשתכן שהמגבלה עליה פגה.

    למה זה אות נפרד ולא עוד "פער מחיר": הפער מודד כמה הדירה זולה מול
    השוק. זה מודד משהו אחר לגמרי — **כמה המוכר יכול להתפשר**. מי שקנה
    במחיר מסובסד ויושב על מאות אלפי שקלים רווח יכול לרדת במחיר ועדיין
    לצאת ברווח גדול; מוכר שקנה במחיר שוק לא יכול להגיע לשם בכלל. שני
    מוכרים עם אותו מחיר מבוקש אינם אותו מוכר.

    שני רכיבים:
      כרית (60%)     — הרווח הגלום כאחוז מהמחיר המבוקש.
      לחץ היצע (40%) — כמה דירות מאותו מתחם מוצעות עכשיו. זו התחרות
                       בפועל בין דירות זהות, ולא ממוצע עירוני שמטשטש
                       מתחם בודד.

    מגבלה פגה היא תנאי: לפני כן המכירה כרוכה בהשבת הסבסוד, והמוכר דווקא
    פחות גמיש. פרויקט שטרם שוחרר מקבל אפס — ומסומן בנפרד לתצוגה.

    **הניקוד מוכפל בוודאות הזיהוי.** זה לא סייג קוסמטי: כל הטענה היא שגל
    השחרור הוא מה שמרכך את המחירים, ואם חלק מהמודעות אינן מחיר למשתכן
    בכלל, האות מתפרק ל"אזור מוצף משום מה". ודאות בינונית שווה 70% מהניקוד
    ונמוכה 40% — כך שמודעה שרק שכבה אחת זיהתה אינה נספרת כמו מודעה ששלוש
    ראיות מסכימות עליה.
    """
    cfg = cfg or {}
    if not weight or not listing.get("mlm") or not listing.get("mlm_released"):
        return 0.0
    # ‎מאומת ידנית‎ היא הדרגה שמעל הזיהוי המרחבי: בן אדם פתח את המודעה
    # והכריע. אין לה סייג, ולכן המקדם 1.0 כמו "גבוהה".
    conf_factor = {tracklist.CONF_MANUAL: 1.0, "גבוהה": 1.0,
                   "בינונית": 0.7, "נמוכה": 0.4}.get(
        listing.get("mlm_confidence"), 0.7)
    cushion_pct = listing.get("mlm_cushion_pct")
    supply_pct = listing.get("mlm_supply_pct")
    cushion_full = float(cfg.get("mlm_cushion_full_at_pct", 40.0))
    supply_full = float(cfg.get("mlm_supply_full_at_pct", 3.0))
    c = _clamp(float(cushion_pct) / cushion_full, 0.0, 1.0) if cushion_pct else 0.0
    s = _clamp(float(supply_pct) / supply_full, 0.0, 1.0) if supply_pct else 0.0
    return round((c * 0.6 + s * 0.4) * weight * conf_factor, 2)


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

def supporting_signal(drop_pct, dom_days, liq_score, cfg, liq_weight, listing=None):
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
    real_drop = bool(drop_pct and sane_drop
                     and drop_pct >= float(cfg.get("signal_min_drop_pct", 3)))
    if real_drop:
        signals.append(f"ירידת מחיר {drop_pct:.1f}%")
    # ותק ארוך הוא אות תומך בזכות עצמו: מוכר שיושב בשוק חודשים ארוכים
    # הוא מוכר שאפשר לדבר איתו על המחיר, גם אם לא הוריד אותו בפרסום.
    # (הגרסה הקודמת דרשה גם ירידת מחיר, וכך חנקה בדיוק את האות הזה.)
    long_dom = bool(dom_days and dom_days >= int(
        cfg.get("signal_min_days_on_market", 60)))
    if long_dom:
        months = dom_days / 30.4
        label = (f"{months:.0f} חודשים בשוק" if months >= 2
                 else f"{dom_days} יום בשוק")
        signals.append(f"{label}, והמחיר ירד" if real_drop else label)
    if liq_weight and liq_score >= float(cfg.get("signal_liquidity_frac", 0.7)) * liq_weight:
        signals.append("אזור נזיל")
    # מחיר למשתכן משוחרר עם כרית משמעותית — ראיה עצמאית שלמוכר יש מרחב
    # לרדת, ולא רק שהמחיר נראה נמוך.
    l = listing or {}
    # רק ודאות גבוהה נחשבת אות תומך. אות שמקדם מודעה ל"לבדוק דחוף" חייב
    # להיות ראיה, ולא השערה שנשענת על שכבה אחת.
    _top_conf = (tracklist.CONF_MANUAL, "גבוהה")
    if (l.get("mlm_released") and l.get("mlm_confidence") in _top_conf
            and (l.get("mlm_cushion_pct") or 0) >= float(
                cfg.get("signal_min_cushion_pct", 20))):
        _how = ("אימות ידני" if l.get("mlm_confirmed") else "זיהוי מרחבי")
        signals.append(
            f"מחיר למשתכן משוחרר ({_how}), כרית {l['mlm_cushion_pct']:.0f}%")
    # אישור ידני בלי כרית מחושבת הוא עדיין ראיה: המשתמש ראה בעיניו שזו
    # דירת מחיר למשתכן, גם אם שכבת הפוליגונים פספסה אותה ולכן אין מחיר
    # הגרלה להשוות אליו.
    elif l.get("mlm_confirmed"):
        signals.append("מחיר למשתכן — אומת ידנית")
    # ── אותות התאומות ──
    # אלה ראיות עצמאיות במובן המדויק שהפונקציה הזאת דורשת: הן אינן
    # נגזרות מהנתון הרשמי שיצר את הפער, אלא מהתנהגות המוכרים עצמם
    # באותו רחוב ממש — מי זול מהשכנים, מי נשבר ראשון, וכמה מתחרים
    # נוספו. מוכר שהוריד פעמיים הוא ראיה חזקה מכל חציון.
    signals.extend(peers.signals(l, cfg))
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


def assign_tier(score, comp, has_signal, cfg, manual_check=False,
                net_gap=None, asset_risk=False):
    """
    הדירוג שמחליף את סף ההתראה הבודד.

    הכללים, לפי סדר קדימות:
      * בלי קומפים מספיקים — אין ראיית מחיר, ולכן לכל היותר "למעקב".
      * ‎can_crown=False‎ — ההשוואה אינה ראיה מקומית מספקת (חציון עירוני
        ביישוב גדול, גודל חריג לקטגוריה). לכל היותר "למעקב".
      * ‎asset_risk‎ — נמצא סיכון בנכס עצמו (דייר מוגן, חכירה לא מהוונת,
        חריגת בנייה, כינוס). מחיר נמוך כאן הוא **המחיר הנכון**, לא הנחה.
        לכל היותר "למעקב", וסיבת החסימה מוצגת.
      * פער חשוד (>30%) — כמעט תמיד שגיאת נתונים או נכס לא-השוואתי.
        לעולם לא בכותרת; לכל היותר "שווה בדיקה".
      * ‎manual_check‎ (למשל ירידת מחיר חריגה) — גם כן לא בכותרת.
      * "לבדוק דחוף" דורש פער **נטו** חיובי מעל הסף, אות תומך, והשוואה
        שמותר לה להכתיר.

    למה נטו ולא גולמי: הסף הקודם היה 8% גולמי, בעוד שעלויות הרכישה של
    משקיע (מס רכישה 8% + עו"ד + תיווך) הן 9–11%. כלומר מודעה שעמדה בסף
    הייתה הפסד ודאי. ‎net_gap‎ הוא הפער אחרי העלויות; כשהוא לא חושב
    (חסר מחיר) נופלים חזרה לפער הגולמי כדי לא להפיל שורה.
    """
    tiers = cfg.get("tiers") or {}
    t_urgent = float(tiers.get("check_urgent", 78))
    t_worth = float(tiers.get("worth_checking", 66))
    t_watch = float(tiers.get("watch", 55))
    gap_min = float(cfg.get("comps_net_gap_min_pct", 5))

    comp = comp or {}
    gap = comp.get("gap_pct")
    sufficient = bool(comp.get("sufficient"))
    suspect = bool(comp.get("suspect"))
    # ברירת מחדל True כדי לא לשבור קריאות ישנות שלא מעבירות את השדה
    can_crown = comp.get("can_crown", True) is not False

    if not sufficient or not can_crown or asset_risk:
        return TIER_WATCH if score >= t_watch else TIER_NONE

    effective = net_gap if net_gap is not None else gap
    real_gap = effective is not None and effective >= gap_min and not suspect

    if real_gap and has_signal and score >= t_urgent and not manual_check:
        return TIER_URGENT
    if (real_gap or suspect) and score >= t_worth:
        return TIER_WORTH
    if score >= t_watch:
        return TIER_WATCH
    return TIER_NONE


def plain_reason(city, area, comp, drop_pct, dom_days, yield_info, area_info,
                 signals, tier, opp_type, net_gap=None, costs=None,
                 risk_hits=None):
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

    # הפער נטו — זה המספר שקובע אם יש כאן עסקה
    if net_gap is not None and (costs or {}).get("total_pct") is not None:
        c = costs["total_pct"]
        kind = "כולל תיווך" if costs.get("is_agency") else "בלי תיווך (מודעה פרטית)"
        if net_gap >= 0:
            bits.append(f"פער נטו {net_gap:.0f}% אחרי עלויות רכישה "
                        f"{c:.1f}% ({kind})")
        else:
            bits.append(f"אחרי עלויות רכישה {c:.1f}% ({kind}) הפער נמחק "
                        f"({net_gap:.0f}%) — זו לא עסקה")

    if risk_hits:
        bits.append("סיכון בנכס: " + ", ".join(risk_hits[:2]))

    if comp.get("crown_block_reason"):
        bits.append(comp["crown_block_reason"])

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
                  area_info=None, street=None, listed_at=None):
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
    _listed = listed_at or listing.get("listed_at")
    dom_score, dom_days, stale_no_drop = score_days_on_market(
        first_seen, w["days_on_market"], cfg["days_on_market_full_at"], today,
        drop_pct=drop_pct, cfg=cfg, listed_at=_listed)
    kw_score, kw_found = score_keywords(
        listing, cfg.get("keywords") or [], w["opportunity_keywords"])
    mlm_score = score_mlm(listing, w.get("mlm_leverage", 0), cfg)
    # השוואת תאומות — "אותו רחוב, אותו מספר חדרים, אותו גודל". האות הזה
    # מחושב ב-peers.annotate לפני הניקוד, בעלות אפס בקשות. הוא **מצטבר**
    # ל-price_gap ולא מחליף אותו: price_gap מודד מול עסקאות שנסגרו, וכאן
    # מודדים מול המתחרות שמוצעות עכשיו. שתי שאלות שונות, שתי תשובות.
    peer_score = peers.score(listing, w.get("peer_gap", 0), cfg)
    liq_score = score_area_liquidity(baseline, w["area_liquidity"], active_listings)

    y_pct = (yield_info or {}).get("yield_pct")
    cagr = (area_info or {}).get("cagr_pct")
    yield_score = score_rental_yield(
        y_pct, w.get("rental_yield", 0),
        cfg.get("yield_floor_pct", 2.0), cfg.get("yield_full_at_pct", 5.0))
    apprec_score = score_appreciation(
        cagr, w.get("area_appreciation", 0), cfg.get("cagr_full_at_pct", 6.0))

    raw_total = round(gap_score + drop_score + dom_score + kw_score + liq_score
                      + yield_score + apprec_score + mlm_score + peer_score, 2)

    # ── נרמול כשאין קבוצת תאומות ──
    # נמדד על 1,254 מודעות פעילות: רק 33% נמצאות בקבוצה של 3+ דירות זהות
    # באותו רחוב או שכונה. לשתי השלישים האחרות אין תאומות **לא** בגלל
    # משהו בדירה, אלא בגלל שאף אחד אחר לא מוכר דירה כזאת שם עכשיו.
    # היעדר ראיה אינו ראיה שלילית, ולכן 14 הנקודות האלה אינן "0" אלא
    # **לא ברות-השגה** — בדיוק ההבחנה שכבר עומדת מאחורי prescreen_gate.
    # בלי הנרמול הזה סף "לבדוק דחוף" (78) היה דורש מהן 91% מהסולם הזמין
    # והיה חוסם אותן כמעט לגמרי.
    #
    # ההשלכה, במכוון: דירה שיש לה תאומות ויושבת בדיוק על החציון שלהן
    # **כן** מאבדת את הנקודות. זה לא עיוות — מוכר שהדירה שלו זהה לשש
    # אחרות ומתומחרת כמוהן הוא בדיוק מי שאין לו סיבה להיות זה שזז.
    peer_weight = float(w.get("peer_gap", 0) or 0)
    reachable = 100.0 - (peer_weight if not listing.get("peer_level") else 0.0)
    scale = 100.0 / reachable if 0 < reachable < 100 else 1.0
    total = round(raw_total * scale, 2)

    has_signal, signals = supporting_signal(
        drop_pct, dom_days, liq_score, cfg, w["area_liquidity"], listing=listing)

    # ── דגל איכות נתונים — נקבע לפני הדירוג, כי הוא חוסם הכתרה ──
    bad_drop = drop_pct is not None and not is_drop_sane(
        drop_pct, sanity or 60)
    if stage == "final":
        quality = (comp or {}).get("data_quality") or "אין נתוני השוואה"
    else:
        quality = "סינון מקדים — טרם הושווה לעסקאות"
    if bad_drop:
        quality = f"ירידת מחיר חריגה ({drop_pct:.0f}%) — בדיקה ידנית"

    # ── עלויות רכישה והפער נטו ──
    # זה מה שמפריד "נראה זול" מ"זול בפועל". מודעת תיווך נושאת עוד ~2.4%.
    is_agency = str(listing.get("listing_type") or listing.get("ad_type") or "") == "agency"
    costs = acquisition_costs(listing.get("price"), cfg, is_agency=is_agency)
    net_gap = (net_gap_pct(gap_pct, costs["total_pct"])
               if gap_pct is not None and costs.get("total_pct") is not None else None)

    # ── סיכון בנכס עצמו ──
    # שני סוגים. (1) מילות סיכון: דייר מוגן, חכירה לא מהוונת, חריגות בנייה,
    #     כינוס. המחיר הנמוך שם הוא המחיר הנכון — לא הנחה.
    # (2) אנומליית גודל/חדרים: 144 מ"ר עם 3 חדרים אינה טעות נתונים אלא
    #     חתימה של תוספת בנייה/פיצול, ולכן גם ₪/מ"ר נמוך שם אינו הנחה.
    #     הדגל הזה היה עד היום כיתוב בדשבורד בלבד ולא נגע בדירוג.
    blob = " ".join(str(x) for x in (listing.get("text"),
                    listing.get("description"), listing.get("condition_text")) if x)
    risk_hits = [k for k in (cfg.get("risk_keywords") or []) if k and k in blob]
    spr = sqm_per_room(listing.get("size_sqm"), listing.get("rooms"))
    spr_high = float(cfg.get("sqm_per_room_max", 45))
    spr_low = float(cfg.get("sqm_per_room_min", 13))
    size_anomaly = bool(spr and (spr > spr_high or spr < spr_low))
    if size_anomaly:
        risk_hits = risk_hits + [
            f'{spr:.0f} מ"ר לחדר — חשד לתוספת בנייה/פיצול'
            if spr > spr_high else f'{spr:.0f} מ"ר לחדר — נתוני גודל חשודים']
    asset_risk = bool(risk_hits)

    # ── וטו רחוב ──
    # אם מול המבוקש באותו רחוב הפער מתאדה, ה"הנחה" היא מיקום ולא מחיר.
    # זו הבדיקה שרצה רק על מי שעבר את השער, ולכן מותר לה להיות יקרה.
    street = street or {}
    street_veto = street.get("street_status") == "veto"
    comp_for_tier = comp if stage == "final" else None
    if street_veto and comp_for_tier:
        comp_for_tier = dict(comp_for_tier, can_crown=False,
                             crown_block_reason=street.get("street_note"))

    tier = assign_tier(total, comp_for_tier, has_signal,
                       cfg, manual_check=bad_drop, net_gap=net_gap,
                       asset_risk=asset_risk)
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
    bad_kw = [k for k in (cfg.get("disqualify_keywords") or []) if k and k in blob]
    disqualified = suspicious_price or bool(bad_kw)
    if disqualified:
        tier = TIER_NONE          # לעולם לא הזדמנות
        if suspicious_price and quality in ("תקין", "סינון מקדים — טרם הושווה לעסקאות"):
            quality = "מחיר חשוד — נמוך בצורה לא-סבירה מהשוק (מחיר-פיתיון/זכויות?)"
        elif bad_kw:
            quality = "לא דירת יד-שנייה סטנדרטית (%s)" % ", ".join(bad_kw[:2])

    # דגלי איכות שנובעים מהבדיקות החדשות — אחרי הדירוג, לפני ההסבר
    if street_veto and quality == "תקין":
        quality = street.get("street_note") or "הפער מתאדה מול הרחוב"
    elif asset_risk and quality in ("תקין", "סינון מקדים — טרם הושווה לעסקאות"):
        quality = "סיכון בנכס (%s)" % ", ".join(risk_hits[:2])
    # ותק ארוך **אינו** פגם באיכות הנתון — הנתון תקין, המודעה פשוט יושבת
    # זמן רב. זה מוצג כאות תומך ("X חודשים בשוק") ולא כאזהרה, כי מבחינת
    # קונה שמגיש הצעה זו נקודת הפתיחה הטובה ולא סיבה לפסול.
    elif (net_gap is not None and gap_pct is not None and net_gap < 0
          and quality == "תקין"):
        quality = "הפער נמוך מעלויות הרכישה — אין עסקה"

    area_name = (comp or {}).get("comp_area") or (baseline or {}).get("area_name")
    reason = plain_reason(listing.get("city"), area_name, comp if stage == "final" else None,
                          drop_pct, dom_days, yield_info, area_info, signals,
                          tier, opp_type, net_gap=net_gap, costs=costs,
                          risk_hits=risk_hits)

    return {
        "score": total,
        # ‎breakdown‎ נושא את הנקודות הגולמיות, ולכן הוא מסתכם ב-‎score_raw‎
        # ולא ב-‎score‎. זה בכוונה: עמודה שמראה "price_gap 26.1" כשהמשקל
        # הוא 24 הייתה מטעה יותר ממועילה.
        "score_raw": raw_total,
        "score_scale": round(scale, 4),
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
            "mlm_leverage": mlm_score,
            "peer_gap": peer_score,
            "area_liquidity": liq_score,
            "rental_yield": yield_score,
            "area_appreciation": apprec_score,
        },
        "days_on_market": dom_days,
        "listed_at": _listed,
        # "מוערך" רק כשתאריך התמונות אכן הוסיף ותק מעבר למה שראינו בעצמנו.
        # כשהוא שווה ל-‎first_seen‎ (אין תמונות, או נראתה ביום ההעלאה) אין מה
        # לסייג — הוותק הוא תצפית שלנו, לא הערכה.
        "dom_estimated": bool(_listed and first_seen
                              and str(_listed) < str(first_seen)),
        "stale_no_drop": stale_no_drop,
        "drop_pct": drop_pct,
        "net_gap_pct": net_gap,
        "acquisition_costs": costs,
        "asset_risk": asset_risk,
        "asset_risk_reasons": risk_hits,
        "size_anomaly": size_anomaly,
        "sqm_per_room": round(spr, 1) if spr else None,
        # השוואת תאומות — נשמר בפלט כדי שהכרטיס והמייל יוכלו להציג
        # "זולה ב-X% מ-N דירות זהות ברחוב" ולא רק ציון.
        "peer_level": listing.get("peer_level"),
        "peer_area": listing.get("peer_area"),
        "peer_count": listing.get("peer_count"),
        "peer_median_ppm": listing.get("peer_median_ppm"),
        "peer_gap_pct": listing.get("peer_gap_pct"),
        "peer_vs_next_pct": listing.get("peer_vs_next_pct"),
        "peer_cheapest": listing.get("peer_cheapest"),
        "peer_new_60d": listing.get("peer_new_60d"),
        "peer_lone_dropper": listing.get("peer_lone_dropper"),
        "peer_droppers": listing.get("peer_droppers"),
        "repeat_drops": listing.get("repeat_drops"),
        "peer_note": listing.get("peer_note"),
        "street_status": street.get("street_status"),
        "street_gap_pct": street.get("street_gap_pct"),
        "street_peers": street.get("street_peers"),
        "street_note": street.get("street_note"),
        "can_crown": (comp_for_tier or {}).get("can_crown") if stage == "final" else None,
        "crown_block_reason": (comp or {}).get("crown_block_reason") if stage == "final" else None,
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
        "opportunity_keywords", "area_liquidity",
        # מחיר למשתכן נגזר מקובץ מקומי ומ-lat/lon שכבר בידינו — אפס בקשות,
        # ולכן הוא בר-השגה כבר בשלב א' וחייב להיספר בנרמול השער. בלעדיו
        # השער היה נדרש מכל מודעה אחוז גבוה יותר מהסולם וחוסם את הצינור.
        "mlm_leverage",
        # השוואת התאומות נגזרת כולה מהמודעות שכבר נסרקו — גם היא זמינה
        # בשלב א' ובעלות אפס, ולכן נספרת בנרמול מאותו נימוק.
        "peer_gap")))


def prescreen_gate(cfg, scale=1.0):
    """
    הסף לשלב הסינון המקדים.

    למה לא פשוט "ציון ≥ סף"? כי בשלב א' הבסיס הוא מחירי מבוקש, ובשלב ב'
    עסקאות אמיתיות — ומודעה יכולה לקבל 62 מול עמיתיה ו-80 מול עסקאות
    שנסגרו בפועל. לכן פותחים את השער במרווח מתחת לסף (prescreen_margin),
    כדי לא לפספס בדיוק את המקרים שהשלב השני נועד לגלות.

    ובנוסף — השער **מנורמל לסולם שבאמת זמין בשלב א'**. תשואה ועליית ערך
    שוות יחד 19 נקודות שאי אפשר לצבור לפני שליפת הנתונים הרשמיים, ולכן
    שער "מוחלט" של 55 היה למעשה דורש אחוז גבוה בהרבה מהניקוד האפשרי
    והיה חוסם כמעט את כל הצינור. הנרמול שומר על הכוונה המקורית.

    ‎scale‎ הוא ‎score_scale‎ של המודעה. **זה לא פרמטר נוח אלא תיקון באג
    שנמדד:** ‎score_listing‎ מנרמל את הציון כלפי מעלה למודעה שאין לה
    קבוצת תאומות, והשער חייב לחיות על אותו סולם. בלי זה שני הנרמולים
    לא מתחברים — נמדד על 1,254 מודעות: השער עלה מ-42.4 ל-44.5 לכולם,
    בעוד ש-67% מהמודעות לא יכולות להגיע ל-14 הנקודות שהעלו אותו, ומספר
    המודעות שעברו צנח מ-40 ל-10. התיקון: החלק הלא-זמין בשלב א' נמדד
    **אחרי** ההכפלה בסולם, בדיוק כמו הציון עצמו.
    """
    raw = max(0.0, float(cfg["alert_threshold"])
              - float(cfg.get("prescreen_margin", 15)))
    w = cfg["weights"]
    total = float(sum(w.values())) or 100.0
    unreachable = float(total - prescreen_reachable_max(cfg))
    try:
        scale = float(scale or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    frac = (100.0 - unreachable * scale) / 100.0
    if frac <= 0:
        return raw
    return round(raw * frac, 2)
