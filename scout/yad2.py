"""
מקור המודעות — יד2, דרך ScraperAPI.

איך זה עובד (נבדק בפועל, אוגוסט 2026):
  * ה-API הפנימי (gw.yad2.co.il) חסום גם דרך ScraperAPI — מחזיר דף אתגר
    של Radware. לכן אנחנו מושכים את **דף החיפוש ה-HTML** ומפרסרים את
    ‎<script id="__NEXT_DATA__">‎ שבתוכו — זהו ה-state שה-SPA (Next.js) מקבל
    מהשרת, והוא מכיל את המודעות כ-JSON מלא ומובנה.
  * המסלול: ‎props.pageProps.feed‎ ובו ‎private‎ (מודעות פרטיות) ו-‎agency‎
    (מתווכים) — 20+20 מודעות לעמוד, וכן ‎pagination.{total,totalPages}‎.
  * ‎feed.yad1‎ הן מודעות פרויקטים מקבלן (יד1) — לא דירות יד שנייה, ולכן
    מסוננות החוצה.
  * קודי הערים של יד2 הם קודי היישוב של הלמ"ס — אותם קודים בדיוק שמחזיר
    אינדקס היישובים של nadlan.gov.il. לכן אין טבלת קודים קשיחה: הקוד
    מגיע מ-nadlan (ראה nadlan.resolve_city).
    **אבל** הקוד חייב להיות מרופד לארבע ספרות (‎city=0070‎ ולא ‎city=70‎),
    אחרת יד2 מחזיר דף לובי שיווקי בלי תוצאות — ראה city_param().

לכל מודעה יש ‎address.coords‎ (lat/lon) ו-‎address.neighborhood‎ — מהם נגזר
"האזור הספציפי" שלפיו שולפים עסקאות אמיתיות בשלב הסופי.
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .scraperapi import CreditBudgetExceeded
from .pricing import clean_ppm, size_is_plausible

log = logging.getLogger(__name__)

SEARCH_URL = ("https://www.yad2.co.il/realestate/forsale"
              "?city={code}&minPrice={pmin}&maxPrice={pmax}&page={page}")
ITEM_URL = "https://www.yad2.co.il/realestate/item/{token}"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

# חתימות דף חסימה — אם הן חוזרות, ScraperAPI לא הצליח לעבור
BLOCK_MARKERS = ("Radware", "Bot Manager", "captcha", "__uzdbm_", "Attention Required")

# סוגי נכס שנחשבים "דירה" לצורך הסינון (הבריף: דירות בלבד).
# בית פרטי/קוטג'/מגרש נשארים בחוץ.
APARTMENT_TYPES = ("דירה", "דירת גן", "דירת גג", "פנטהאוז", "מיני פנטהאוז",
                   "דופלקס", "סטודיו", "לופט", "יחידת דיור", "טריפלקס")

# מצב הנכס לפי id. פיד החיפוש מחזיר id בלבד; דף המודעה מחזיר גם text.
# הערכים כאן אומתו אמפירית מול דפי מודעות אמיתיים (אוגוסט 2026) — אל תנחשו
# כאן ערכים נוספים: id לא מוכר יישאר בלי תווית, והתווית האמיתית תגיע ממילא
# בשלב ההעשרה (enrich_listing) שרץ על כל המועמדות.
CONDITION_TEXT = {
    2: "משופץ",
    3: "במצב שמור",
    5: "דרוש שיפוץ",
}


class Yad2Status:
    """סטטוס המקור לדו"ח ולמייל."""

    def __init__(self):
        self.cities_attempted = 0
        self.ok_cities = []
        self.failed_cities = []
        self.empty_cities = []          # ענו כשורה אבל אין דירות בטווח
        self.pages_fetched = 0
        self.pages_failed = 0
        self.listings = 0
        self.enriched = 0
        self.budget_stopped = False
        self.last_error = None

    @property
    def blocked(self):
        # עיר שענתה כשורה בלי מודעות בטווח אינה עדות לחסימה
        return (bool(self.cities_attempted)
                and not self.ok_cities and not self.empty_cities)

    def summary_he(self):
        if not self.cities_attempted:
            return "לא נוסה"
        if self.blocked:
            return (f"נחסם דרך ScraperAPI — נוסו {self.cities_attempted} ערים, "
                    f"0 מודעות. שגיאה אחרונה: {self.last_error or 'לא ידוע'}")
        base = (f"{self.listings} מודעות מ-{len(self.ok_cities)} ערים "
                f"({self.pages_fetched} עמודים דרך ScraperAPI)")
        if self.empty_cities:
            base += f", {len(self.empty_cities)} ערים בלי מודעות בטווח"
        if self.failed_cities:
            base += f", {len(self.failed_cities)} ערים נכשלו"
        if self.enriched:
            base += f", {self.enriched} מודעות הועשרו"
        if self.budget_stopped:
            base += " (נעצר בתקרת קרדיטים)"
        return base


