"""שכבת התמדה — SQLite עם מעקב היסטוריית מחירים ומצב התראות."""
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

from .pricing import drop_pct, is_drop_sane

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    city          TEXT,
    url           TEXT,
    rooms         REAL,
    size_sqm      REAL,
    price         REAL,
    price_per_sqm REAL,
    first_seen    TEXT,
    last_seen     TEXT,
    active        INTEGER DEFAULT 1,
    raw_json      TEXT,
    neighborhood  TEXT,
    street        TEXT,
    property_type TEXT,
    condition_text TEXT,
    lat           REAL,
    lon           REAL,
    -- מצב התראות: מונע שליחה חוזרת של אותה מודעה בכל ריצה
    first_alerted_at   TEXT,
    last_alerted_score REAL,
    last_alerted_price REAL,
    last_alerted_at    TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    price       REAL,
    seen_at     TEXT,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);
CREATE INDEX IF NOT EXISTS idx_hist_listing ON price_history(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_city ON listings(city);

-- חציוני עסקאות רשות המיסים לכל ריצה (לצורך היסטוריה והשוואת אזורים)
CREATE TABLE IF NOT EXISTS city_stats (
    city            TEXT NOT NULL,
    run_date        TEXT NOT NULL,
    setl_code       INTEGER,
    median_ppsqm    REAL,
    deals_12m       INTEGER,
    population      INTEGER,
    raw_json        TEXT,
    PRIMARY KEY (city, run_date)
);

-- חציוני ₪/מ"ר שנתיים לכל אזור — הבסיס לעליית ערך (CAGR) ולגרף המגמה
CREATE TABLE IF NOT EXISTS area_history (
    area_key     TEXT NOT NULL,       -- 'settlement:9000' / 'neighborhood:65210105'
    year         INTEGER NOT NULL,
    area_level   TEXT,
    city         TEXT,
    area_name    TEXT,
    median_ppm   REAL,
    median_price REAL,
    deal_quarters INTEGER,            -- רבעונים עם עסקאות מפורסמות באותה שנה
    data_version TEXT,
    run_date     TEXT,
    PRIMARY KEY (area_key, year)
);
CREATE INDEX IF NOT EXISTS idx_area_hist_city ON area_history(city);
"""


# עמודות שנוספו אחרי הגרסה הראשונה — נוספות ל-DB קיים ב-migrate
ADDED_COLUMNS = {
    "neighborhood": "TEXT",
    "street": "TEXT",
    "property_type": "TEXT",
    "condition_text": "TEXT",
    "lat": "REAL",
    "lon": "REAL",
    "first_alerted_at": "TEXT",
    "last_alerted_score": "REAL",
    "last_alerted_price": "REAL",
    "last_alerted_at": "TEXT",
}


def _migrate(conn):
    """מוסיף עמודות חסרות ל-DB שנוצר בגרסה קודמת (SQLite בלי IF NOT EXISTS)."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    for col, coltype in ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {coltype}")
            log.info("DB: נוספה עמודה %s", col)


