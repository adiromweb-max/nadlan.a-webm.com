"""
השוואה like-for-like מול עסקאות שנסגרו בפועל, ועליית ערך רב-שנתית.

═══════════════════════════════════════════════════════════════════════
מגבלת מקור קריטית — קרא לפני שינוי הקוד הזה
═══════════════════════════════════════════════════════════════════════
הדרישה המקורית: "השווה רק לעסקאות סגורות באותה שכונה, חדרים ±0.5,
גודל ±20%, 12–18 חודשים אחרונים; צריך ≥5 עסקאות; קח חציון ₪/מ"ר".

זה מחייב גישה ל**עסקה בודדת**. נבדק בפועל (אוגוסט 2026):
  * ‎api.nadlan.gov.il‎ (רמת עסקה בודדת, עם מ"ר וחדרים) → 403 Forbidden,
    גם ישירות וגם דרך ScraperAPI עם יציאה ישראלית.
  * ‎data.nadlan.gov.il/api/deals/...‎ → 403.
  * ‎www.nadlan.gov.il/Nadlan.REST/Main/GetAssestAndDeals‎ → מחזיר HTML
    של SPA, לא JSON.

מה **כן** ציבורי: ‎data.nadlan.gov.il/api/pages/{settlement|neighborhood}/buy‎,
ובו ‎trends.rooms[].graphData‎ — **מחיר עסקה ממוצע רבעוני** לפי קטגוריית
חדרים (3/4/5 בלבד), כ-22 רבעונים אחורה. אין מ"ר, אין מספר עסקאות, אין
עסקה בודדת.

לכן המימוש כאן הוא הקירוב הנאמן ביותר האפשרי, וכל סטייה מסומנת במפורש
בפלט (‎comp_match_level‎, ‎data_quality‎) ולא מוסתרת:

  ✔ אזור          — שכונה, ובנפילה חזרה יישוב. מיושם במלואו.
  ✔ חלון זמן      — 12–18 חודשים אחרונים (4–6 רבעונים). מיושם במלואו.
  ✔ חדרים ±0.5    — מיושם מול קטגוריות 3/4/5: דירת 3.5 חדרים מושווית
                     לקטגוריות 3 **וגם** 4; דירת 3 חדרים לקטגוריה 3 בלבד.
  ✔ חציון ₪/מ"ר   — חציון על תצפיות הרבעון בחלון (ולא הממוצע השנתי המוכן).
  ⚠ "≥5 עסקאות"   — **אין מספר עסקאות ציבורי.** הפרוקסי: מספר תצפיות
                     הרבעון עם נתון מפורסם בחלון. רבעון מתפרסם רק כשיש בו
                     די עסקאות, ולכן זו אינדיקציה לעומק נתונים — אבל היא
                     **אינה** ספירת עסקאות. הערך מדווח כ-‎comp_count‎ עם
                     ‎comp_basis="רבעונים"‎ כדי שלא ייקרא כ-5 עסקאות.
  ⚠ גודל ±20%     — **אין מ"ר בנתון הרשמי.** לכן אי אפשר לסנן קומפים לפי
                     גודל. במקום זה הכלל מיושם כבדיקת שפיות על המודעה:
                     אם גודל המודעה חורג ב-±20% מהגודל הטיפוסי של קטגוריית
                     החדרים, ההשוואה אינה like-for-like והשורה מסומנת
                     ‎data_quality="גודל חריג לקטגוריה"‎ ואינה מוכתרת.

ההמרה ל-₪/מ"ר: המחיר הרשמי הוא ₪ **לעסקה** לפי חדרים, ולכן מחולק בגודל
טיפוסי — קודם הגודל החציוני של מודעות יד2 אמיתיות באותה עיר+חדרים, ורק
אם אין — טבלת ‎typical_size_sqm‎ מה-config.
"""
import logging
import statistics

from .pricing import cagr_pct, gap_pct, gross_yield_pct

log = logging.getLogger(__name__)