def _looks_blocked(html):
    head = (html or "")[:4000]
    return any(m in head for m in BLOCK_MARKERS)


def _parse_next_data(html):
    """מחלץ את ה-JSON של Next.js מתוך ה-HTML. מחזיר dict או None."""
    if not html:
        return None
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        log.debug("יד2: __NEXT_DATA__ לא ניתן לפרסור: %s", e)
        return None


def _to_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _text(node):
    """מוציא .text ממבנה {'text': ...} או מחזיר את הערך עצמו."""
    if isinstance(node, dict):
        return node.get("text")
    return node


def parse_item(item, fallback_city):
    """ממפה רשומת feed של יד2 למודל הפנימי. מחזיר dict או None."""
    if not isinstance(item, dict):
        return None
    token = item.get("token") or item.get("orderId")
    if not token:
        return None

    addr = item.get("address") or {}
    house = addr.get("house") or {}
    coords = addr.get("coords") or {}
    ad = item.get("additionalDetails") or {}
    cond = ad.get("propertyCondition") or {}
    cond_id = cond.get("id")

    price = _to_float(item.get("price"))
    size = _to_float(ad.get("squareMeter")
                     or (item.get("metaData") or {}).get("squareMeterBuild"))
    # שומר שפיות: גודל לא-סביר (1 מ"ר, המחיר בשדה הגודל) מבוטל, כדי שלא
    # ייגזר ממנו ₪/מ"ר אבסורדי ופער מפוצץ.
    size = size if size_is_plausible(size) else None
    rooms = _to_float(ad.get("roomsCount"))
    prop_type = _text(ad.get("property"))

    tags = [t.get("name") for t in (item.get("tags") or [])
            if isinstance(t, dict) and t.get("name")]

    city = _text(addr.get("city")) or fallback_city
    neighborhood = _text(addr.get("neighborhood"))
    street = _text(addr.get("street"))

    # הטקסט שעליו רץ ניקוד מילות ההזדמנות. בשלב הסריקה אין תיאור חופשי —
    # יש תגיות ("בהזדמנות", "גמיש במחיר") ומצב נכס. התיאור המלא מתווסף
    # בשלב ההעשרה, רק למועמדות.
    text_bits = tags + [prop_type, cond.get("text") or CONDITION_TEXT.get(cond_id),
                        neighborhood, street]
    free_text = " | ".join(str(t) for t in text_bits if t)

    return {
        "id": f"yad2:{token}",
        "token": str(token),
        "source": "yad2",
        "city": city,
        "neighborhood": neighborhood,
        "street": street,
        "house_number": house.get("number"),
        "floor": house.get("floor"),
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
        "url": ITEM_URL.format(token=token),
        "rooms": rooms,
        "size_sqm": size,
        "price": price,
        "price_per_sqm": clean_ppm(price, size)[0],
        "property_type": prop_type,
        "condition_id": cond_id,
        "condition_text": cond.get("text") or CONDITION_TEXT.get(cond_id),
        "ad_type": item.get("adType"),
        "tags": tags,
        "text": free_text,
        "description": None,       # מתמלא בהעשרה
        "enriched": False,
        "raw": item,
    }