def connect(db_path):
    """פותח חיבור ומייצר סכימה אם צריך."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def upsert_listing(conn, listing, today=None):
    """
    Upsert של מודעה. מחזיר dict עם:
      is_new       — מודעה שלא נראתה קודם
      old_price    — המחיר הקודם אם השתנה, אחרת None
      first_seen   — תאריך הופעה ראשונה (לחישוב ותק)
    כשמחיר משתנה — נרשמת שורה ב-price_history.
    """
    today = today or date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    lid = listing["id"]

    row = conn.execute(
        "SELECT price, first_seen FROM listings WHERE id = ?", (lid,)
    ).fetchone()

    price = listing.get("price")
    result = {"is_new": row is None, "old_price": None, "first_seen": today}

    if row is None:
        conn.execute(
            """INSERT INTO listings
               (id, source, city, url, rooms, size_sqm, price, price_per_sqm,
                first_seen, last_seen, active, raw_json,
                neighborhood, street, property_type, condition_text, lat, lon)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)""",
            (lid, listing.get("source"), listing.get("city"), listing.get("url"),
             listing.get("rooms"), listing.get("size_sqm"), price,
             listing.get("price_per_sqm"), today, now,
             json.dumps(listing.get("raw", {}), ensure_ascii=False),
             listing.get("neighborhood"), listing.get("street"),
             listing.get("property_type"), listing.get("condition_text"),
             listing.get("lat"), listing.get("lon")),
        )
        # שורת בסיס בהיסטוריה כדי שנוכל להשוות בעתיד
        conn.execute(
            "INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)",
            (lid, price, today),
        )
    else:
        result["first_seen"] = row["first_seen"] or today
        old_price = row["price"]
        # רישום שינוי מחיר בלבד (לא כל ריצה) — כך ההיסטוריה נשארת רזה ומדויקת
        if price is not None and old_price is not None and float(price) != float(old_price):
            result["old_price"] = float(old_price)
            conn.execute(
                "INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)",
                (lid, price, today),
            )
        conn.execute(
            """UPDATE listings SET city=?, url=?, rooms=?, size_sqm=?, price=?,
                      price_per_sqm=?, last_seen=?, active=1, raw_json=?,
                      neighborhood=?, street=?, property_type=?, condition_text=?,
                      lat=?, lon=?
               WHERE id=?""",
            (listing.get("city"), listing.get("url"), listing.get("rooms"),
             listing.get("size_sqm"), price, listing.get("price_per_sqm"), now,
             json.dumps(listing.get("raw", {}), ensure_ascii=False),
             listing.get("neighborhood"), listing.get("street"),
             listing.get("property_type"), listing.get("condition_text"),
             listing.get("lat"), listing.get("lon"), lid),
        )
    return result


def mark_inactive_stale(conn, source, days=14, today=None):
    """
    מסמן כלא-פעילות מודעות שלא נראו כבר `days` ימים.

    למה לפי ותק ולא "כל מה שלא נראה בריצה הזו": הסריקה מוגבלת למספר
    עמודים לעיר (yad2_max_pages_per_city), ולכן מודעה קיימת לגמרי עשויה
    פשוט לא להופיע בעמודים שנמשכו. סימון לפי ותק מונע השבתה שגויה.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    cur = conn.execute(
        "UPDATE listings SET active=0 WHERE source=? AND active=1 AND "
        "(last_seen IS NULL OR substr(last_seen,1,10) < ?)",
        (source, cutoff),
    )
    return cur.rowcount


def price_drop_history(conn, days=180, sanity_limit=None):
    """
    מחזיר ירידות מחיר: לכל מודעה, המחיר הקודם, הנוכחי ותאריך השינוי.
    משמש גם לגיליון 'מעקב ירידות מחיר' וגם לניקוד.

    האחוז מחושב אך ורק ב-pricing.drop_pct — (ישן−חדש)/ישן×100. ירידה
    שחורגת מחסם השפיות מסומנת ‎suspect=True‎ ואינה משמשת לניקוד או להתראה:
    ירידה של 70%+ היא כמעט תמיד שגיאת נתונים, לא מציאה.
    """
    rows = conn.execute(
        """SELECT h.listing_id, h.price, h.seen_at,
                  l.city, l.url, l.rooms, l.size_sqm, l.price AS current_price
           FROM price_history h JOIN listings l ON l.id = h.listing_id
           ORDER BY h.listing_id, h.seen_at, h.id"""
    ).fetchall()

    by_listing = {}
    for r in rows:
        by_listing.setdefault(r["listing_id"], []).append(r)

    drops = []
    for lid, hist in by_listing.items():
        if len(hist) < 2:
            continue
        for prev, cur in zip(hist, hist[1:]):
            if prev["price"] is None or cur["price"] is None:
                continue
            if float(cur["price"]) < float(prev["price"]):
                pct = drop_pct(prev["price"], cur["price"])
                sane = is_drop_sane(pct) if sanity_limit is None else \
                    is_drop_sane(pct, sanity_limit)
                if not sane:
                    log.warning("ירידת מחיר חריגה במודעה %s: %s → %s (%.1f%%) — "
                                "מסומנת כשגיאת נתונים ולא תיצור התראה",
                                lid, prev["price"], cur["price"], pct or 0.0)
                drops.append({
                    "listing_id": lid,
                    "city": cur["city"],
                    "url": cur["url"],
                    "rooms": cur["rooms"],
                    "size_sqm": cur["size_sqm"],
                    "old_price": float(prev["price"]),
                    "new_price": float(cur["price"]),
                    "changed_at": cur["seen_at"],
                    "drop_pct": pct,
                    "suspect": not sane,
                })
    return drops


