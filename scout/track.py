"""
מצב מעקב — בודק רק את המודעות שמעניינות, דרך דף המודעה של כל אחת.

**למה זה קיים.** הסריקה המקורית מביאה את דפי החיפוש של כל הערים, 30
קרדיטים לעיר, ~1,560 לריצה. היא הדרך הנכונה לגלות מלאי חדש, אבל היא
הדרך הגרועה לעקוב אחרי מלאי קיים, משתי סיבות:

  1. **עלות.** 1,560 קרדיטים ליום כדי לגלות שהדירות שמעניינות אותנו לא
     זזו. המעקב בודק 100–150 מודעות בקרדיט אחד כל אחת.
  2. **אי-ודאות.** בסריקת חיפוש, מודעה שנמחקה ומודעה שפשוט לא הגיעה
     לעמוד הראשון נראות **זהות**. לכן המערכת נאלצה להמתין
     ‎inactive_after_days‎ (14 יום) לפני שסימנה מודעה כלא-פעילה, ובפועל
     נפתחו קישורים מתים. נמדד על 60 מודעות מול יד2 החי: 0% מתות כשנראו
     היום, 7% אחרי 1–3 ימים, 13% אחרי 4–7, 20% אחרי 8–14.
     דף מודעה בודד מחזיר 404 — תשובה חד-משמעית.

**חלוקת העבודה:**
    גיוס (‎scan‎)   — שבועי. סורק ערים, מביא מלאי חדש.
    מעקב (‎track‎)  — יומי. בודק את קבוצת המעקב, מודעה-מודעה.

מי בקבוצת המעקב נקבע ב-‎scout/tracklist.py‎ — מאושרות ידנית קודם,
אחריהן מחיר למשתכן בוודאות גבוהה, מובילות התאומות, והמוכתרות.

**הזהירות המכוונת סביב "נמחקה":** סימון שגוי מוציא דירה חיה מהרשימה,
וזו השגיאה היקרה יותר — קישור מת מבזבז דקה, דירה שנעלמה מהרשימה
מבזבזת את ההזדמנות. לכן נדרשות ‎track_dead_confirmations‎ בדיקות רצופות
עם 404 (ברירת מחדל 2) לפני שמודעה מסומנת כנמחקה. במעקב יומי זה אומר
שקישור מת נעלם בתוך יומיים, במקום שבועיים.

הרצה:
    python -m scout.track              — מעקב מלא
    python -m scout.track --dry-run    — בלי מייל
    python -m scout.track --limit 20   — תקרה לבדיקה
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import date
from pathlib import Path

from . import db, peers, tracklist
from .config import load_config
from .scraperapi import CreditBudgetExceeded, ScraperApiClient
from .yad2 import check_listing

log = logging.getLogger(__name__)

DEFAULTS = {
    "track_dead_confirmations": 2,
    "track_max_credits": 300,
    "track_workers": 4,
}


def _cfg(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _load_pool(cfg):
    """
    קורא את המאגר המצטבר מ-‎out/latest.json‎.

    למה משם ולא מה-DB: ‎latest.json‎ הוא התמונה שכבר נושאת את כל האותות
    — תאומות, מחיר למשתכן, דירוג, אישור ידני — ואלה בדיוק הקריטריונים
    שקובעים מי במעקב. ה-DB מחזיק את השורות הגולמיות בלי האותות האלה.
    """
    p = Path(cfg["paths"]["out"]) / "latest.json"
    if not p.exists():
        log.error("אין %s — צריך להריץ גיוס (scan) לפני שיש אחרי מה לעקוב", p)
        return [], None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        log.error("latest.json פגום:\n%s", traceback.format_exc())
        return [], None
    return (data.get("listings") or []), data


def _refresh_signals(pool, cfg, conn, today):
    """
    מרענן את האותות שקובעים מי במעקב, לפני הבחירה.

    הכרחי: האישור הידני מגיע מקובץ שהמשתמש העלה **אחרי** הגיוס האחרון,
    ולכן בלי הריענון הזה דירה שאישרת היום לא תיכנס למעקב עד הגיוס הבא
    — כלומר עד שבוע.
    """
    try:
        manual = tracklist.load(cfg)
        n_conf, n_rej = tracklist.apply(pool, manual, cfg)
        log.info("אישור ידני: %d מאושרות, %d נפסלות (הקובץ עודכן %s)",
                 n_conf, n_rej, manual.get("updated") or "—")
    except Exception:
        log.error("כשל בטעינת אישורים ידניים:\n%s", traceback.format_exc())
    try:
        drops = db.price_drop_history(conn)
        peers.annotate(pool, cfg, drops=drops, today=today)
    except Exception:
        log.error("כשל ברענון התאומות:\n%s", traceback.format_exc())


def run(cfg, dry_run=False, limit=None, today=None):
    """
    ריצת מעקב אחת. מחזיר dict עם מה שקרה.

    אינה סורקת ערים ואינה פונה לנדל"ן ממשלתי — רק דפי מודעות.
    """
    today = today or date.today()
    day = today.isoformat()
    report = {"checked": 0, "alive": 0, "dead": 0, "dying": 0, "unknown": 0,
              "price_changes": [], "credits": 0, "tracked": 0, "kinds": {}}

    pool, data = _load_pool(cfg)
    if not pool:
        return report

    conn = db.connect(Path(cfg["paths"]["db"]))
    try:
        _refresh_signals(pool, cfg, conn, today)
        selected = tracklist.select(pool, cfg)
        report["tracked"], report["kinds"] = tracklist.summary(pool, cfg)
        if limit:
            selected = selected[: int(limit)]
        log.info("קבוצת המעקב: %d מודעות — %s", len(selected),
                 ", ".join(f"{k} {v}" for k, v in report["kinds"].items() if v))
        if not selected:
            log.warning("אין מודעות במעקב. אשר דירות באתר, או הרץ גיוס.")
            return report

        key = (cfg.get("_scraperapi_key") or "").strip()
        if not key:
            import os
            key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not key:
            log.error("חסר SCRAPERAPI_KEY — אין איך לבדוק מודעות")
            return report

        client = ScraperApiClient(
            key,
            country_code=cfg.get("yad2_country_code", "il"),
            timeout=cfg.get("scraperapi_timeout", 90),
            retries=cfg.get("scraperapi_retries", 3),
            max_credits=int(_cfg(cfg, "track_max_credits")),
            ultra_premium=bool(cfg.get("scraperapi_ultra_premium")),
        )
        dead_needed = int(_cfg(cfg, "track_dead_confirmations"))

        for l in selected:
            lid = l.get("id")
            try:
                res = check_listing(client, l)
            except CreditBudgetExceeded:
                log.warning("נגמרה מכסת הקרדיטים למעקב אחרי %d מודעות — "
                            "עוצר כאן ושומר את מה שנבדק",
                            report["checked"])
                break
            except Exception:
                log.debug("בדיקה נכשלה ל-%s:\n%s", lid, traceback.format_exc())
                res = {"alive": None}

            report["checked"] += 1
            try:
                out = db.record_check(conn, lid, res, today=day,
                                      dead_needed=dead_needed)
            except Exception:
                log.error("כשל בכתיבת תוצאת בדיקה ל-%s:\n%s", lid,
                          traceback.format_exc())
                continue

            state = out["state"]
            report[state if state in report else "unknown"] = \
                report.get(state, 0) + 1
            if out.get("old_price"):
                report["price_changes"].append({
                    "id": lid, "city": l.get("city"),
                    "hood": l.get("hood") or l.get("neighborhood"),
                    "rooms": l.get("rooms"), "url": l.get("url"),
                    "old": out["old_price"], "new": out["new_price"],
                    "pct": round(100.0 * (out["old_price"] - out["new_price"])
                                 / out["old_price"], 2),
                    "confirmed": bool(l.get("mlm_confirmed")),
                })
                log.info("שינוי מחיר: %s %s — %s ← %s",
                         l.get("city"), l.get("hood") or "",
                         f"{out['new_price']:,.0f}", f"{out['old_price']:,.0f}")
            elif state == "dead":
                log.info("נמחקה מיד2 (אושר ב-%d בדיקות): %s %s", dead_needed,
                         l.get("city"), l.get("hood") or "")
        conn.commit()
        report["credits"] = client.credits_used
    finally:
        conn.close()

    # ── עדכון המאגר המצטבר ──
    # בלי זה האתר ימשיך להציג מחיר ישן ומודעות מתות עד הגיוס הבא.
    if data is not None:
        try:
            _apply_to_pool(cfg, data, report, day)
        except Exception:
            log.error("כשל בעדכון latest.json:\n%s", traceback.format_exc())

    log.info("מעקב: נבדקו %d — %d חיות, %d נמחקו, %d בדרך, %d לא נבדקו | "
             "%d שינויי מחיר | %d קרדיטים",
             report["checked"], report.get("alive", 0), report.get("dead", 0),
             report.get("dying", 0), report.get("unknown", 0),
             len(report["price_changes"]), report["credits"])
    return report


def _apply_to_pool(cfg, data, report, day):
    """כותב את תוצאות המעקב חזרה ל-latest.json ולדף האתר."""
    out_dir = Path(cfg["paths"]["out"])
    conn = db.connect(Path(cfg["paths"]["db"]))
    try:
        alive = {r["id"] for r in conn.execute(
            "SELECT id FROM listings WHERE active=1")}
        checked = {r["id"]: r["last_checked"] for r in conn.execute(
            "SELECT id, last_checked FROM listings WHERE last_checked IS NOT NULL")}
        prices = {r["id"]: r["price"] for r in conn.execute(
            "SELECT id, price FROM listings")}
    finally:
        conn.close()

    kept = []
    removed = 0
    for x in data.get("listings") or []:
        lid = x.get("id")
        if lid not in alive:
            removed += 1
            continue
        if lid in checked:
            x["last_checked"] = checked[lid]
        p = prices.get(lid)
        if p and x.get("price") and float(p) != float(x["price"]):
            x["price"] = float(p)
            if x.get("size"):
                x["ppm"] = round(float(p) / float(x["size"]))
            elif x.get("size_sqm"):
                x["price_per_sqm"] = round(float(p) / float(x["size_sqm"]))
        kept.append(x)
    data["listings"] = kept
    data["tracked_at"] = day
    data["track_report"] = {k: v for k, v in report.items()
                            if k != "price_changes"}
    (out_dir / "latest.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log.info("latest.json: הוסרו %d מודעות שנמחקו, נשארו %d", removed,
             len(kept))

    try:
        from . import webapp
        webapp.write(out_dir, cfg)
    except Exception:
        log.error("כשל בבניית דף האתר:\n%s", traceback.format_exc())


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='מעקב אחרי קבוצת המודעות הנבחרת (בלי סריקת ערים)')
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="בלי שליחת מייל")
    ap.add_argument("--limit", type=int, default=None,
                    help="לבדוק רק N מודעות (לבדיקות)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    from .main import setup_logging
    setup_logging(cfg, args.verbose)
    rep = run(cfg, dry_run=args.dry_run, limit=args.limit)
    print(f'מעקב: נבדקו {rep["checked"]}, '
          f'{rep.get("dead", 0)} נמחקו, '
          f'{len(rep["price_changes"])} שינויי מחיר, '
          f'{rep["credits"]} קרדיטים')
    return 0


if __name__ == "__main__":
    sys.exit(main())