def from_stored(row):
    """
    משחזר מודעה שנשמרה ב-DB למודל הפנימי המלא.

    ‎raw_json‎ הוא רשומת ה-feed המקורית של יד2, ולכן אפשר להריץ עליה שוב
    את אותו ‎parse_item‎ ולקבל את התגיות והטקסט שעליהם רץ ניקוד מילות
    ההזדמנות — מידע שאינו נשמר בעמודות. אם ה-raw חסר או פגום, נופלים על
    העמודות עצמן: פחות עשיר, אבל מספיק לניקוד מחיר, ותק וירידות.

    המחיר והגודל **תמיד** נלקחים מהעמודות, כי הן המצב המעודכן (ה-raw הוא
    צילום של הפעם הראשונה שהמודעה נראתה ובו המחיר הישן).
    """
    listing = None
    raw = row.get("raw") or {}
    if raw:
        try:
            listing = parse_item(raw, row.get("city"))
        except Exception:                       # raw פגום לא מפיל ריצה
            listing = None

    if listing is None:
        token = str(row.get("id") or "").split(":", 1)[-1]
        listing = {
            "id": row.get("id"), "token": token, "source": row.get("source"),
            "city": row.get("city"), "url": row.get("url"),
            "neighborhood": row.get("neighborhood"), "street": row.get("street"),
            "property_type": row.get("property_type"),
            "condition_text": row.get("condition_text"),
            "lat": row.get("lat"), "lon": row.get("lon"),
            "tags": [], "text": " | ".join(
                str(v) for v in (row.get("condition_text"), row.get("neighborhood"),
                                 row.get("street"), row.get("property_type")) if v),
            "description": None, "enriched": False, "raw": raw,
        }

    listing["id"] = row.get("id") or listing.get("id")
    listing["source"] = row.get("source") or listing.get("source")
    listing["url"] = row.get("url") or listing.get("url")
    listing["city"] = row.get("city") or listing.get("city")
    if row.get("neighborhood"):
        listing["neighborhood"] = row["neighborhood"]
    if row.get("condition_text"):
        listing["condition_text"] = row["condition_text"]

    price = _to_float(row.get("price"))
    size = _to_float(row.get("size_sqm"))
    size = size if size_is_plausible(size) else None
    rooms = _to_float(row.get("rooms"))
    listing["price"] = price if price is not None else listing.get("price")
    listing["size_sqm"] = size if size is not None else listing.get("size_sqm")
    listing["rooms"] = rooms if rooms is not None else listing.get("rooms")
    # ₪/מ"ר תמיד נגזר מחדש דרך שומר השפיות — לא סומכים על ערך שמור שאולי
    # חושב לפני התיקון.
    ppm, _ok = clean_ppm(listing.get("price"), listing.get("size_sqm"))
    listing["price_per_sqm"] = ppm
    listing["from_db"] = True
    listing["last_seen"] = row.get("last_seen")
    listing["first_seen"] = row.get("first_seen")
    return listing


def _is_apartment(listing, allowed=APARTMENT_TYPES):
    """דירות בלבד — לפי סוג הנכס שיד2 מדווח."""
    t = listing.get("property_type")
    if not t:
        return True          # אין מידע — לא פוסלים, הניקוד יטפל
    return any(a in str(t) for a in allowed)


def city_param(code):
    """
    קוד העיר כפי שיד2 מצפה לו ב-URL: **ארבע ספרות עם אפסים מובילים**.

    זה לא קישוט. יד2 משתמש בקודי הלמ"ס, אבל קוד קצר מוחזר כדף "לובי"
    שיווקי בלי תוצאות חיפוש: ‎city=70‎ (אשדוד) → לובי, ‎city=0070‎ → אשדוד.
    בלי הריפוד הזה כל עיר עם קוד קטן מ-1000 נופלת בשקט — אשדוד, אופקים,
    ירוחם, מצפה רמון ועומר כולן ריקות.
    """
    try:
        return f"{int(code):04d}"
    except (TypeError, ValueError):
        return str(code)