def price_ladder(conn, source=None, sanity_limit=None, max_points=6):
    """
    "סולם המחירים" של כל מודעה — התמונה המצטברת, לא רק הירידה האחרונה.

    למה זה נחוץ: ‎price_drop_history‎ מחזיר **אירועי** שינוי. דירה שירדה
    שלוש פעמים (900,000 → 888,888 → 850,000) מופיעה שם כשלוש שורות נפרדות,
    ואף אחת מהן לא אומרת את הדבר החשוב — שהמוכר ירד 5.6% בסך הכול ושהוא
    ממשיך לרדת. הפונקציה הזו מחזירה שורה אחת לכל מודעה עם התמונה המלאה.

    מחזיר {listing_id: {
        original_price   — המחיר הראשון שנרשם אי פעם
        current_price    — המחיר הנוכחי (מטבלת listings)
        total_drop_pct   — (מקורי − נוכחי)/מקורי × 100, חיובי = ירידה
        num_drops        — מספר הירידות הנפרדות (לא כולל עליות)
        num_rises        — עליות מחיר (נדיר, אבל קורה — ומעיד על תיקון)
        last_drop_pct / last_change_at — הירידה האחרונה ומתי
        points           — [{price, seen_at}] מהישן לחדש
        history_text     — "900,000 → 888,888 → 850,000" עם תאריכים
        suspect          — ירידה כוללת/בודדת חריגה = כנראה שגיאת נתונים
    }}
    """
    q = ("""SELECT h.listing_id, h.price, h.seen_at, l.price AS current_price,
                   l.source
            FROM price_history h JOIN listings l ON l.id = h.listing_id"""
         + (" WHERE l.source = ?" if source else "")
         + " ORDER BY h.listing_id, h.seen_at, h.id")
    rows = conn.execute(q, (source,) if source else ()).fetchall()

    by_listing = {}
    for r in rows:
        by_listing.setdefault(r["listing_id"], []).append(r)

    out = {}
    for lid, hist in by_listing.items():
        points = [{"price": float(r["price"]), "seen_at": r["seen_at"]}
                  for r in hist if r["price"] is not None]
        if not points:
            continue
        current = hist[-1]["current_price"]
        current = float(current) if current is not None else points[-1]["price"]
        # המחיר הנוכחי בטבלת listings הוא מקור האמת; אם משום מה הוא לא
        # נרשם בהיסטוריה (למשל DB מגרסה ישנה) — מוסיפים אותו כנקודה אחרונה
        if abs(points[-1]["price"] - current) > 0.5:
            points.append({"price": current, "seen_at": hist[-1]["seen_at"]})

        original = points[0]["price"]
        drops, rises, last_pct, last_at = 0, 0, None, None
        worst_step = 0.0
        for prev, cur in zip(points, points[1:]):
            if cur["price"] < prev["price"]:
                drops += 1
                last_pct = drop_pct(prev["price"], cur["price"])
                last_at = cur["seen_at"]
                worst_step = max(worst_step, last_pct or 0.0)
            elif cur["price"] > prev["price"]:
                rises += 1

        total = drop_pct(original, current)
        sane = (is_drop_sane(total, sanity_limit) if sanity_limit is not None
                else is_drop_sane(total))
        sane_step = (is_drop_sane(worst_step, sanity_limit) if sanity_limit is not None
                     else is_drop_sane(worst_step))

        out[lid] = {
            "listing_id": lid,
            "original_price": original,
            "current_price": current,
            "total_drop_pct": total if (total or 0) > 0 else 0.0,
            "num_drops": drops,
            "num_rises": rises,
            "num_points": len(points),
            "last_drop_pct": last_pct,
            "last_change_at": last_at,
            "first_seen_price_at": points[0]["seen_at"],
            "points": points,
            "history_text": _history_text(points, max_points),
            "suspect": not (sane and sane_step),
        }
    return out


def _history_text(points, max_points=6):
    """
    "900,000 (30/07) → 850,000 (12/08)" — קריא בשורת אקסל אחת.

    מסומן ב-LRM כי המחרוזת מעורבת (מספרים ותאריכים בתוך הקשר עברי RTL);
    בלי הסימון סדר החצים מתהפך בתצוגה והרצף נראה הפוך מהאמת.
    """
    pts = list(points or [])
    if not pts:
        return ""
    if len(pts) > max_points:                 # שומרים את הראשון והאחרונים
        pts = pts[:1] + [{"price": None, "seen_at": None}] + pts[-(max_points - 1):]

    bits = []
    for p in pts:
        if p["price"] is None:
            bits.append("…")
            continue
        day = ""
        if p.get("seen_at"):
            iso = str(p["seen_at"])[:10]
            parts = iso.split("-")
            day = f" ({parts[2]}/{parts[1]})" if len(parts) == 3 else f" ({iso})"
        bits.append(f"{p['price']:,.0f}{day}")
    return "‎" + " → ".join(bits)


