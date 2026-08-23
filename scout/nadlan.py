"""
מקור העסקאות האמיתיות — נדל"ן ממשלתי (נתוני רשות המיסים).

בזרימת listings-first המודול הזה נקרא **אחרי** שיד2 נסרק ונוקד:
רק למודעות שעברו את הסינון המקדים שולפים את העסקאות שנסגרו בפועל
באזור הספציפי שלהן, ומהן נגזר פער המחיר הסופי.

מצב ה-API (נבדק אוגוסט 2026):
  * data.nadlan.gov.il — ציבורי לחלוטין, בלי מפתח, בלי ScraperAPI, בלי עלות:
      - ‎api/index/setl_types.json‎              — אינדקס יישובים (קוד למ"ס + אוכלוסייה)
      - ‎api/pages/settlement/buy/{code}.json‎   — נתוני יישוב + רשימת שכונות
      - ‎api/pages/neighborhood/buy/{id}.json‎   — נתוני **שכונה** (האזור הספציפי)
    שני האחרונים מחזירים מחיר עסקה ממוצע ב-12 החודשים האחרונים לפי מספר
    חדרים — נתון רשמי הנגזר מעסקאות שנסגרו בפועל.
  * api.nadlan.gov.il (עסקה-בודדת, עם מ"ר מדויק) מחזיר 403 Forbidden —
    גם ישירות וגם דרך ScraperAPI עם יציאה ישראלית. לכן אין גישה לרמת
    העסקה הבודדת, ו-₪/מ"ר הרשמי מחושב כמחיר ממוצע חלקי גודל אופייני.
  * קודי היישוב כאן הם קודי הלמ"ס — אותם קודים שיד2 משתמש בהם ב-URL.
"""
import logging
import re
import threading

log = logging.getLogger(__name__)

DATA_BASE = "https://data.nadlan.gov.il/api"
SETL_INDEX_URL = f"{DATA_BASE}/index/setl_types.json"
SETTLEMENT_BUY_URL = f"{DATA_BASE}/pages/settlement/buy/{{code}}.json"
NEIGHBORHOOD_BUY_URL = f"{DATA_BASE}/pages/neighborhood/buy/{{nid}}.json"
# שכירות — אותו מבנה בדיוק, אבל המחירים הם שכר דירה חודשי ממוצע.
# זהו הבסיס לתשואה: נתון מדוד ולא הנחה מה-config.
SETTLEMENT_RENT_URL = f"{DATA_BASE}/pages/settlement/rent/{{code}}.json"
NEIGHBORHOOD_RENT_URL = f"{DATA_BASE}/pages/neighborhood/rent/{{nid}}.json"

# קישור לחיפוש באתר נדל"ן ממשלתי — לשקיפות, מופיע בכל שורת אקסל
NADLAN_SEARCH_URL = "https://www.nadlan.gov.il/?search={query}"

REFERER = {"Referer": "https://www.nadlan.gov.il/", "Origin": "https://www.nadlan.gov.il"}


def _norm(name):
    """נרמול שם יישוב: קרית/קריית, רווחים כפולים, גרשיים."""
    if not name:
        return ""
    s = str(name).strip().replace("״", '"').replace("’", "'")
    s = " ".join(s.split())
    s = s.replace("קרית ", "קריית ")
    return s


def _norm_neighborhood(name):
    """
    נרמול שם שכונה להשוואה בין יד2 לנדל"ן ממשלתי.
    יד2 אומר "שכונה א'" / 'שכונה י"א' ; נדל"ן ממשלתי אומר "א" / "יא".
    """
    if not name:
        return ""
    s = str(name).strip().replace("״", '"').replace("’", "'")
    s = s.replace('"', "").replace("'", "")
    s = re.sub(r"^שכונ(?:ה|ת)\s+", "", s)
    s = re.sub(r"^ה(?=\S)", "", s)          # "העיר העתיקה" ↔ "עיר עתיקה"
    return " ".join(s.split())


def fetch_settlement_index(fetcher):
    """{שם_מנורמל: {code, name, population, type}} או {} בכשל."""
    data = fetcher.get_json(SETL_INDEX_URL, headers=REFERER)
    if not isinstance(data, dict):
        log.error("לא ניתן לטעון את אינדקס היישובים")
        return {}
    index = {}
    for code, info in data.items():
        if not isinstance(info, dict):
            continue
        name = info.get("SETL_NAME")
        if not name:
            continue
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            continue
        index[_norm(name)] = {
            "code": code_int,
            "name": name,
            "population": info.get("POPULATION"),
            "type": info.get("GLOBAL_TYPE"),
        }
    log.info("אינדקס יישובים נטען: %d יישובים", len(index))
    return index


