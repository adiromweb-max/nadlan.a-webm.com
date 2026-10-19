"""
מחיר למשתכן — זיהוי מרחבי של מודעות בתוך פרויקטים, וכרית המשא ומתן.

הרעיון
------
דירת מחיר למשתכן נמכרה לבעליה במחיר מסובסד, הרבה מתחת לשוק. על המכירה
מוטלת מגבלה: אי אפשר למכור לפני שחלפו 5 שנים מטופס 4 **או** 7 שנים
מההגרלה, לפי המוקדם. כשהמגבלה פגה, הבעלים משוחרר למכור — ושלושה דברים
נובעים מזה, וכולם ניתנים למדידה:

1. **כרית מו"מ.** מי שקנה ב-640 אלף ומבקש מיליון יושב על 360 אלף רווח.
   הוא יכול להתפשר על המחיר בלי להפסיד, בשונה ממוכר שקנה במחיר שוק.
   זה ההבדל בין מוכר שיכול לרדת למוכר שלא יכול.
2. **לחץ היצע מקומי.** כשמגבלה פגה על מתחם שלם, הרבה דירות זהות יכולות
   להגיע לשוק יחד. הן מתחרות זו בזו, לא מול העיר כולה.
3. **תמחור ייחוס.** המחיר המסובסד המקורי ידוע, ולכן אפשר לראות כמה
   המוכר מבקש מעליו — נתון שאין לשום מודעה אחרת.

למה ההצלבה גאוגרפית ולא לפי שם
--------------------------------
שמות השכונות של משרד הבינוי והשיכון ושל יד2 הם שתי מערכות שונות. בירוחם
יד2 קורא לשכונה "אירוס" ומשרד השיכון קורא לה "צפונית" — אפס חפיפה בטקסט.
בבאר שבע משרד השיכון אומר "שכונת הפארק" ו"סיגליות" ויד2 אומר "שכונה א'"
ו"שכונה ב'". נמדד בפועל: התאמת שמות הצליחה ב-28 מקרים ונכשלה ב-53, ואף
ייצרה זיווגים שגויים ("סיגליות" ל"שכונה ג'", כי האות ג' מופיעה במילה).

לכן הזיהוי כאן הוא **נקודה בתוך פוליגון** על קואורדינטות המודעה, מול
שכבת ה-GIS הרשמית. אפס שמות, אפס ניחוש. ל-100% ממודעות יד2 יש lat/lon.

מגבלות הנתון — לקרוא לפני שמסתמכים
------------------------------------
* ``release`` הוא 7 שנים מההגרלה: **החסם העליון בחוק**. המגבלה פוקעת גם
  5 שנים מטופס 4 לפי המוקדם, ותאריך טופס 4 אינו במאגר. לכן התאריך כאן
  שמרני — דירות רבות שוחררו לפני כן.
* השכבה מכסה 98,475 יח"ד מתוך 162,563 שבמרשם ההגרלות: לא לכל הגרלה יש
  פוליגון. מודעה שלא זוהתה אינה הוכחה שאינה מחיר למשתכן.
* ``cushion`` מחושב כמחיר מבוקש פחות (מחיר מסובסד למ"ר × גודל). הוא
  **אינו** כולל חניה, מחסן, שדרוגים ומע"מ, ולכן העלות האמיתית של המוכר
  גבוהה יותר והכרית בפועל צרה מהמוצג. זה אומדן לדירוג, לא מספר למו"מ.
* פוליגון מזהה מתחם, לא דירה. דירה בבניין ותיק בתוך גבולות המתחם תזוהה
  בטעות כמחיר למשתכן.

מקור: משרד הבינוי והשיכון, data.gov.il — שכבות GIS מחיר למשתכן, ומאגר
"מעקב אחר הגרלות דירה בהנחה".
"""

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

LAYER = Path(__file__).resolve().parent.parent / "data" / "mlm_projects.geojson"
LOTS = Path(__file__).resolve().parent.parent / "data" / "mlm_lots.json"

# דירה ותיקה שגודלה חריג לא תקבל כרית אבסורדית מהכפלה בגודל שגוי
MAX_SANE_SQM = 400.0


def _rings(geom):
    """כל הטבעות החיצוניות של Polygon/MultiPolygon, כרשימות נקודות."""
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [p[0] for p in c if p]
    return []


