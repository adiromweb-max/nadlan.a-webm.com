"""
השוואת תאומות — "אותו מתחם, אותו מספר חדרים, אותו גודל".

למה זה מודול נפרד ולא עוד בסיס השוואה:
    ‎comps.py‎ משווה מול **עסקאות שנסגרו** באזור — הנתון הכי אמין שיש,
    אבל הוא מגיע מנדל"ן ממשלתי בקטגוריות חדרים ברמת שכונה, בפיגור של
    חודשים, ובלי מ"ר מדויק. הוא עונה על "מה שווה דירה כזאת באזור".

    המודול הזה עונה על שאלה אחרת לגמרי: **מבין הדירות הזהות שמוצעות
    עכשיו באותו רחוב — איזו היא הזולה, ובכמה.** זו השאלה של קונה שעומד
    לבחור בין שבע מודעות של 3 חדרים באותו מתחם מחיר למשתכן.

    הנתון כאן הוא מחירי **מבוקש** ולא עסקאות, ולכן הוא חלש כהערכת שווי —
    אבל חזק בדיוק במה ש-comps חלש: רמת רחוב, זמן אמת, וגודל אמיתי במ"ר.
    שני המספרים חיים זה לצד זה ואין ביניהם סתירה.

    ‎comps.street_check‎ עושה בדיקה דומה ברמת רחוב, אבל כ**וטו** בלבד
    (האם הפער מול העסקאות מתאדה מול הרחוב), היא רצה רק על מי שעבר את
    השער, ואין בה סינון גודל. כאן ההשוואה היא אות חיובי בזכות עצמו,
    רצה על כל המודעות, ומנוקה לגודל.

ארבעה אותות, כולם בעלות אפס בקשות:

  1. ‎peer_gap_pct‎ — כמה ₪/מ"ר של המודעה מתחת לחציון התאומות שלה.
  2. ‎peer_new_60d‎ — כמה מודעות נוספו לאותה קבוצה בחודשיים האחרונים.
     מוכר אחד הוא מוכר; שבעה מוכרים של אותה דירה הם קונה אחד שמחליט.
  3. ‎peer_lone_dropper‎ — המודעה היחידה בקבוצה שהורידה מחיר לאחרונה.
     מי שנשבר ראשון הוא הלחוץ, והירידה שלו היא גם עוגן מול השכנים.
  4. ‎repeat_drops‎ — מוכר שהוריד מחיר פעמיים ומעלה. מי שכבר בתנועה
     למטה ימשיך לרדת במו"מ; מי שלא זז פעם אחת צריך סיבה להתחיל.

סינון הגודל הוא העיקר כאן. 3 חדרים ב-62 מ"ר ו-3 חדרים ב-95 מ"ר אינן
אותה דירה, וחציון משותף שלהן אינו בסיס השוואה. לכן הקבוצה נחתכת
ל-‎peer_size_tolerance_pct‎ סביב הגודל החציוני שלה, וקבוצה שנשארת קטנה
מדי אחרי החיתוך פשוט לא מייצרת אות.

רמת ההשוואה היא רחוב, ובנפילה חזרה — שכונה. שכונה דורשת יותר חברים,
כי היא מערבבת בניינים ומיקומים.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

LEVEL_STREET = "רחוב"
LEVEL_HOOD = "שכונה"

# ברירות מחדל — נדרסות מ-config.yaml
DEFAULTS = {
    "peer_min_street": 3,
    "peer_min_hood": 4,
    "peer_size_tolerance_pct": 15.0,
    "peer_gap_full_at_pct": 20.0,
    "peer_gap_min_pct": 8.0,
    "peer_gap_suspect_pct": 35.0,
    "peer_supply_window_days": 60,
    "peer_min_new_for_signal": 3,
    "peer_min_group_for_signal": 5,
    "peer_drop_window_days": 60,
}


def _cfg(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _as_date(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _rooms_key(listing):
    """מספר החדרים כמפתח קבוצה — התאמה מדויקת, לא טווח.

    בכוונה אין כאן סובלנות ±0.5 כמו ב-comps: שם משווים מול קטגוריות של
    נתון רשמי ואי אפשר אחרת, וכאן המטרה היא תאומות ממש. 3 ו-3.5 חדרים
    הן שתי דירות שונות אצל אותו קבלן, והחציון המשותף שלהן מטשטש בדיוק
    את ההבדל שאנחנו מחפשים.
    """
    try:
        r = float(listing.get("rooms"))
    except (TypeError, ValueError):
        return None
    return round(r * 2) / 2 if r > 0 else None


def _ppm(listing):
    try:
        p = float(listing.get("price_per_sqm") or 0)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def _size(listing):
    try:
        s = float(listing.get("size_sqm") or 0)
    except (TypeError, ValueError):
        return None
    return s if 20 <= s <= 400 else None


def _group_keys(listing):
    """(מפתח רחוב, מפתח שכונה) — כל מפתח שאי אפשר לבנות חוזר None."""
    city, rooms = listing.get("city"), _rooms_key(listing)
    if not city or rooms is None:
        return None, None
    # ‎str()‎ ולא ‎or ""‎ לבדו: ב-‎latest.json‎ המצטבר השדות האלה עוברים
    # דרך JSON ו-SQLite, ורחוב שהוא מספר ("12") חוזר כמספר ולא כמחרוזת.
    street = str(listing.get("street") or "").strip()
    hood = str(listing.get("neighborhood") or "").strip()
    return ((city, street, rooms) if street else None,
            (city, hood, rooms) if hood else None)


def _drop_events(drops, sane_only=True):
    """{listing_id: [(תאריך, אחוז), ...]} מרשימת הירידות של db."""
    out = {}
    for d in drops or []:
        if sane_only and d.get("suspect"):
            continue
        lid = d.get("listing_id")
        day = _as_date(d.get("seen_at") or d.get("changed_at"))
        pct = d.get("drop_pct")
        if lid is None or pct is None:
            continue
        out.setdefault(lid, []).append((day, float(pct)))
    for v in out.values():
        v.sort(key=lambda x: (x[0] or date.min))
    return out


def _build_groups(listings, cfg):
    """בונה את שתי רמות הקבוצות, כבר אחרי סינון הגודל."""
    tol = float(_cfg(cfg, "peer_size_tolerance_pct")) / 100.0
    raw = {LEVEL_STREET: {}, LEVEL_HOOD: {}}
    for l in listings or []:
        if _ppm(l) is None or _size(l) is None:
            continue
        sk, hk = _group_keys(l)
        if sk:
            raw[LEVEL_STREET].setdefault(sk, []).append(l)
        if hk:
            raw[LEVEL_HOOD].setdefault(hk, []).append(l)

    minimums = {LEVEL_STREET: int(_cfg(cfg, "peer_min_street")),
                LEVEL_HOOD: int(_cfg(cfg, "peer_min_hood"))}
    out = {LEVEL_STREET: {}, LEVEL_HOOD: {}}
    for level, buckets in raw.items():
        need = minimums[level]
        for key, members in buckets.items():
            if len(members) < need:
                continue
            med_size = statistics.median([_size(m) for m in members])
            twins = [m for m in members
                     if (1 - tol) * med_size <= _size(m) <= (1 + tol) * med_size]
            if len(twins) < need:
                continue
            out[level][key] = twins
    return out


def _supply(members, today, window_days):
    """(כמה עכשיו, כמה נוספו בחלון) — לפי ותק השוק האמיתי."""
    cutoff = today - timedelta(days=int(window_days))
    new = 0
    for m in members:
        started = _as_date(m.get("listed_at") or m.get("first_seen"))
        # בלי תאריך אי אפשר לדעת, ולכן לא נספר כחדש — האות הזה מעדיף
        # להחמיץ על פני לנפח.
        if started and started > cutoff:
            new += 1
    return len(members), new


def describe(listing, groups, drop_events, cfg, today=None):
    """
    האות של מודעה אחת מול התאומות שלה, או None כשאין קבוצה.

    הרמה הראשונה שיש לה קבוצה תקפה היא הקובעת — רחוב לפני שכונה.
    """
    today = today or date.today()
    ppm = _ppm(listing)
    if ppm is None:
        return None

    sk, hk = _group_keys(listing)
    chosen = None
    for level, key in ((LEVEL_STREET, sk), (LEVEL_HOOD, hk)):
        if key and key in (groups.get(level) or {}):
            members = groups[level][key]
            if any(m.get("id") == listing.get("id") for m in members):
                chosen = (level, key, members)
                break
    if not chosen:
        return None

    level, key, members = chosen
    others = [m for m in members if m.get("id") != listing.get("id")]
    if not others:
        return None

    peer_ppms = [_ppm(m) for m in others]
    peer_ppms = sorted(p for p in peer_ppms if p)
    if not peer_ppms:
        return None

    median_ppm = statistics.median(peer_ppms + [ppm])
    gap = 100.0 * (median_ppm - ppm) / median_ppm if median_ppm else None
    # הפער מול הזולה הבאה — שומר מפני מודעה בודדת שנראית מצוינת רק
    # משום שהיא לבד בתחתית. פער גדול מול החציון אך אפסי מול השנייה
    # פירושו שתי מודעות זולות, כלומר מחיר שוק ולא מציאה.
    vs_next = (100.0 * (peer_ppms[0] - ppm) / peer_ppms[0]
               if peer_ppms[0] else None)

    now, new = _supply(members, today, _cfg(cfg, "peer_supply_window_days"))

    # ── מי הוריד מחיר בקבוצה ──
    win = int(_cfg(cfg, "peer_drop_window_days"))
    cutoff = today - timedelta(days=win)
    recent = []
    for m in members:
        ev = drop_events.get(m.get("id")) or []
        if any(d and d >= cutoff for d, _ in ev):
            recent.append(m.get("id"))
    mine = drop_events.get(listing.get("id")) or []
    my_downs = len(mine)
    lone = bool(listing.get("id") in recent and len(recent) == 1 and now >= 3)

    # האם בכלל אפשר לדבר על "גדילה": אם **כל** חברי הקבוצה נכנסו בתוך
    # החלון, אין קבוצת בסיס להשוות אליה. זה קורה הרבה כי היסטוריית ה-DB
    # קצרה מהשוק ו-‎listed_at‎ נופל חזרה ל-‎first_seen‎ — ובלי התנאי הזה
    # 19% מכל המודעות היו מקבלות "ההיצע גדל", אות שלא אומר כלום.
    cohort_known = (now - new) >= 1
    where = key[1] or ""
    return {
        "peer_level": level,
        "peer_area": where,
        "peer_count": now,
        "peer_median_ppm": round(median_ppm, 2) if median_ppm else None,
        "peer_gap_pct": round(gap, 2) if gap is not None else None,
        "peer_vs_next_pct": round(vs_next, 2) if vs_next is not None else None,
        "peer_cheapest": bool(gap is not None and ppm <= peer_ppms[0]),
        "peer_new_60d": new,
        "peer_cohort_known": cohort_known,
        "peer_lone_dropper": lone,
        "peer_droppers": len(recent),
        "repeat_drops": my_downs,
        "peer_note": _note(level, where, now, gap, new if cohort_known else 0,
                           lone, my_downs),
    }


def _note(level, where, count, gap, new, lone, downs):
    """משפט אחד בעברית פשוטה — זה מה שמופיע בכרטיס ובמייל."""
    head = f'{count} דירות זהות ב{where}' if where else f"{count} דירות זהות"
    parts = [head + f' ({level})']
    if gap is not None:
        if gap >= 1:
            parts.append(f'הדירה הזאת זולה ב-{gap:.0f}% מהחציון שלהן')
        elif gap <= -1:
            parts.append(f'הדירה הזאת יקרה ב-{abs(gap):.0f}% מהחציון שלהן')
        else:
            parts.append("המחיר בדיוק על החציון שלהן")
    if new >= 2:
        parts.append(f'{new} מהן נוספו בחודשיים האחרונים')
    if lone:
        parts.append("והיא היחידה בקבוצה שהורידה מחיר")
    elif downs >= 2:
        parts.append(f'המוכר הוריד מחיר {downs} פעמים')
    return ". ".join(parts) + "."


def annotate(listings, cfg, drops=None, today=None):
    """
    מחשב את האות לכל המודעות ומעדכן אותן במקום. מחזיר כמה קיבלו אות.

    עלות: אפס בקשות. הכול נגזר מהמודעות שכבר נסרקו ומהיסטוריית המחירים
    שכבר ב-DB.
    """
    today = today or date.today()
    groups = _build_groups(listings, cfg)
    events = _drop_events(drops)
    tagged = 0
    for l in listings or []:
        try:
            info = describe(l, groups, events, cfg, today)
        except Exception:
            log.debug("כשל בהשוואת תאומות למודעה %s", l.get("id"), exc_info=True)
            info = None
        if info:
            l.update(info)
            tagged += 1
    return tagged


def score(listing, weight, cfg=None):
    """
    ניקוד ‎peer_gap‎ — 0..weight.

    הסקאלה הדוקה מזו של ‎price_gap‎ (20% מול 25%) בכוונה: פער של 20%
    מול **דירות זהות באותו רחוב** הוא קיצוני, בעוד שאותו פער מול חציון
    עירוני רב-גודל הוא שגרתי.

    פער שחורג מ-‎peer_gap_suspect_pct‎ אינו מתוגמל במלואו. בין תאומות
    אמיתיות הפרש של 35%+ כמעט תמיד אומר שמשהו בדירה שונה — קומה, מצב,
    כיוון, חזית מול עורף — ולא שהמוכר טועה במחיר.
    """
    cfg = cfg or {}
    w = float(weight or 0)
    if w <= 0:
        return 0.0
    gap = listing.get("peer_gap_pct")
    if gap is None or gap <= 0:
        return 0.0
    full_at = float(_cfg(cfg, "peer_gap_full_at_pct"))
    if full_at <= 0:
        return 0.0
    val = min(1.0, gap / full_at) * w
    if gap > float(_cfg(cfg, "peer_gap_suspect_pct")):
        val *= float(cfg.get("suspect_gap_score_factor", 0.5))
    return round(val, 2)


def signals(listing, cfg=None):
    """האותות התומכים שהמודול הזה מספק, כרשימת משפטים בעברית."""
    cfg = cfg or {}
    out = []
    gap = listing.get("peer_gap_pct")
    count = listing.get("peer_count") or 0
    area = listing.get("peer_area") or ""
    if (gap is not None and gap >= float(_cfg(cfg, "peer_gap_min_pct"))
            and count >= 3):
        out.append(f'זולה ב-{gap:.0f}% מ-{count - 1} דירות זהות ב{area}'.strip())
    new = listing.get("peer_new_60d") or 0
    # ‎peer_cohort_known‎ הוא התנאי שהופך "נוספו 8 מתוך 8" ל"אין מה לומר".
    if (count >= int(_cfg(cfg, "peer_min_group_for_signal"))
            and listing.get("peer_cohort_known")
            and new >= int(_cfg(cfg, "peer_min_new_for_signal"))):
        out.append(f'{new} דירות זהות נוספו ב{area} בחודשיים'.strip())
    if listing.get("peer_lone_dropper"):
        out.append(f'היחידה מתוך {count} שהורידה מחיר')
    elif (listing.get("repeat_drops") or 0) >= 2:
        out.append(f'המוכר הוריד מחיר {listing["repeat_drops"]} פעמים')
    return out