def resolve_city(index, city):
    """מוצא רשומת יישוב לפי שם (עם נרמול). מחזיר dict או None."""
    key = _norm(city)
    if key in index:
        return index[key]
    matches = [v for k, v in index.items() if key and (key in k or k in key)]
    if len(matches) == 1:
        log.info("שם היישוב %r הותאם ל-%r", city, matches[0]["name"])
        return matches[0]
    if len(matches) > 1:
        log.warning("שם היישוב %r מתאים ל-%d יישובים — מדלג", city, len(matches))
    return None


def _quarter_series(entry, price_field):
    """
    הסדרה הרבעונית המלאה של קטגוריית חדרים אחת, מהחדש לישן.

    ‎graphData‎ מחזיק כ-22 רבעונים (≈5.5 שנים) — זה המקור גם להשוואה
    like-for-like בחלון של 12–18 חודשים וגם לעליית הערך הרב-שנתית.
    רבעון בלי מספיק עסקאות מתפרסם כ-null ולכן מסונן החוצה.

    מחזיר [{"year": int, "month": int, "price": float}] ממוין מהחדש לישן.
    """
    out = []
    for p in entry.get("graphData") or []:
        val = p.get(price_field)
        if val is None:
            continue
        try:
            out.append({"year": int(p.get("year")), "month": int(p.get("month")),
                        "price": float(val)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    return out


def _parse_trends(data, price_field):
    """
    מפרסר את ‎trends.rooms‎ (מבנה זהה ביישוב ובשכונה).
    price_field: 'settlementPrice' או 'neighborhoodPrice'.
    מחזיר (rooms_map, all_rooms_avg, all_change, quarters, all_series).

    לכל קטגוריית חדרים נשמרת גם ‎series‎ — הסדרה הרבעונית המלאה, שממנה
    נגזרים גם ההשוואה בחלון וגם ה-CAGR.
    """
    trends = (data or {}).get("trends") or {}
    rooms_out, all_avg, all_change, all_series = {}, None, None, []

    for entry in trends.get("rooms") or []:
        num = entry.get("numRooms")
        summary = entry.get("summary") or {}
        try:
            avg = float(summary.get("lastYearAvgPrice"))
        except (TypeError, ValueError):
            avg = None
        try:
            change = float(summary.get("priceDifferencePercentage"))
        except (TypeError, ValueError):
            change = None

        series = _quarter_series(entry, price_field)

        if num == "all":
            all_avg, all_change, all_series = avg, change, series
            continue
        try:
            num_i = int(num)
        except (TypeError, ValueError):
            continue
        if avg and avg > 0 and entry.get("hasDeals"):
            rooms_out[num_i] = {
                "avg_price_12m": avg,
                "change_pct": change,
                # רבעונים עם עסקאות ב-12 החודשים האחרונים (4 רבעונים)
                "quarters_with_data": min(len(series), 4),
                "series": series,
            }

    quarters_max = max([v["quarters_with_data"] for v in rooms_out.values()] or [0])
    return rooms_out, all_avg, all_change, quarters_max, all_series


def fetch_city_baseline(fetcher, city, setl):
    """בסיס ההשוואה הרשמי ברמת היישוב. מחזיר dict או None."""
    data = fetcher.get_json(SETTLEMENT_BUY_URL.format(code=setl["code"]),
                            headers=REFERER)
    if not isinstance(data, dict):
        log.warning("אין נתוני עסקאות ל-%s (קוד %s)", city, setl["code"])
        return None

    rooms, all_avg, all_change, quarters, all_series = _parse_trends(
        data, "settlementPrice")
    if not rooms and not all_avg:
        log.warning("לא נמצאו נתוני מחיר ל-%s", city)
        return None

    baseline = {
        "level": "settlement",
        "area_key": f"settlement:{setl['code']}",
        "area_name": data.get("settlementName") or setl.get("name"),
        "city": city,
        "setl_code": setl["code"],
        "neighborhood_id": None,
        "population": setl.get("population"),
        "data_version": data.get("version"),
        "rooms": rooms,
        "all_rooms_avg_price": all_avg,
        "all_series": all_series,
        "price_change_pct": all_change,
        "quarters_with_data": quarters,
        "indexes": (data.get("trends") or {}).get("indexes") or {},
        "neighborhoods": data.get("otherNeighborhoods") or [],
    }
    log.info("%s: בסיס רשמי ברמת יישוב — %d קטגוריות חדרים, ממוצע %s ₪ (גרסה %s)",
             city, len(rooms), f"{all_avg:,.0f}" if all_avg else "אין",
             data.get("version"))
    return baseline


def fetch_neighborhood_baseline(fetcher, nb_id, nb_title, city, setl_baseline):
    """
    בסיס ההשוואה הרשמי ברמת **השכונה** — האזור הספציפי של המודעה.
    נופל חזרה על נתוני היישוב עבור קטגוריות חדרים שאין עליהן עסקאות בשכונה.
    מחזיר dict או None.
    """
    data = fetcher.get_json(NEIGHBORHOOD_BUY_URL.format(nid=nb_id), headers=REFERER)
    if not isinstance(data, dict):
        log.debug("אין נתוני שכונה ל-%s (%s)", nb_title, nb_id)
        return None

    rooms, all_avg, all_change, quarters, all_series = _parse_trends(
        data, "neighborhoodPrice")
    if not rooms and not all_avg:
        return None

    setl_rooms = (setl_baseline or {}).get("rooms") or {}
    merged = dict(setl_rooms)
    merged.update(rooms)            # נתוני השכונה גוברים על נתוני היישוב

    return {
        "level": "neighborhood",
        "area_key": f"neighborhood:{nb_id}",
        "area_name": data.get("neighborhoodName") or nb_title,
        "city": city,
        "setl_code": (setl_baseline or {}).get("setl_code") or data.get("settlementID"),
        "neighborhood_id": nb_id,
        "population": (setl_baseline or {}).get("population"),
        "data_version": data.get("version"),
        "rooms": merged,
        "neighborhood_rooms": rooms,        # אילו קטגוריות באמת מהשכונה
        "all_rooms_avg_price": all_avg or (setl_baseline or {}).get("all_rooms_avg_price"),
        "all_series": all_series or (setl_baseline or {}).get("all_series") or [],
        "price_change_pct": all_change,
        "quarters_with_data": max(quarters, (setl_baseline or {}).get("quarters_with_data") or 0),
        "indexes": (data.get("trends") or {}).get("indexes") or {},
        "settlement_baseline": setl_baseline,
    }


# ------------------------------------------------------------------
# שכירות — הבסיס לתשואה
# ------------------------------------------------------------------

def fetch_rent(fetcher, level, code):
    """
    שכר דירה חודשי ממוצע ב-12 החודשים האחרונים לפי מספר חדרים, מאותו מקור
    רשמי בדיוק (עסקאות שכירות מדווחות). מחזיר dict או None.

    {"rooms": {3: 2575, ...}, "all": 2725, "yield_index": 3.05, "version": ...}
    """
    url = (SETTLEMENT_RENT_URL if level == "settlement"
           else NEIGHBORHOOD_RENT_URL).format(code=code, nid=code)
    data = fetcher.get_json(url, headers=REFERER)
    if not isinstance(data, dict):
        return None

    field = "settlementPrice" if level == "settlement" else "neighborhoodPrice"
    rooms, all_avg, _change, _q, _s = _parse_trends(data, field)
    rent_rooms = {k: v["avg_price_12m"] for k, v in rooms.items()
                  if v.get("avg_price_12m")}
    if not rent_rooms and not all_avg:
        return None

    indexes = (data.get("trends") or {}).get("indexes") or {}
    return {
        "rooms": rent_rooms,
        "all": all_avg,
        "yield_index": indexes.get("yield"),
        "version": data.get("version"),
        "level": level,
    }


def match_neighborhood(neighborhoods, name):
    """
    מתאים שם שכונה של יד2 לרשומת שכונה של נדל"ן ממשלתי.
    מחזיר dict {'title','id'} או None.
    """
    if not name or not neighborhoods:
        return None

    # יד2 לפעמים מחזיר כמה שכונות מופרדות בפסיק — מנסים כל חלק
    parts = [p for p in re.split(r"[,/]", str(name)) if p.strip()]
    norm_index = [(_norm_neighborhood(n.get("title")), n) for n in neighborhoods
                  if isinstance(n, dict) and n.get("title") and n.get("id")]

    for part in parts:
        key = _norm_neighborhood(part)
        if not key:
            continue
        # 1. התאמה מדויקת
        for nk, n in norm_index:
            if nk and nk == key:
                return n
        # 2. הכלה — רק לשמות ארוכים מספיק, אחרת "א" מתאים להכול
        if len(key) >= 3:
            hits = [n for nk, n in norm_index
                    if nk and len(nk) >= 3 and (nk in key or key in nk)]
            uniq = {h["id"]: h for h in hits}
            if len(uniq) == 1:
                return next(iter(uniq.values()))
    return None


class AreaComps:
    """
    שירות עסקאות-לפי-אזור, עם cache.

    נקרא רק עבור מודעות שעברו את הסינון המקדים (listings-first), ולכן
    מספר הבקשות קטן. כל הבקשות הן ל-data.nadlan.gov.il — חינם וללא ScraperAPI.
    בטוח לתהליכונים.
    """

    def __init__(self, fetcher, index):
        self.fetcher = fetcher
        self.index = index
        self._settlements = {}      # city -> baseline|None
        self._neighborhoods = {}    # (city, nb_key) -> baseline|None
        self._rents = {}            # (level, code) -> rent|None
        self._lock = threading.Lock()
        self.settlement_requests = 0
        self.neighborhood_requests = 0
        self.neighborhood_hits = 0
        self.rent_requests = 0
        self.rent_hits = 0
        self.misses = []

    def rent(self, level, code):
        """נתוני שכירות לאזור (עם cache). מחזיר dict או None."""
        if not code:
            return None
        key = (level, code)
        with self._lock:
            if key in self._rents:
                return self._rents[key]
        data = None
        self.rent_requests += 1
        try:
            data = fetch_rent(self.fetcher, level, code)
        except Exception as e:
            log.warning("כשל בשליפת שכירות ל-%s/%s: %s", level, code, e)
        if data:
            self.rent_hits += 1
        with self._lock:
            self._rents[key] = data
        return data

    def rent_for(self, baseline):
        """
        שכירות לאזור המודעה: קודם ברמת השכונה, ואם אין — ברמת היישוב.
        מחזיר (rent_dict|None, level_used).
        """
        if not baseline:
            return None, None
        if baseline.get("level") == "neighborhood" and baseline.get("neighborhood_id"):
            r = self.rent("neighborhood", baseline["neighborhood_id"])
            if r and (r.get("rooms") or r.get("all")):
                return r, "neighborhood"
        if baseline.get("setl_code"):
            r = self.rent("settlement", baseline["setl_code"])
            if r and (r.get("rooms") or r.get("all")):
                return r, "settlement"
        return None, None

    def settlement(self, city):
        """בסיס רמת-יישוב לעיר (עם cache). מחזיר dict או None."""
        with self._lock:
            if city in self._settlements:
                return self._settlements[city]
        setl = resolve_city(self.index, city)
        baseline = None
        if setl:
            self.settlement_requests += 1
            try:
                baseline = fetch_city_baseline(self.fetcher, city, setl)
            except Exception as e:
                log.warning("כשל בבסיס היישוב %s: %s", city, e)
        else:
            log.warning("לא נמצא קוד יישוב עבור %r", city)
            with self._lock:
                if city not in self.misses:
                    self.misses.append(city)
        with self._lock:
            self._settlements[city] = baseline
        return baseline

    def for_listing(self, listing):
        """
        מחזיר את בסיס ההשוואה הרשמי לאזור הספציפי של המודעה:
        שכונה אם נמצאה התאמה ויש עליה נתונים, אחרת רמת היישוב.
        """
        city = listing.get("city")
        setl_baseline = self.settlement(city)
        if not setl_baseline:
            return None

        nb_name = listing.get("neighborhood")
        if not nb_name:
            return setl_baseline

        key = _norm_neighborhood(nb_name)
        cache_key = (city, key)
        with self._lock:
            if cache_key in self._neighborhoods:
                cached = self._neighborhoods[cache_key]
                return cached or setl_baseline

        match = match_neighborhood(setl_baseline.get("neighborhoods"), nb_name)
        baseline = None
        if match:
            self.neighborhood_requests += 1
            try:
                baseline = fetch_neighborhood_baseline(
                    self.fetcher, match["id"], match["title"], city, setl_baseline)
            except Exception as e:
                log.warning("כשל בבסיס השכונה %s/%s: %s", city, nb_name, e)
            if baseline:
                self.neighborhood_hits += 1
                log.info("%s / %s → שכונת %s (עסקאות ברמת שכונה)",
                         city, nb_name, match["title"])
        else:
            log.debug("%s: לא נמצאה שכונה רשמית מתאימה ל-%r", city, nb_name)

        with self._lock:
            self._neighborhoods[cache_key] = baseline
        return baseline or setl_baseline

    def loaded_settlements(self):
        with self._lock:
            return {c: b for c, b in self._settlements.items() if b}

    def summary_he(self):
        return (f"{self.settlement_requests} יישובים, "
                f"{self.neighborhood_requests} שכונות נשלפו "
                f"({self.neighborhood_hits} עם נתונים ברמת שכונה), "
                f"{self.rent_requests} שליפות שכירות ({self.rent_hits} עם נתונים)")


def search_link(city, area_name=None):
    """
    קישור חי לחיפוש באתר נדל"ן ממשלתי, ממוקד לעיר/אזור של המודעה.
    מאפשר לאמת את העסקאות שנסגרו בלי לחפש ידנית.
    """
    import urllib.parse
    parts = [p for p in (area_name, city) if p]
    # "שכונה ג', באר שבע" — האתר מקבל חיפוש טקסט חופשי
    query = ", ".join(dict.fromkeys(parts)) if parts else (city or "")
    return NADLAN_SEARCH_URL.format(query=urllib.parse.quote(query))


def baseline_ppsqm(baseline, rooms, typical_sizes, observed_size_by_rooms=None):
    """
    ₪ למ"ר "הוגן" לפי עסקאות רשמיות, לדירה עם מספר חדרים נתון.

    המחיר הרשמי הוא מחיר עסקה ממוצע (₪ לעסקה) לפי מספר חדרים, ולכן כדי
    להמיר ל-₪/מ"ר צריך גודל אופייני: קודם הגודל החציוני של מודעות אמיתיות
    באותה עיר+חדרים, ואם אין — הטבלה מה-config.

    מחזיר dict עם ppsqm, avg_price, size_used, matched_rooms, rooms_level.
    """
    empty = {"ppsqm": None, "avg_price": None, "size_used": None,
             "matched_rooms": None, "rooms_level": None}
    if not baseline:
        return empty

    rooms_map = baseline.get("rooms") or {}
    avg_price, matched_rooms = None, None

    if rooms is not None and rooms_map:
        try:
            target = int(round(float(rooms)))
        except (TypeError, ValueError):
            target = None
        if target is not None:
            nearest = min(rooms_map, key=lambda r: (abs(r - target), r))
            if abs(nearest - target) <= 1:      # פער גדול מחדר = לא רלוונטי
                matched_rooms = nearest
                avg_price = rooms_map[nearest]["avg_price_12m"]

    if avg_price is None:
        avg_price = baseline.get("all_rooms_avg_price")
        matched_rooms = None
    if not avg_price:
        return empty

    # מאיזו רמה הגיע הנתון שנבחר בפועל
    rooms_level = baseline.get("level")
    if (baseline.get("level") == "neighborhood" and matched_rooms is not None
            and matched_rooms not in (baseline.get("neighborhood_rooms") or {})):
        rooms_level = "settlement"

    size = None
    if observed_size_by_rooms and matched_rooms in (observed_size_by_rooms or {}):
        size = observed_size_by_rooms[matched_rooms]
    if not size and typical_sizes:
        key = matched_rooms
        if key is None and rooms is not None:
            try:
                key = int(round(float(rooms)))
            except (TypeError, ValueError):
                key = None
        if key is not None:
            size = typical_sizes.get(key)
    if not size:
        return {"ppsqm": None, "avg_price": float(avg_price), "size_used": None,
                "matched_rooms": matched_rooms, "rooms_level": rooms_level}

    return {"ppsqm": float(avg_price) / float(size), "avg_price": float(avg_price),
            "size_used": float(size), "matched_rooms": matched_rooms,
            "rooms_level": rooms_level}