def _extract_feed(data):
    """
    מחזיר (items, pagination, is_feed_page).
    is_feed_page=False כשיד2 החזיר דף לובי במקום תוצאות חיפוש.
    """
    try:
        feed = data["props"]["pageProps"]["feed"]
    except (KeyError, TypeError):
        return [], {}, False
    if not isinstance(feed, dict):
        return [], {}, False

    items = []
    # רק מודעות יד-שנייה: private + agency. yad1 = פרויקטים מקבלן.
    for key in ("private", "agency"):
        v = feed.get(key)
        if isinstance(v, list):
            items.extend(v)
    return items, feed.get("pagination") or {}, True


def fetch_city_page(client, city, code, cfg, page):
    """
    מביא עמוד תוצאות אחד. מחזיר (listings, pagination, error).
    error=None כשהכול תקין.
    """
    url = SEARCH_URL.format(code=city_param(code), pmin=int(cfg["price_min"]),
                            pmax=int(cfg["price_max"]), page=page)
    html = client.get(url)
    if html is None:
        return [], {}, "no-response"
    if _looks_blocked(html):
        return [], {}, "blocked"

    data = _parse_next_data(html)
    if data is None:
        return [], {}, "no-next-data"

    items, pagination, is_feed = _extract_feed(data)
    if not is_feed:
        # דף לובי — הקוד לא זוהה כעיר. לא "אין תוצאות".
        return [], {}, "lobby-page"

    out = []
    for it in items:
        l = parse_item(it, city)
        if not l:
            continue
        if not _is_apartment(l, cfg.get("apartment_types") or APARTMENT_TYPES):
            continue
        p = l.get("price")
        if not p or not (cfg["price_min"] <= p <= cfg["price_max"]):
            continue
        out.append(l)

    # אימות שהקוד באמת מחזיר את העיר שביקשנו. קודי יד2 הם קודי הלמ"ס,
    # אבל טעות מיפוי הייתה מזהמת את הנתונים בשקט בעיר אחרת לגמרי
    # (למשל city=7000 מחזיר את לוד, לא את אשדוד).
    if out:
        wrong = _city_mismatch(out, city)
        if wrong:
            return [], pagination, f"city-mismatch:{wrong}"
    return out, pagination, None


def _city_mismatch(listings, expected):
    """מחזיר את שם העיר שהתקבלה בפועל אם היא אינה העיר המבוקשת, אחרת None."""
    counts = {}
    for l in listings:
        c = l.get("city")
        if c:
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return None
    modal = max(counts, key=counts.get)
    exp = str(expected).replace("קרית ", "קריית ").strip()
    got = str(modal).replace("קרית ", "קריית ").strip()
    if exp == got or exp in got or got in exp:
        return None
    return modal


def fetch_city(client, city, code, cfg, status):
    """
    מביא את כל העמודים המוגדרים לעיר אחת. לא זורק חריגות (פרט לתקרת קרדיטים).
    """
    max_pages = int(cfg.get("yad2_max_pages_per_city", 3))
    collected = {}
    total_pages = None
    total_results = None
    err = None

    for page in range(1, max_pages + 1):
        if total_pages is not None and page > total_pages:
            break
        listings, pagination, err = fetch_city_page(client, city, code, cfg, page)
        if err:
            status.pages_failed += 1
            log.warning("יד2 %s עמוד %d: %s", city, page, err)
            status.last_error = err
            # כשל בעמוד הראשון = אין טעם להמשיך בעיר הזו
            if page == 1:
                break
            continue

        status.pages_fetched += 1
        if total_pages is None:
            total_pages = pagination.get("totalPages")
            total_results = pagination.get("total")
            log.info("יד2 %s: %s מודעות בסך הכול, %s עמודים (נמשוך עד %d)",
                     city, total_results, total_pages, max_pages)
        for l in listings:
            collected[l["id"]] = l          # dedup בין עמודים
        if not listings:
            break

    out = list(collected.values())
    if out:
        status.ok_cities.append(city)
        status.listings += len(out)
        log.info("יד2 %s: %d מודעות רלוונטיות", city, len(out))
    elif not err and total_results == 0:
        # יד2 ענה כשורה, פשוט אין דירות בטווח המחירים — זו לא תקלה
        status.empty_cities.append(city)
        log.info("יד2 %s: אין מודעות בטווח המבוקש", city)
    else:
        status.failed_cities.append(city)
        log.warning("יד2 %s: 0 מודעות (%s)", city, err or "אין תוצאות")
    return out