def load_stored_listings(conn, source=None, active_only=True):
    """
    המודעות ששמורות ב-DB, כמודל הפנימי — הבסיס לריצה בלי סריקה חדשה.

    למה: הסריקה של יד2 יקרה ולא אמינה (Radware), אבל המודעות שכבר נאספו
    הן נתון אמיתי לכל דבר. בלי זה ריצה שבה יד2 חסום מפיקה פלט ריק, וזה
    בדיוק מה שהמוצר הזה אמור למנוע. ‎raw_json‎ נשמר כדי שאפשר יהיה לשחזר
    את המודעה במלואה (תגיות, טקסט) דרך ‎yad2.from_stored‎.
    """
    q = "SELECT * FROM listings WHERE 1=1"
    params = []
    if active_only:
        q += " AND active = 1"
    if source:
        q += " AND source = ?"
        params.append(source)

    out = []
    for r in conn.execute(q, params):
        row = dict(r)
        try:
            row["raw"] = json.loads(row.get("raw_json") or "{}")
        except (ValueError, TypeError):
            row["raw"] = {}
        out.append(row)
    return out


def latest_drop_for(conn, listing_id):
    """הירידה האחרונה של מודעה מסוימת (dict או None) — לשימוש בניקוד."""
    drops = [d for d in price_drop_history(conn) if d["listing_id"] == listing_id]
    return max(drops, key=lambda d: d["changed_at"]) if drops else None


def save_city_stats(conn, city, stats, today=None):
    today = today or date.today().isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO city_stats
           (city, run_date, setl_code, median_ppsqm, deals_12m, population, raw_json)
           VALUES (?,?,?,?,?,?,?)""",
        (city, today, stats.get("setl_code"), stats.get("median_ppsqm"),
         stats.get("deals_12m"), stats.get("population"),
         json.dumps(stats, ensure_ascii=False, default=str)),
    )


def save_area_history(conn, area_key, rows, today=None):
    """
    שומר חציוני ₪/מ"ר שנתיים לאזור. rows = רשימת dicts עם year/median_ppm/...
    INSERT OR REPLACE — הנתונים הרשמיים מתעדכנים, השורה האחרונה גוברת.
    """
    today = today or date.today().isoformat()
    for r in rows or []:
        if r.get("year") is None:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO area_history
               (area_key, year, area_level, city, area_name, median_ppm,
                median_price, deal_quarters, data_version, run_date)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (area_key, int(r["year"]), r.get("area_level"), r.get("city"),
             r.get("area_name"), r.get("median_ppm"), r.get("median_price"),
             r.get("deal_quarters"), r.get("data_version"), today),
        )


def alert_state(conn, listing_ids):
    """
    מצב ההתראות הקודם למודעות נתונות.
    מחזיר {listing_id: {first_alerted_at, last_alerted_score,
                        last_alerted_price, last_alerted_at}}.
    """
    out = {}
    ids = [i for i in (listing_ids or []) if i]
    for i in range(0, len(ids), 400):          # SQLite מגביל פרמטרים לשאילתה
        chunk = ids[i:i + 400]
        q = ("SELECT id, first_alerted_at, last_alerted_score, last_alerted_price,"
             " last_alerted_at FROM listings WHERE id IN (%s)"
             % ",".join("?" * len(chunk)))
        for r in conn.execute(q, chunk):
            out[r["id"]] = {
                "first_alerted_at": r["first_alerted_at"],
                "last_alerted_score": r["last_alerted_score"],
                "last_alerted_price": r["last_alerted_price"],
                "last_alerted_at": r["last_alerted_at"],
            }
    return out


def record_alerts(conn, alerted, today=None):
    """
    רושם שהמודעות האלה נשלחו בהתראה. alerted = רשימת dicts עם id/score/price.
    first_alerted_at נכתב פעם אחת בלבד (COALESCE) — הוא "מתי דיווחנו לראשונה".
    """
    today = today or date.today().isoformat()
    for a in alerted or []:
        conn.execute(
            """UPDATE listings
               SET first_alerted_at = COALESCE(first_alerted_at, ?),
                   last_alerted_score = ?,
                   last_alerted_price = ?,
                   last_alerted_at = ?
               WHERE id = ?""",
            (today, a.get("score"), a.get("price"), today, a.get("id")),
        )


def count_active(conn, city=None):
    if city:
        return conn.execute(
            "SELECT COUNT(*) FROM listings WHERE active=1 AND city=?", (city,)
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM listings WHERE active=1").fetchone()[0]