# ברירות מחדל — כולן ניתנות לדריסה מה-config
WINDOW_MONTHS = 18
MIN_COMPS = 5
ROOMS_TOLERANCE = 0.5
SIZE_TOLERANCE_PCT = 20.0
GAP_SUSPECT_PCT = 30.0
GAP_MIN_PCT = 8.0
APPRECIATION_YEARS = 5

MATCH_LEVEL_HE = {
    "neighborhood": "שכונה",
    "settlement": "יישוב",
    None: "אין",
}


def _median(values):
    vals = [float(v) for v in values if v is not None]
    return statistics.median(vals) if vals else None


def _window_cutoff(series, months):
    """
    (year, month) של גבול החלון, נספר אחורה מהתצפית העדכנית ביותר שיש.

    נספר מהנתון ולא מ"היום" כי נתוני נדל"ן ממשלתי מתעדכנים בפיגור של
    חודשים — חלון מבוסס-היום היה מרוקן את עצמו.
    """
    if not series:
        return None
    newest = series[0]
    total = newest["year"] * 12 + newest["month"] - int(months)
    return (total // 12, total % 12 or 12)


def _in_window(point, cutoff):
    if not cutoff:
        return False
    return (point["year"], point["month"]) >= cutoff


def eligible_rooms(rooms_map, rooms, tolerance=ROOMS_TOLERANCE):
    """
    קטגוריות החדרים שנחשבות התאמה למודעה, לפי סבילות ±tolerance.
    3.5 חדרים → [3, 4] ; 3 חדרים → [3] ; 2 חדרים כשיש רק 3/4/5 → [].
    """
    if rooms is None or not rooms_map:
        return []
    try:
        r = float(rooms)
    except (TypeError, ValueError):
        return []
    return sorted(k for k in rooms_map if abs(float(k) - r) <= float(tolerance) + 1e-9)


def typical_size(rooms_key, typical_sizes, observed_sizes):
    """גודל טיפוסי לקטגוריית חדרים: מודעות אמיתיות קודם, אחר כך ה-config."""
    if observed_sizes and rooms_key in observed_sizes:
        return float(observed_sizes[rooms_key]), "חציון מודעות אמיתיות"
    if typical_sizes and rooms_key in (typical_sizes or {}):
        return float(typical_sizes[rooms_key]), "טבלת config"
    return None, None


def bucket_observations(rooms_map, buckets, window, typical_sizes, observed_sizes=None):
    """
    תצפיות ₪/מ"ר בחלון, לקטגוריות החדרים שנבחרו.

    מוצא משותף לכל שלבי סולם ההשוואה: אותו חישוב בדיוק מופעל על נתוני
    שכונה, יישוב, קבוצת יישובים דומים ומרחב — כך שהמספרים בכל הרמות
    מדידים באותה סרגל ואפשר להשוות ביניהם.

    מחזיר (ppm_values, prices, sizes_used, size_source, buckets_used).
    """
    ppm_values, prices, sizes_used, size_src, used = [], [], [], None, []
    for b in buckets:
        info = (rooms_map or {}).get(b) or {}
        series = info.get("series") or []
        cutoff = _window_cutoff(series, window)
        pts = [p for p in series if _in_window(p, cutoff)]
        if not pts:
            continue
        size, src = typical_size(b, typical_sizes, observed_sizes)
        if not size or size <= 0:
            continue
        size_src = size_src or src
        sizes_used.append(size)
        for p in pts:
            prices.append(p["price"])
            ppm_values.append(p["price"] / size)
        used.append(b)
    return ppm_values, prices, sizes_used, size_src, used


def like_for_like(baseline, listing, cfg, observed_sizes=None):
    """
    בונה את סל ההשוואה למודעה אחת ומחזיר dict מלא ושקוף.

    מפתחות עיקריים:
      comp_median_ppm   — חציון ₪/מ"ר של הקומפים
      comp_count        — מספר תצפיות (רבעונים — ראה הערת המודול)
      comp_basis        — "רבעונים" — מה נספר בפועל
      comp_match_level  — "שכונה" / "יישוב" / "אין"
      gap_pct           — (חציון − מודעה)/חציון × 100
      sufficient        — האם עברנו את comps_min_count
      suspect           — פער > comps_gap_suspect_pct
      data_quality      — דגל איכות בעברית פשוטה
    """
    window = int(cfg.get("comps_window_months", WINDOW_MONTHS))
    min_comps = int(cfg.get("comps_min_count", MIN_COMPS))
    rooms_tol = float(cfg.get("comps_rooms_tolerance", ROOMS_TOLERANCE))
    size_tol = float(cfg.get("comps_size_tolerance_pct", SIZE_TOLERANCE_PCT))
    suspect_at = float(cfg.get("comps_gap_suspect_pct", GAP_SUSPECT_PCT))
    typical = cfg.get("typical_size_sqm") or {}

    out = {
        "comp_median_ppm": None, "comp_count": 0, "comp_basis": "רבעונים",
        "comp_match_level": MATCH_LEVEL_HE[None], "comp_match_level_raw": None,
        "comp_area": None, "comp_rooms": None, "comp_size_used": None,
        "comp_size_source": None, "comp_window_months": window,
        "comp_ppm_values": [], "comp_prices": [],
        "gap_pct": None, "sufficient": False, "suspect": False,
        "data_quality": "אין נתוני השוואה",
        "comp_version": None,
    }
    if not baseline:
        return out

    out["comp_area"] = baseline.get("area_name")
    out["comp_version"] = baseline.get("data_version")
    rooms_map = baseline.get("rooms") or {}
    nb_rooms = baseline.get("neighborhood_rooms") or {}

    buckets = eligible_rooms(rooms_map, listing.get("rooms"), rooms_tol)
    if not buckets:
        out["data_quality"] = "אין קטגוריית חדרים תואמת בנתון הרשמי"
        return out
    out["comp_rooms"] = buckets

    # ── איסוף תצפיות הרבעון בחלון, לכל קטגוריית חדרים מתאימה ──
    ppm_values, prices, sizes_used, size_src, used_buckets = bucket_observations(
        rooms_map, buckets, window, typical, observed_sizes)
    used_neighborhood = any(b in nb_rooms for b in used_buckets)

    out["comp_count"] = len(ppm_values)
    out["comp_ppm_values"] = [round(v, 1) for v in ppm_values]
    out["comp_prices"] = prices
    out["comp_size_used"] = _median(sizes_used)
    out["comp_size_source"] = size_src

    # רמת ההתאמה בפועל: "שכונה" רק אם הנתון שנעשה בו שימוש הוא באמת מהשכונה
    level_raw = "neighborhood" if (
        baseline.get("level") == "neighborhood" and used_neighborhood) else (
        "settlement" if ppm_values else None)
    out["comp_match_level_raw"] = level_raw
    out["comp_match_level"] = MATCH_LEVEL_HE[level_raw]

    if not ppm_values:
        out["data_quality"] = "אין עסקאות מפורסמות בחלון"
        return out

    out["comp_median_ppm"] = _median(ppm_values)
    out["sufficient"] = out["comp_count"] >= min_comps

    if not out["sufficient"]:
        out["data_quality"] = (f"אין מספיק עסקאות להשוואה "
                               f"({out['comp_count']} מתוך {min_comps} נדרשים)")
        return out

    # ── הפער ──
    out["gap_pct"] = gap_pct(out["comp_median_ppm"], listing.get("price_per_sqm"))

    # ── שפיות: גודל המודעה מול הגודל הטיפוסי של הקטגוריה (±20%) ──
    size_ok = True
    lsize, tsize = listing.get("size_sqm"), out["comp_size_used"]
    if lsize and tsize and tsize > 0:
        dev = abs(float(lsize) - float(tsize)) / float(tsize) * 100.0
        out["size_deviation_pct"] = dev
        size_ok = dev <= size_tol

    if out["gap_pct"] is not None and out["gap_pct"] > suspect_at:
        out["suspect"] = True
        out["data_quality"] = f"פער חשוד ({out['gap_pct']:.0f}%) — בדיקה ידנית"
    elif not size_ok:
        out["data_quality"] = (f"גודל חריג לקטגוריה "
                               f"({out.get('size_deviation_pct', 0):.0f}% מהטיפוסי)")
    else:
        out["data_quality"] = "תקין"
    return out


# ------------------------------------------------------------------
# עליית ערך אזורית — חציון שנתי + CAGR
# ------------------------------------------------------------------

def area_series(baseline, cfg, observed_sizes=None, years=None):
    """
    חציון ₪/מ"ר לכל שנה באזור, מספר הרבעונים עם עסקאות, ו-CAGR.

    הבסיס: הסדרה הרבעונית של קטגוריית "כל החדרים" (all), שהיא הרחבה ביותר.
    ההמרה ל-₪/מ"ר משתמשת בגודל טיפוסי משוקלל של קטגוריית 3–4 חדרים —
    הגודל אינו משתנה בין שנים, ולכן ה-CAGR זהה בין ₪/עסקה ל-₪/מ"ר.

    מחזיר {"years": [...], "cagr_pct": x, "years_covered": n, ...}
    """
    years = int(years or cfg.get("appreciation_years", APPRECIATION_YEARS))
    out = {"area_key": (baseline or {}).get("area_key"),
           "area_name": (baseline or {}).get("area_name"),
           "area_level": (baseline or {}).get("level"),
           "city": (baseline or {}).get("city"),
           "data_version": (baseline or {}).get("data_version"),
           "years": [], "cagr_pct": None, "years_covered": 0}
    if not baseline:
        return out

    series = baseline.get("all_series") or []
    if not series:
        return out

    typical = cfg.get("typical_size_sqm") or {}
    # גודל ייחוס לכלל הקטגוריות — חציון הגדלים הטיפוסיים של 3–4 חדרים
    ref_sizes = []
    for r in (3, 4):
        s, _src = typical_size(r, typical, observed_sizes)
        if s:
            ref_sizes.append(s)
    ref_size = _median(ref_sizes)

    by_year = {}
    for p in series:
        by_year.setdefault(p["year"], []).append(p["price"])

    rows = []
    for y in sorted(by_year):
        med_price = _median(by_year[y])
        rows.append({
            "year": y,
            "median_price": round(med_price, 0) if med_price else None,
            "median_ppm": round(med_price / ref_size, 1) if (med_price and ref_size) else None,
            "deal_quarters": len(by_year[y]),
            "area_level": out["area_level"],
            "city": out["city"],
            "area_name": out["area_name"],
            "data_version": out["data_version"],
        })

    # קצוות ה-CAGR חייבים להיות שנים עם כיסוי אמיתי. הסף הוא 3 רבעונים
    # ולא 4: השנה האחרונה בנתון הרשמי כמעט תמיד חסרה רבעון בגלל פיגור
    # הדיווח, ודרישת 4 הייתה מקצצת את החישוב בשנה שלמה ומחמיצה בדיוק את
    # התקופה העדכנית שמעניינת אותנו.
    min_q = int(cfg.get("appreciation_min_quarters_per_year", 3))
    full = [r for r in rows if r["deal_quarters"] >= min_q and r["median_price"]]
    full = full[-(years + 1):] if len(full) > years + 1 else full
    if len(full) >= 2:
        span = full[-1]["year"] - full[0]["year"]
        out["cagr_pct"] = cagr_pct(full[0]["median_price"],
                                   full[-1]["median_price"], span)
        out["years_covered"] = span
        out["cagr_from_year"] = full[0]["year"]
        out["cagr_to_year"] = full[-1]["year"]

    out["years"] = rows[-max(years + 1, 2):]
    out["ref_size_sqm"] = ref_size
    return out


# ------------------------------------------------------------------
# סולם השוואה מדורג — לעולם לא פוסלים מודעה בגלל היעדר עסקאות מקומיות
# ------------------------------------------------------------------
#
# הבעיה שהסולם פותר: ‎like_for_like‎ עוצר ברמת היישוב. ביישוב קטן (ירוחם,
# מצפה רמון, להבים) פשוט אין די רבעונים מפורסמים, ואז המודעה יוצאת בלי
# פער — כלומר דירה שמבוקשת 25% מתחת לשוק נעלמת מהפלט רק כי המקור הרשמי
# דליל שם. זו בדיוק התוצאה ההפוכה מהמבוקש.
#
# הפתרון: ארבע רמות, מהספציפי לכללי. לוקחים את **הרמה הספציפית ביותר
# שיש בה מספיק תצפיות**, ומדווחים במפורש באיזו רמה השתמשנו וברמת ביטחון
# מה. מודעה בלי אימות מקומי אינה נעלמת — היא מסומנת
# "אטרקטיבי לכאורה — לא אומת" ונשארת בפלט.
#
#   1. שכונה              — ביטחון גבוה
#   2. יישוב              — ביטחון גבוה
#   3. יישובים דומים      — קבוצת יישובים באותה רמת מחירים. ביטחון בינוני.
#   4. מרחב               — כל היישובים שנסרקו. ביטחון בינוני.
#   (בלי מספיק תצפיות באף רמה — ביטחון נמוך, אבל עדיין מדווח.)

LEVEL_NEIGHBORHOOD = 1
LEVEL_CITY = 2
LEVEL_TIER = 3
LEVEL_REGION = 4

LEVEL_HE = {
    LEVEL_NEIGHBORHOOD: "שכונה",
    LEVEL_CITY: "יישוב",
    LEVEL_TIER: "יישובים דומים",
    LEVEL_REGION: "מרחב",
    None: "אין",
}

CONF_HIGH, CONF_MEDIUM, CONF_LOW = "high", "medium", "low"
CONF_HE = {CONF_HIGH: "גבוה", CONF_MEDIUM: "בינוני", CONF_LOW: "נמוך", None: "—"}

UNVERIFIED_TAG = "אטרקטיבי לכאורה - לא אומת (מעט עסקאות)"


class MarketIndex:
    """
    מדד השוק לרמות 3–4 בסולם: קיבוץ יישובים לפי רמת מחירים.

    נבנה מהבסיסים הרשמיים שכבר נשלפו ברמת היישוב (‎AreaComps.settlement‎),
    ולכן **אינו עולה אף בקשה נוספת** — לא ל-ScraperAPI ולא לנדל"ן ממשלתי.

    "יישובים דומים" = יישובים שחציון ה-₪/מ"ר שלהם באותה מדרגה. עדיף על
    קרבה גיאוגרפית: להבים ועומר שכנות לבאר שבע אבל שוק אחר לגמרי, בעוד
    שדרות ונתיבות — רחוקות זו מזו ומתנהגות דומה.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.window = int(cfg.get("comps_window_months", WINDOW_MONTHS))
        self.typical = cfg.get("typical_size_sqm") or {}
        self.n_tiers = max(1, int(cfg.get("market_tiers", 4)))
        self.cities = {}            # city -> {"rooms": map, "sizes": {}, "median": x}
        self.tiers = {}             # city -> tier index
        self.tier_names = {}        # tier index -> תיאור בעברית
        self._obs_cache = {}

    def add_city(self, city, baseline, observed_sizes=None):
        """מוסיף יישוב למדד מתוך בסיס רשמי ברמת יישוב."""
        if not city or not baseline:
            return
        rooms_map = baseline.get("rooms") or {}
        if not rooms_map:
            return
        all_ppm, _p, _s, _src, _u = bucket_observations(
            rooms_map, sorted(rooms_map), self.window, self.typical, observed_sizes)
        self.cities[city] = {
            "rooms": rooms_map,
            "sizes": observed_sizes or {},
            "median": _median(all_ppm),
            "population": baseline.get("population"),
            "area_key": baseline.get("area_key"),
        }

    def build(self):
        """מחלק את היישובים למדרגות מחיר שוות-גודל. קורא אחרי כל add_city."""
        ranked = sorted(((c, d["median"]) for c, d in self.cities.items()
                         if d["median"]), key=lambda x: x[1])
        if not ranked:
            return self
        n_tiers = min(self.n_tiers, len(ranked))
        per = max(1, round(len(ranked) / n_tiers))
        for i, (city, _m) in enumerate(ranked):
            self.tiers[city] = min(i // per, n_tiers - 1)
        for t in set(self.tiers.values()):
            vals = [m for c, m in ranked if self.tiers[c] == t]
            self.tier_names[t] = (f"יישובים בטווח {min(vals):,.0f}–{max(vals):,.0f} "
                                  f'₪ למ"ר')
        log.info("מדד שוק: %d יישובים ב-%d מדרגות מחיר",
                 len(ranked), len(set(self.tiers.values())))
        return self

    def _obs(self, cities, buckets):
        """תצפיות ₪/מ"ר מצטברות מכמה יישובים, לקטגוריות חדרים נתונות."""
        key = (tuple(sorted(cities)), tuple(buckets))
        if key in self._obs_cache:
            return self._obs_cache[key]
        vals = []
        for c in cities:
            d = self.cities.get(c)
            if not d:
                continue
            ppm, _p, _s, _src, _u = bucket_observations(
                d["rooms"], buckets, self.window, self.typical, d["sizes"])
            vals.extend(ppm)
        self._obs_cache[key] = vals
        return vals

    def _buckets_for(self, city, rooms, tolerance=None):
        d = self.cities.get(city) or {}
        rooms_map = d.get("rooms") or {}
        if not rooms_map:
            # יישוב לא מוכר — משתמשים בקטגוריות שקיימות אצל שאר היישובים
            rooms_map = {b: None for c in self.cities
                         for b in (self.cities[c].get("rooms") or {})}
        tol = float(tolerance if tolerance is not None
                    else self.cfg.get("comps_rooms_tolerance", ROOMS_TOLERANCE))
        return eligible_rooms(rooms_map, rooms, tol)

    def city_level(self, city, rooms):
        buckets = self._buckets_for(city, rooms)
        return self._obs([city], buckets) if city in self.cities else []

    def tier_level(self, city, rooms):
        tier = self.tiers.get(city)
        if tier is None:
            return [], None
        peers = [c for c, t in self.tiers.items() if t == tier]
        return self._obs(peers, self._buckets_for(city, rooms)), self.tier_names.get(tier)

    def region_level(self, city, rooms, tolerance=None):
        return self._obs(list(self.cities), self._buckets_for(city, rooms, tolerance))

    def nearest_rooms_level(self, city, rooms):
        """
        מוצא אחרון: כל המרחב, עם התאמת חדרים מורחבת.

        למה זה קיים: הנתון הרשמי מפרסם רק קטגוריות 3/4/5 חדרים. דירת 2
        חדרים או 6 חדרים לא מקבלת אף רמת השוואה, ואז היא נעלמת מהפלט —
        בדיוק מה שאסור. כאן מרחיבים את הסבילות לקטגוריה הקרובה ביותר,
        ומחזירים גם את מרחק החדרים כדי שהפלט יגיד את האמת: ההשוואה היא
        לקטגוריה אחרת, ולכן ביטחון נמוך.

        חשוב לדעת בקריאת המספר: ל-₪/מ"ר יש הטיה לפי גודל (דירות קטנות
        יקרות יותר למ"ר), ולכן פער מול קטגוריה גדולה יותר נוטה להיראות
        שלילי מדי. זו הסיבה שהתוצאה כאן לעולם אינה "ביטחון גבוה".
        """
        try:
            r = float(rooms)
        except (TypeError, ValueError):
            return [], None
        available = sorted({b for c in self.cities
                            for b in (self.cities[c].get("rooms") or {})})
        if not available:
            return [], None
        nearest = min(available, key=lambda b: abs(float(b) - r))
        dist = abs(float(nearest) - r)
        if dist <= float(self.cfg.get("comps_rooms_tolerance", ROOMS_TOLERANCE)):
            return [], None          # כבר כוסה ברמות הרגילות
        return self._obs(list(self.cities), [nearest]), nearest

    def tier_peers(self, city):
        tier = self.tiers.get(city)
        if tier is None:
            return []
        return sorted(c for c, t in self.tiers.items() if t == tier)


def _rung(level, name, values, listing_ppm, min_count):
    """שלב אחד בסולם — חציון, מספר תצפיות, פער, והאם הוא מספיק."""
    med = _median(values or [])
    return {
        "level": level,
        "level_he": LEVEL_HE[level],
        "area_name": name,
        "median_ppm": med,
        "count": len(values or []),
        "gap_pct": gap_pct(med, listing_ppm) if med else None,
        "sufficient": len(values or []) >= min_count,
    }


def cascade(listing, comp, index, cfg):
    """
    מריץ את סולם ההשוואה ומחזיר את ההערכה הטובה ביותר שאפשר לתת למודעה.

    **הכלל החשוב**: מודעה לעולם אינה נפסלת בגלל היעדר עסקאות מקומיות.
    אם היא נראית זולה אבל אי אפשר לאמת — היא מוחזרת עם ‎confidence="low"‎
    ועם התגית ‎UNVERIFIED_TAG‎, ומופיעה בפלט ככזו.

    מחזיר dict עם comp_level, confidence, value_gap_pct והסולם המלא.
    """
    min_count = int(cfg.get("comp_ladder_min_count",
                            cfg.get("comps_min_count", MIN_COMPS)))
    suspect_at = float(cfg.get("comps_gap_suspect_pct", GAP_SUSPECT_PCT))
    gap_min = float(cfg.get("comps_gap_min_pct", GAP_MIN_PCT))
    ppm = listing.get("price_per_sqm")
    city = listing.get("city")

    out = {
        "comp_level": None, "comp_level_he": LEVEL_HE[None],
        "value_area": None, "value_median_ppm": None, "value_count": 0,
        "value_gap_pct": None, "confidence": CONF_LOW,
        "confidence_he": CONF_HE[CONF_LOW], "suspect": False,
        "unverified_attractive": False, "value_tag": None, "ladder": [],
    }
    if not index or not ppm:
        out["value_tag"] = "אין מחיר למ\"ר לחישוב"
        return out

    rungs = []
    comp = comp or {}
    # רמה 1 — שכונה: קיימת רק אם באמת נשלפו נתוני שכונה למודעה הזו
    if comp.get("comp_match_level_raw") == "neighborhood" and comp.get("comp_median_ppm"):
        rungs.append({
            "level": LEVEL_NEIGHBORHOOD, "level_he": LEVEL_HE[LEVEL_NEIGHBORHOOD],
            "area_name": comp.get("comp_area"),
            "median_ppm": comp.get("comp_median_ppm"),
            "count": comp.get("comp_count") or 0,
            "gap_pct": comp.get("gap_pct") if comp.get("gap_pct") is not None
            else gap_pct(comp.get("comp_median_ppm"), ppm),
            "sufficient": (comp.get("comp_count") or 0) >= min_count,
        })

    rungs.append(_rung(LEVEL_CITY, city, index.city_level(city, listing.get("rooms")),
                       ppm, min_count))
    tier_vals, tier_name = index.tier_level(city, listing.get("rooms"))
    rungs.append(_rung(LEVEL_TIER, tier_name, tier_vals, ppm, min_count))
    rungs.append(_rung(LEVEL_REGION, "כלל היישובים שנסרקו",
                       index.region_level(city, listing.get("rooms")), ppm, min_count))

    # מוצא אחרון — רק אם אף רמה רגילה לא החזירה נתון
    relaxed_rooms = None
    if not any(r["median_ppm"] for r in rungs):
        vals, nearest = index.nearest_rooms_level(city, listing.get("rooms"))
        if vals:
            relaxed_rooms = nearest
            r = _rung(LEVEL_REGION, f"כלל היישובים, קטגוריית {nearest} חדרים",
                      vals, ppm, min_count)
            r["relaxed_rooms"] = nearest
            r["sufficient"] = False       # לעולם לא "מספיק" — ההתאמה חלקית
            rungs.append(r)

    out["ladder"] = rungs

    # הרמה הספציפית ביותר שיש בה די תצפיות; אם אין כזו — הספציפית ביותר
    # שיש בה בכלל נתון, וסימון ביטחון נמוך.
    chosen = next((r for r in rungs if r["sufficient"] and r["median_ppm"]), None)
    if chosen:
        out["confidence"] = (CONF_HIGH if chosen["level"] in
                             (LEVEL_NEIGHBORHOOD, LEVEL_CITY) else CONF_MEDIUM)
    else:
        chosen = next((r for r in rungs if r["median_ppm"]), None)
        out["confidence"] = CONF_LOW

    if not chosen:
        out["value_tag"] = "אין עסקאות מפורסמות באף רמת השוואה"
        return out

    out.update({
        "comp_level": chosen["level"],
        "comp_level_he": chosen["level_he"],
        "value_area": chosen["area_name"],
        "value_median_ppm": chosen["median_ppm"],
        "value_count": chosen["count"],
        "value_gap_pct": chosen["gap_pct"],
        "confidence_he": CONF_HE[out["confidence"]],
    })

    out["relaxed_rooms"] = chosen.get("relaxed_rooms")
    if chosen.get("relaxed_rooms"):
        out["value_tag"] = (f"השוואה לקטגוריית {chosen['relaxed_rooms']} חדרים "
                            f"(אין נתון רשמי לגודל הזה)")
        return out

    gap = out["value_gap_pct"]
    if gap is not None and gap > suspect_at:
        # התקרה נשמרת גם כאן: פער כזה כמעט תמיד אינו אמיתי
        out["suspect"] = True
        out["value_tag"] = f"פער חשוד ({gap:.0f}%) — בדיקה ידנית"
    elif out["confidence"] == CONF_LOW and gap is not None and gap >= gap_min:
        out["unverified_attractive"] = True
        out["value_tag"] = UNVERIFIED_TAG
    elif out["confidence"] == CONF_MEDIUM and gap is not None and gap >= gap_min:
        out["value_tag"] = f"פער מול {chosen['level_he']} — אימות מקומי חסר"
    return out


# ------------------------------------------------------------------
# תשואת שכירות ברוטו
# ------------------------------------------------------------------

def rental_yield(listing, rent, cfg, level_used=None):
    """
    תשואה שנתית ברוטו = שכ"ד חודשי × 12 / מחיר × 100.

    שכר הדירה הוא **הערכה**: הוא הממוצע הרשמי המדווח לקטגוריית החדרים
    באזור, לא שכר דירה בפועל של הנכס הזה. אין ניכוי ועד/ארנונה/תיווך/
    תקופות ריקות — לכן זו תשואה **ברוטו** ולא תשואה נטו.

    מחזיר dict עם yield_pct, monthly_rent, rent_basis, is_estimate.
    """
    months = int(cfg.get("rent_months_per_year", 12))
    out = {"yield_pct": None, "monthly_rent": None, "annual_rent": None,
           "rent_basis": None, "rent_level": level_used, "is_estimate": True,
           "yield_index": (rent or {}).get("yield_index")}
    if not rent or not listing.get("price"):
        return out

    rooms = listing.get("rooms")
    monthly, basis = None, None
    rent_rooms = rent.get("rooms") or {}
    if rooms is not None and rent_rooms:
        try:
            target = float(rooms)
            near = [k for k in rent_rooms
                    if abs(float(k) - target) <= float(
                        cfg.get("comps_rooms_tolerance", ROOMS_TOLERANCE)) + 1e-9]
        except (TypeError, ValueError):
            near = []
        if near:
            monthly = _median([rent_rooms[k] for k in near])
            basis = f"שכ\"ד ממוצע רשמי, {'/'.join(str(n) for n in sorted(near))} חדרים"
    if monthly is None and rent.get("all"):
        monthly = float(rent["all"])
        basis = "שכ\"ד ממוצע רשמי, כל הקטגוריות"
    if monthly is None:
        return out

    out["monthly_rent"] = round(monthly, 0)
    out["annual_rent"] = round(monthly * months, 0)
    out["rent_basis"] = basis
    out["yield_pct"] = gross_yield_pct(monthly * months, listing.get("price"))
    return out