def fetch_all(client, cities_with_codes, cfg, status=None):
    """
    סורק את כל הערים. מחזיר (listings, status).
    רץ בכמה תהליכונים (ScraperAPI מתיר 5 בו-זמנית) כדי שהריצה לא תימשך שעה.
    """
    status = status or Yad2Status()
    status.cities_attempted = len(cities_with_codes)
    workers = max(1, int(cfg.get("yad2_concurrency", 3)))
    lock = threading.Lock()
    out = []

    def work(pair):
        city, code = pair
        try:
            res = fetch_city(client, city, code, cfg, status)
        except CreditBudgetExceeded as e:
            with lock:
                status.budget_stopped = True
            log.warning("יד2 %s: %s", city, e)
            return
        except Exception as e:                      # לא מפיל את הריצה
            log.warning("יד2 %s: כשל לא צפוי: %s", city, e)
            with lock:
                status.failed_cities.append(city)
                status.last_error = str(e)
            return
        with lock:
            out.extend(res)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, cities_with_codes))

    log.info("יד2: %s", status.summary_he())
    return out, status


# ------------------------------------------------------------------
# העשרה — רק למועמדות שעברו את הסינון המקדים
# ------------------------------------------------------------------

def enrich_listing(client, listing):
    """
    מביא את דף המודעה עצמו ומוסיף תיאור חופשי + מצב נכס מדויק.
    זה משפר משמעותית את ניקוד מילות ההזדמנות (בפיד אין תיאור).
    מחזיר True אם ההעשרה הצליחה. לא זורק חריגות.
    """
    try:
        html = client.get(ITEM_URL.format(token=listing["token"]))
    except CreditBudgetExceeded:
        raise
    except Exception as e:
        log.debug("העשרה נכשלה ל-%s: %s", listing.get("id"), e)
        return False
    if html is None or _looks_blocked(html):
        return False

    data = _parse_next_data(html)
    if not data:
        return False

    node = _find_item_node(data)
    if not node:
        return False

    desc = (node.get("metaData") or {}).get("description")
    ad = node.get("additionalDetails") or {}
    cond = ad.get("propertyCondition") or {}

    if desc:
        listing["description"] = desc
        listing["text"] = (listing.get("text") or "") + " | " + str(desc)
    if cond.get("text"):
        listing["condition_text"] = cond["text"]
        listing["condition_id"] = cond.get("id", listing.get("condition_id"))
    # לפעמים דף המודעה מדויק יותר מהפיד
    size = _to_float(ad.get("squareMeter"))
    if size and size_is_plausible(size) and not listing.get("size_sqm"):
        listing["size_sqm"] = size
        ppm, _ok = clean_ppm(listing.get("price"), size)
        if ppm is not None:
            listing["price_per_sqm"] = ppm
    listing["enriched"] = True
    return True


def _find_item_node(data):
    """
    דף המודעה שומר את הנתונים תחת dehydratedState של react-query.
    מחפשים את הצומת שנראה כמו מודעה (יש בו token/price/additionalDetails).
    """
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if ("additionalDetails" in node
                    and ("token" in node or "price" in node)):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None