def _bbox(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def _in_ring(lon, lat, ring):
    """ray casting — נקודה בתוך מצולע."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x:
                inside = not inside
        j = i
    return inside


class Projects:
    """
    שכבת הפרויקטים, נטענת פעם אחת לריצה.

    אין כאן תלות ב-geopandas/shapely בכוונה: 190 מתחמים מול כמה אלפי
    מודעות הם חישוב זניח בפייתון טהור, וכל תלות נוספת היא עוד דבר
    שיכול להיכשל בהתקנה ב-GitHub Actions ולהפיל ריצה שלמה.
    """

    def __init__(self, path=None):
        self.features = []
        p = Path(path or LAYER)
        if not p.exists():
            log.warning("mlm: שכבת הפרויקטים לא נמצאה (%s) — הזיהוי מושבת", p)
            return
        try:
            fc = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            log.warning("mlm: שכבת הפרויקטים לא נקראה (%s) — הזיהוי מושבת", e)
            return
        for f in fc.get("features", []):
            rings = _rings(f.get("geometry") or {})
            if not rings:
                continue
            self.features.append((f.get("properties") or {}, rings, _bbox(rings)))
        log.info("mlm: נטענו %d מתחמי מחיר למשתכן", len(self.features))

    def __bool__(self):
        return bool(self.features)

    def find(self, lat, lon):
        """המתחם שהנקודה נופלת בתוכו, או None."""
        if lat is None or lon is None or not self.features:
            return None
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        for props, rings, (x0, y0, x1, y1) in self.features:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue            # חסם מלבני — מדלג על הרוב המכריע
            if any(_in_ring(lon, lat, r) for r in rings):
                return props
        return None


class Lots:
    """
    שכבת המגרשים — הראיה השנייה, ובלתי-תלויה בשכבת המתחמים.

    למה צריך אותה: פוליגון מתחם הוא שטח של ~9,250 מ"ר בחציון, והוא מכיל
    גם בניינים ותיקים שקדמו לפרויקט. מגרש הוא ~600 מ"ר — קטן פי 15 — ונושא
    את מספר יחידות הדיור המתוכננות בו. נקודה בתוך מגרש עם יח"ד היא ראיה
    חזקה בהרבה מנקודה בתוך גבולות המתחם.

    נמדד על 1,102 מודעות: 34 נמצאות בשתי השכבות, 13 רק במתחם (חשודות
    כזיהוי שגוי) ו-8 רק במגרש (שהמתחם פספס). ההסכמה בין השתיים היא מדד
    הוודאות, ולא עוד פילטר.

    יש כאן 11,756 מגרשים, ולכן נבנה אינדקס רשת: בלעדיו כל מודעה הייתה
    נבדקת מול כל מגרש, וריצה ארצית הייתה נמשכת דקות.
    """

    CELL = 0.01        # ~1.1 ק"מ — תא רשת

    def __init__(self, path=None):
        self.cells = {}
        self.n = 0
        p = Path(path or LOTS)
        if not p.exists():
            log.warning("mlm: שכבת המגרשים לא נמצאה (%s) — הוודאות תיקבע בלעדיה", p)
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            log.warning("mlm: שכבת המגרשים לא נקראה (%s)", e)
            return
        for lot in data.get("lots", []):
            rings = lot.get("r") or []
            if not rings:
                continue
            x0, y0, x1, y1 = _bbox(rings)
            entry = (lot.get("u") or 0, rings, (x0, y0, x1, y1))
            self.n += 1
            for cx in range(int(x0 / self.CELL), int(x1 / self.CELL) + 1):
                for cy in range(int(y0 / self.CELL), int(y1 / self.CELL) + 1):
                    self.cells.setdefault((cx, cy), []).append(entry)
        log.info("mlm: נטענו %d מגרשים עם יח\"ד", self.n)

    def __bool__(self):
        return bool(self.n)

    def find(self, lat, lon):
        """המגרש שהנקודה נופלת בתוכו, או None."""
        if lat is None or lon is None or not self.n:
            return None
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        bucket = self.cells.get((int(lon / self.CELL), int(lat / self.CELL)))
        if not bucket:
            return None
        for units, rings, (x0, y0, x1, y1) in bucket:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if any(_in_ring(lon, lat, r) for r in rings):
                return {"units": units}
        return None


def confidence(in_project, in_lot, condition_id):
    """
    עד כמה בטוח שזו באמת דירת מחיר למשתכן, משלוש ראיות בלתי-תלויות:
    פוליגון המתחם, מגרש עם יח"ד, ומצב הנכס שיד2 מדווח.

    למה זה קריטי ולא קוסמטי: כל הטענה היא שגל השחרור הוא מה שמרכך את
    המחירים באזור. אם חלק מהמודעות שזוהו אינן מחיר למשתכן בכלל, האות
    מתפרק — זה הופך ל"אזור מוצף משום מה". לכן הוודאות מוצגת לצד כל מודעה
    במקום להיבלע בציון.

    "חדש (גרו בנכס)" (קוד 6) הוא הראיה השלישית, והיא חזקה: 97% מהמודעות
    ששתי שכבות ה-GIS מסכימות עליהן נושאות אותה, מול 15% בכל השאר.

    מחזיר 'גבוהה' / 'בינונית' / 'נמוכה', או None אם אינה בפרויקט כלל.
    """
    if not (in_project or in_lot):
        return None
    score = (2 if (in_project and in_lot) else 1)
    if condition_id in (1, 6):
        score += 1
    return {3: "גבוהה", 2: "בינונית", 1: "נמוכה"}[score]


def describe(listing, projects, today=None, lots=None):
    """
    מה שידוע על מודעה מבחינת מחיר למשתכן. מחזיר dict ריק אם אינה בפרויקט.

    השדות:
      mlm              — True כשהמודעה בתוך מתחם מזוהה
      mlm_project      — שם המתחם אצל משרד השיכון
      mlm_units        — יח"ד במתחם
      mlm_orig_ppm     — המחיר המסובסד למ"ר בהגרלה
      mlm_release      — התאריך שבו המגבלה פגה בוודאות (שמרני)
      mlm_released     — האם כבר פגה
      mlm_paid_est     — אומדן מה שהמוכר שילם (ראה המגבלות בראש הקובץ)
      mlm_cushion      — כרית המו"מ בשקלים
      mlm_cushion_pct  — הכרית כאחוז מהמחיר המבוקש
      mlm_confidence   — 'גבוהה'/'בינונית'/'נמוכה' (ראה confidence())
      mlm_in_lot       — נמצאת בתוך מגרש עם יח"ד מתוכננות
      mlm_lot_units    — יח"ד באותו מגרש
    """
    today = today or date.today()
    lat, lon = listing.get("lat"), listing.get("lon")
    p = projects.find(lat, lon) if projects else None
    lot = lots.find(lat, lon) if lots else None
    if not p and not lot:
        return {}

    p = p or {}
    out = {
        "mlm": True,
        "mlm_project": p.get("project"),
        "mlm_units": p.get("units"),
        "mlm_orig_ppm": p.get("orig_ppm"),
        "mlm_release": p.get("release"),
        "mlm_released": None,
        "mlm_in_lot": bool(lot),
        "mlm_lot_units": (lot or {}).get("units"),
        "mlm_confidence": confidence(bool(p), bool(lot),
                                     listing.get("condition_id")),
    }
    rel = p.get("release")
    if rel:
        try:
            out["mlm_released"] = date.fromisoformat(rel) <= today
        except ValueError:
            pass

    price = listing.get("price")
    size = listing.get("size_sqm")
    ppm = p.get("orig_ppm")
    if price and size and ppm and 0 < float(size) <= MAX_SANE_SQM:
        paid = float(ppm) * float(size)
        cushion = float(price) - paid
        if cushion > 0:
            out["mlm_paid_est"] = round(paid)
            out["mlm_cushion"] = round(cushion)
            out["mlm_cushion_pct"] = round(cushion / float(price) * 100.0, 1)
    return out


def supply_pressure(listings):
    """
    כמה דירות מאותו מתחם עומדות למכירה באותו רגע.

    זה הגרסה המקומית של "המגבלה פגה והשוק הוצף": לא ממוצע עירוני, שמטשטש
    שכונה בודדת, אלא התחרות בפועל בין דירות זהות. נמדד על הנתונים החיים:
    ברוב המתחמים פחות מ-1.2%, ובירוחם צפונית 4.1% — פי שלושה מכולם.

    מוסיף ‎mlm_on_sale‎ ו-‎mlm_supply_pct‎ לכל מודעה במקום. הערך הוא רצפה
    ולא תקרה: הסריקה מוגבלת בעמודים לעיר ולכן סופרת בחסר.
    """
    buckets = {}
    for s in listings:
        key = s.get("mlm_project")
        if s.get("mlm") and key:
            buckets.setdefault((s.get("city"), key), []).append(s)
    for (_city, _proj), group in buckets.items():
        n = len(group)
        units = group[0].get("mlm_units") or 0
        pct = round(n / units * 100.0, 2) if units else None
        for s in group:
            s["mlm_on_sale"] = n
            s["mlm_supply_pct"] = pct
    return listings
