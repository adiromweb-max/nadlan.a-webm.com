"""
האישור הידני וקבוצת המעקב.

שני דברים שקשורים זה לזה:

**1. אישור ידני.** הזיהוי המרחבי של מחיר למשתכן (`scout/mlm.py`) הוא
הטוב שאפשר להשיג אוטומטית — נקודה בתוך פוליגון רשמי, בתוספת מצב הנכס
כראיה שלישית. אבל הוא עדיין הסקה. הדבר היחיד שחזק ממנו הוא **בן אדם
שפתח את המודעה, בדק, והכריע.** לכן יש דרגת ודאות אחת מעל "גבוהה":
`מאומת ידנית`. היא באה מ-`data/mlm_confirmed.json`, קובץ שהמשתמש מייצר
מהאתר ומעלה לריפו.

למה קובץ בריפו ולא מסד נתונים: האתר הוא דף סטטי ב-GitHub Pages ואין לו
שרת. חלופה אחת היא לכתוב ישירות ל-Supabase, אבל אז מפתח כתיבה צריך לשבת
בדף ציבורי. קובץ שעובר דרך גיט לא דורש שום סוד, הוא מתועד בהיסטוריה
(רואים מתי אושרה כל דירה ומי שינה מה), והוא עולה למשתמש העלאה אחת
לכמה ימים.

**2. קבוצת המעקב.** אחרי שהמלאי נאסף, אין טעם לסרוק את יד2 מחדש כל יום:
זה 1,560 קרדיטים ליום על ערים שלמות, רק כדי לגלות שהמודעות שמעניינות
אותנו לא זזו. במקום זה בודקים **רק את המודעות שמעניינות** — כל אחת דרך
דף המודעה שלה, בקרדיט אחד. הגיוס (סריקת הערים) רץ שבועי.

מי נכנס לקבוצת המעקב, לפי הסדר:
  * מה שאושר ידנית — תמיד, בלי תנאי. זו הרשימה שהמשתמש בנה בעצמו.
  * מחיר למשתכן משוחרר בוודאות גבוהה.
  * מובילות התאומות — זולות משמעותית מדירות זהות, או היחידות שהורידו.
  * מודעות שהמערכת הכתירה.
ומי לא: מה שנפסל ידנית, ומה שנפסל אוטומטית (`disqualified`).

התקרה (`track_max_listings`) קיימת כדי שהמעקב לא יגדל בשקט חזרה לעלות
של סריקה מלאה.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CONFIRMED_FILE = "mlm_confirmed.json"

# דרגת הוודאות שגוברת על הזיהוי המרחבי. מופיעה בכל מקום שבו מוצגת
# ‎mlm_confidence‎, ולכן היא מחרוזת ולא דגל נפרד.
CONF_MANUAL = "מאומת ידנית"

# ── הצינור האישי ──
# סטטוס המו"מ של המשתמש מול כל דירה. זה **לא** אותו דבר כמו ✓/✕:
# אלה אומרים "זו מחיר למשתכן / זו לא", כלומר **זיהוי**; הסטטוס אומר
# "איפה אני עומד מולה", כלומר **תהליך**. דירה יכולה להיות מחיר למשתכן
# מאומת ועדיין פסולה כי המוכר בלתי-אפשרי, ולהיפך.
STATUS_NEW = "חדשה"
STATUS_CALLED = "התקשרתי"
STATUS_VISIT = "קבעתי ביקור"
STATUS_NEGOTIATING = "במו\"מ"
STATUS_DROPPED = "פסולה"
STATUSES = (STATUS_NEW, STATUS_CALLED, STATUS_VISIT,
            STATUS_NEGOTIATING, STATUS_DROPPED)
# סטטוסים שמעידים שהמשתמש כבר עובד על הדירה. הם עולים לראש המעקב:
# דירה שהוא התקשר עליה חשובה יותר מדירה שהמנוע רק דירג גבוה.
ACTIVE_STATUSES = (STATUS_CALLED, STATUS_VISIT, STATUS_NEGOTIATING)

DEFAULTS = {
    "track_max_listings": 150,
    "track_include_high_conf": True,
    "track_include_peer_leaders": True,
    "track_include_crowned": True,
    "track_peer_gap_min_pct": 8.0,
}


def _cfg(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _data_dir(cfg):
    db = (cfg or {}).get("paths", {}).get("db")
    if db:
        return Path(db).parent
    return Path("data")


def load(cfg=None, path=None):
    """
    קורא את קובץ האישורים. מחזיר ‎{"confirmed": set, "rejected": set,
    "notes": {id: str}, "updated": str}‎.

    קובץ חסר אינו שגיאה — פשוט אין עדיין אישורים. קובץ פגום **כן** נרשם
    כשגיאה בלוג, כי בשקט הוא היה מאפס את כל העבודה הידנית של המשתמש.
    """
    out = {"confirmed": set(), "rejected": set(), "notes": {}, "crm": {},
           "updated": None}
    p = Path(path) if path else _data_dir(cfg) / CONFIRMED_FILE
    if not p.exists():
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        log.error("קובץ האישורים הידניים %s פגום — מתעלם ממנו בריצה הזו", p)
        return out
    if not isinstance(raw, dict):
        log.error("קובץ האישורים הידניים %s אינו אובייקט — מתעלם", p)
        return out
    for key in ("confirmed", "rejected"):
        vals = raw.get(key) or []
        if isinstance(vals, dict):          # סובלנות לפורמט {id: true}
            vals = [k for k, v in vals.items() if v]
        out[key] = {str(v) for v in vals if v}
    notes = raw.get("notes") or {}
    if isinstance(notes, dict):
        out["notes"] = {str(k): str(v) for k, v in notes.items() if v}
    # הצינור האישי. סטטוס שאינו ברשימה המוכרת נזרק בשקט — עדיף לאבד
    # ערך פגום מלהכניס סטטוס שאף מסך לא יודע להציג.
    crm = raw.get("crm") or {}
    if isinstance(crm, dict):
        for k, v in crm.items():
            if not isinstance(v, dict):
                continue
            st = str(v.get("status") or "").strip()
            entry = {}
            if st in STATUSES:
                entry["status"] = st
            if v.get("note"):
                entry["note"] = str(v["note"])
            if v.get("reason"):
                entry["reason"] = str(v["reason"])
            if v.get("updated"):
                entry["updated"] = str(v["updated"])
            if entry:
                out["crm"][str(k)] = entry
    out["updated"] = raw.get("updated")
    # מזהה שמופיע בשתי הרשימות — הפסילה גוברת. אחרת האתר היה יכול
    # להשאיר מודעה "מאושרת" אחרי שהמשתמש פסל אותה, וזו הטעות המטרידה
    # מהשתיים כי היא מחזירה למעקב דירה שהוא כבר דחה.
    both = out["confirmed"] & out["rejected"]
    if both:
        out["confirmed"] -= both
        log.warning("%d מודעות מופיעות גם כמאושרות וגם כנפסלות — "
                    "הפסילה גוברת", len(both))
    return out


def apply(listings, manual, cfg=None):
    """
    מחיל את האישור הידני על המודעות. מחזיר (מאושרות, נפסלות) שנמצאו.

    אישור ידני **אינו** מוסיף כרית ואינו ממציא נתון שלא היה: הוא מעלה
    את הוודאות ומסמן את המודעה כמאושרת. אם הזיהוי המרחבי לא מצא אותה
    בפוליגון בכלל, ‎mlm_confirmed‎ נשאר True אבל אין כרית — וזה בדיוק
    המצב שבו המשתמש ראה בעיניו שזו דירת מחיר למשתכן שהשכבה פספסה.
    """
    conf, rej = manual.get("confirmed") or set(), manual.get("rejected") or set()
    notes = manual.get("notes") or {}
    crm = manual.get("crm") or {}
    n_conf = n_rej = 0
    for l in listings or []:
        lid = str(l.get("id"))
        # הצינור מוחל על כל מודעה, גם כזו שלא אושרה כמחיר למשתכן:
        # אפשר להיות במו"מ על דירה שאינה מחיר למשתכן בכלל.
        entry = crm.get(lid)
        if entry:
            l["crm_status"] = entry.get("status")
            l["crm_note"] = entry.get("note")
            l["crm_reason"] = entry.get("reason")
            l["crm_updated"] = entry.get("updated")
            if entry.get("status") == STATUS_DROPPED:
                l["crm_dropped"] = True
        if lid in rej:
            l["mlm_rejected"] = True
            l["manual_note"] = notes.get(lid) or l.get("manual_note")
            n_rej += 1
            continue
        if lid in conf:
            l["mlm_confirmed"] = True
            l["mlm"] = True
            # המגבלה פגה — אחרת אין טעם באישור: לפני הפקיעה המכירה
            # כרוכה בהשבת הסבסוד והמוכר דווקא פחות גמיש.
            l["mlm_released"] = True
            l["mlm_confidence"] = CONF_MANUAL
            l["manual_note"] = notes.get(lid) or l.get("manual_note")
            n_conf += 1
    return n_conf, n_rej


def is_confirmed(listing):
    return bool((listing or {}).get("mlm_confirmed"))


def select(listings, cfg=None):
    """
    בוחר את קבוצת המעקב. מחזיר רשימת מודעות, מסודרת לפי עדיפות.

    הסדר חשוב כי התקרה חותכת מהסוף: מה שאושר ידנית לא ייחתך לעולם.
    """
    cfg = cfg or {}
    gap_min = float(_cfg(cfg, "track_peer_gap_min_pct"))
    buckets = {"active": [], "manual": [], "mlm": [], "peers": [], "crowned": []}
    for l in listings or []:
        # דירה שפסלת בצינור יוצאת מהמעקב, בדיוק כמו דירה שפסלת כזיהוי.
        if l.get("mlm_rejected") or l.get("disqualified") or l.get("crm_dropped"):
            continue
        # דירה שאתה כבר עובד עליה קודמת לכול — גם למאושרות.
        if l.get("crm_status") in ACTIVE_STATUSES:
            buckets["active"].append(l)
        elif l.get("mlm_confirmed"):
            buckets["manual"].append(l)
        elif (_cfg(cfg, "track_include_high_conf")
                and l.get("mlm_released")
                and l.get("mlm_confidence") == "גבוהה"):
            buckets["mlm"].append(l)
        elif (_cfg(cfg, "track_include_peer_leaders")
                and ((l.get("peer_gap_pct") or 0) >= gap_min
                     or l.get("peer_lone_dropper")
                     or (l.get("repeat_drops") or 0) >= 2)):
            buckets["peers"].append(l)
        elif _cfg(cfg, "track_include_crowned") and l.get("tier"):
            buckets["crowned"].append(l)

    def rank(l):
        return (l.get("peer_gap_pct") or 0) + (l.get("score") or 0) / 10.0

    ordered = (buckets["active"]
               + buckets["manual"]
               + sorted(buckets["mlm"], key=lambda l: -(l.get("mlm_cushion") or 0))
               + sorted(buckets["peers"], key=lambda l: -rank(l))
               + sorted(buckets["crowned"], key=lambda l: -(l.get("score") or 0)))
    cap = int(_cfg(cfg, "track_max_listings"))
    if cap > 0 and len(ordered) > cap:
        log.warning("קבוצת המעקב היא %d מודעות, נחתכת ל-%d "
                    "(track_max_listings). המאושרות ידנית אינן נחתכות.",
                    len(ordered), cap)
        ordered = ordered[:cap]
    return ordered


def summary(listings, cfg=None):
    """שורה אחת ללוג ולדו"ח: מי במעקב ולמה."""
    sel = select(listings, cfg)
    kinds = {"בטיפול שלך": 0, "מאושרות ידנית": 0,
             "מחיר למשתכן בוודאות גבוהה": 0, "מובילות תאומות": 0,
             "מוכתרות": 0}
    gap_min = float(_cfg(cfg, "track_peer_gap_min_pct"))
    for l in sel:
        if l.get("crm_status") in ACTIVE_STATUSES:
            kinds["בטיפול שלך"] += 1
        elif l.get("mlm_confirmed"):
            kinds["מאושרות ידנית"] += 1
        elif l.get("mlm_released") and l.get("mlm_confidence") == "גבוהה":
            kinds["מחיר למשתכן בוודאות גבוהה"] += 1
        elif ((l.get("peer_gap_pct") or 0) >= gap_min
                or l.get("peer_lone_dropper")
                or (l.get("repeat_drops") or 0) >= 2):
            kinds["מובילות תאומות"] += 1
        else:
            kinds["מוכתרות"] += 1
    return len(sel), kinds


def write_template(path, confirmed=None, rejected=None, notes=None,
                   updated=None, crm=None):
    """
    כותב קובץ אישורים. משמש ליצירת קובץ ריק בפעם הראשונה ולבדיקות;
    בשימוש רגיל הקובץ נוצר באתר ומועלה לגיט.
    """
    data = {
        "_": "נוצר מהאתר (טאב 'מאומת על ידי' → ייצוא). מעלים לגיט ב-"
             "data/mlm_confirmed.json. המנוע קורא אותו ומסמן את הדירות "
             "האלה בוודאות 'מאומת ידנית'.",
        "updated": updated,
        "confirmed": sorted(confirmed or []),
        "rejected": sorted(rejected or []),
        "notes": notes or {},
        "crm": crm or {},
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return data
