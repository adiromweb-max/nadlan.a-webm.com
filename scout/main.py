"""
נדל"ן סקאוט — נקודת הכניסה. רץ מ-cron, דטרמיניסטי, בלי קריאות LLM.

זרימת listings-first (מודעות קודם):
  0. אינדקס יישובים מנדל"ן ממשלתי → קוד למ"ס לכל עיר (יד2 משתמש באותם קודים).
  1. סריקת יד2 דרך ScraperAPI — כל הערים, כל טווח המחירים.
  2. התמדה ב-SQLite: upsert + רישום ירידות מחיר.
  3. סינון מקדים: ניקוד מלא מול חציון ה-₪/מ"ר של המודעות עצמן (חינם).
  4. רק למועמדות שעברו את השער: העשרה מדף המודעה + שליפת **עסקאות
     שנסגרו בפועל באזור הספציפי** (שכונה, ואם אין — יישוב) → ציון סופי.
  5. אקסל + מייל אחד.

עקרון: שום כשל של מקור לא מפיל את הריצה. כל שלב עטוף ב-try/except.
"""
import argparse
import json
import logging
import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import (alerts, charts, comps, dashboard, db, emailer, excel, jsonout,
               nadlan, pricing, scoring, sections, webapp, yad2)
from .config import load_config
from .http import Fetcher
from .scraperapi import CreditBudgetExceeded, ScraperApiClient, account_info

log = logging.getLogger("scout")


def setup_logging(cfg, verbose=False):
    logs = cfg["paths"]["logs"]
    logs.mkdir(parents=True, exist_ok=True)
    logfile = logs / f"run-{date.today():%Y%m%d}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # פלט למסך רק בהרצה ידנית — ב-cron ה-stdout מופנה לאותו קובץ
    if sys.stdout.isatty():
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logfile


# ------------------------------------------------------------------
# שלב 0 — קודי יישוב
# ------------------------------------------------------------------

def resolve_city_codes(fetcher, cfg, report):
    """
    מחזיר (pairs, index) כאשר pairs = [(city, setl_code)].
    קודי הלמ"ס של נדל"ן ממשלתי הם גם קודי הערים של יד2.
    """
    try:
        index = nadlan.fetch_settlement_index(fetcher)
    except Exception:
        log.error("כשל בטעינת אינדקס היישובים:\n%s", traceback.format_exc())
        index = {}
    if not index:
        report["notes"].append(
            "אינדקס היישובים של נדל\"ן ממשלתי לא נטען — אי אפשר לגזור קודי ערים ליד2.")
        return [], {}

    pairs = []
    for city in cfg["cities"]:
        setl = nadlan.resolve_city(index, city)
        if not setl:
            log.warning("לא נמצא קוד יישוב עבור %r — מדלג", city)
            report["notes"].append(f"לא נמצא קוד יישוב עבור \u200f{city}\u200f.")
            continue
        pairs.append((city, setl["code"]))
    log.info("נפתרו קודי יישוב ל-%d/%d ערים", len(pairs), len(cfg["cities"]))
    return pairs, index


# ------------------------------------------------------------------
# שלב 2 — התמדה
# ------------------------------------------------------------------

def persist(conn, listings, cfg, today):
    """upsert לכל המודעות + זיהוי ירידות מחיר. מחזיר (meta, drops, latest_drop)."""
    meta = {}
    for l in listings:
        try:
            meta[l["id"]] = db.upsert_listing(conn, l, today.isoformat())
        except Exception:
            log.error("כשל upsert למודעה %s:\n%s", l.get("id"), traceback.format_exc())
    conn.commit()

    # מודעות שלא נראו זמן רב מסומנות כלא-פעילות (הסריקה חלקית לפי עמודים,
    # ולכן אי אפשר להסיק היעדרות מריצה בודדת)
    try:
        n = db.mark_inactive_stale(conn, "yad2",
                                   int(cfg.get("inactive_after_days", 14)), today)
        if n:
            log.info("סומנו %d מודעות כלא-פעילות (לא נראו מעל %s ימים)",
                     n, cfg.get("inactive_after_days", 14))
        conn.commit()
    except Exception:
        log.error("כשל בסימון מודעות ישנות:\n%s", traceback.format_exc())

    drops = []
    try:
        drops = db.price_drop_history(conn)
    except Exception:
        log.error("כשל בשליפת היסטוריית מחירים:\n%s", traceback.format_exc())

    return meta, drops, _latest_drops(drops)


def _latest_drops(drops):
    latest = {}
    for d in drops or []:
        cur = latest.get(d["listing_id"])
        if cur is None or (d["changed_at"] or "") > (cur["changed_at"] or ""):
            latest[d["listing_id"]] = d
    return latest


def load_from_db(conn, cfg):
    """
    המודעות השמורות, בלי אף בקשה לרשת — הבסיס לריצת ‎--no-scrape‎.

    בריצה כזו **לא** מבצעים upsert ולא מסמנים מודעות כלא-פעילות: לא ראינו
    את יד2 בריצה הזו, ולכן אין לנו שום מידע חדש על מה שקיים באוויר. רענון
    ‎last_seen‎ בלי סריקה אמיתית היה הופך מודעות שירדו מהאוויר לנצחיות.
    """
    rows = db.load_stored_listings(conn, "yad2")
    listings, meta = [], {}
    for row in rows:
        try:
            l = yad2.from_stored(row)
        except Exception:
            log.error("כשל בשחזור מודעה %s:\n%s", row.get("id"), traceback.format_exc())
            continue
        listings.append(l)
        meta[l["id"]] = {"is_new": False, "old_price": None,
                         "first_seen": row.get("first_seen")}
    drops = db.price_drop_history(conn)
    log.info("ריצה ללא סריקה: נטענו %d מודעות פעילות מה-DB (0 קרדיטים)",
             len(listings))
    return listings, meta, drops, _latest_drops(drops)


# ------------------------------------------------------------------
# שלב 3 — סינון מקדים (חינם, מול חציון המבוקש של יד2)
# ------------------------------------------------------------------

def prescreen(listings, cfg, meta, latest_drop, today):
    benchmarks = scoring.peer_benchmarks(listings)
    counts = {}
    for l in listings:
        counts[l.get("city")] = counts.get(l.get("city"), 0) + 1

    scored = []
    for l in listings:
        try:
            bm = benchmarks.get(l.get("city"))
            ppsqm, label = scoring.peer_ppsqm(bm, l.get("rooms"))
            info = meta.get(l["id"], {})
            res = scoring.score_listing(
                l, cfg,
                benchmark_ppsqm=ppsqm,
                benchmark_label=f"חציון המבוקש ({label})" if label else "חציון המבוקש",
                baseline=None,
                active_listings=counts.get(l.get("city"), 0),
                drop=latest_drop.get(l["id"]),
                first_seen=info.get("first_seen", today.isoformat()),
                today=today,
                stage="prescreen",
            )
            scored.append({**l, **res})
        except Exception:
            log.error("כשל בסינון מקדים למודעה %s:\n%s",
                      l.get("id"), traceback.format_exc())
    scored.sort(key=lambda s: s.get("score") or 0, reverse=True)
    return scored, benchmarks


# ------------------------------------------------------------------
# שלב 4 — ציון סופי מול עסקאות אמיתיות באזור הספציפי
# ------------------------------------------------------------------

def finalize(candidates, client, area_svc, cfg, meta, latest_drop, observed_sizes,
             counts, today, status, area_cache):
    """
    לכל מועמדת: העשרה מדף המודעה (ScraperAPI) + השוואה like-for-like מול
    עסקאות שנסגרו באזור הספציפי + תשואה + עליית ערך → ציון סופי ודירוג.

    ‎area_cache‎ נצבר תוך כדי ומשמש אחר כך לבלוק "areas" ב-JSON ולגרפים.
    """
    workers = max(1, int(cfg.get("finalize_concurrency", 3)))
    stop = {"budget": False}
    lock = threading.Lock()

    def work(s):
        try:
            # בלי client (ריצה ללא סריקה) אין העשרה — כל השאר עובד כרגיל,
            # כי מקור העסקאות והשכירות אינו עובר דרך ScraperAPI.
            if client and cfg.get("enrich_listings", True) and not stop["budget"]:
                try:
                    if yad2.enrich_listing(client, s):
                        status.enriched += 1
                except CreditBudgetExceeded:
                    stop["budget"] = True
                    status.budget_stopped = True

            baseline = area_svc.for_listing(s)
            sizes = observed_sizes.get(s.get("city")) or {}

            # ── השוואה like-for-like ──
            comp = comps.like_for_like(baseline, s, cfg, sizes)

            # ── עליית ערך אזורית (עם cache לפי אזור) ──
            area_key = (baseline or {}).get("area_key")
            area_info = None
            if area_key:
                with lock:
                    area_info = area_cache.get(area_key)
                if area_info is None:
                    area_info = comps.area_series(baseline, cfg, sizes)
                    with lock:
                        area_cache[area_key] = area_info

            # ── תשואת שכירות ──
            rent, rent_level = area_svc.rent_for(baseline)
            yield_info = comps.rental_yield(s, rent, cfg, rent_level)

            info = meta.get(s["id"], {})
            res = scoring.score_listing(
                s, cfg,
                baseline=baseline,
                active_listings=counts.get(s.get("city"), 0),
                drop=latest_drop.get(s["id"]),
                first_seen=info.get("first_seen", today.isoformat()),
                today=today,
                stage="final",
                comp=comp,
                yield_info=yield_info,
                area_info=area_info,
            )
            s.update(res)      # prescreen_score נשמר — res לא כולל אותו
            s["comp"] = comp
            s["yield_info"] = yield_info
            s["area_info"] = area_info
            s["comps_area"] = comp.get("comp_area") or s.get("city")
            s["comps_level"] = comp.get("comp_match_level_raw")
            s["comps_version"] = comp.get("comp_version")
            s["nadlan_link"] = nadlan.search_link(s.get("city"), comp.get("comp_area"))
        except Exception:
            log.error("כשל בציון סופי למודעה %s:\n%s",
                      s.get("id"), traceback.format_exc())

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, candidates))
    return candidates


# ------------------------------------------------------------------
# שלב 5 — השוואת אזורים
# ------------------------------------------------------------------

def build_city_rows(conn, cfg, area_svc, listings, today, observed, area_cache):
    """
    שורות 'השוואת אזורים' — בסיס רשמי לכל עיר שהניבה מודעות, כולל עליית
    ערך רב-שנתית. מוסיף גם את אזורי הערים ל-area_cache כדי שיהיו גרפים
    ובלוק areas גם לערים שלא הגיעה מהן אף מועמדת.
    """
    cities = []
    for l in listings:
        if l.get("city") and l["city"] not in cities:
            cities.append(l["city"])

    typical = cfg.get("typical_size_sqm") or {}
    rows = []
    for city in cities:
        try:
            b = area_svc.settlement(city)
        except Exception:
            log.error("כשל בבסיס היישוב %s:\n%s", city, traceback.format_exc())
            b = None
        if not b:
            continue

        # עליית ערך ברמת היישוב — נשמרת ל-DB, ל-JSON ולגרף המגמה
        area_info = None
        try:
            key = b.get("area_key")
            area_info = area_cache.get(key)
            if area_info is None:
                area_info = comps.area_series(b, cfg, observed.get(city) or {})
                area_cache[key] = area_info
            db.save_area_history(conn, key, area_info.get("years"),
                                 today.isoformat())
        except Exception:
            log.error("כשל בעליית ערך ל-%s:\n%s", city, traceback.format_exc())

        # חציון ₪/מ"ר ברמת העיר: מיצוע קטגוריות החדרים דרך גודל טיפוסי
        ppsqms = []
        for rooms, info in (b.get("rooms") or {}).items():
            size = typical.get(rooms)
            if size and info.get("avg_price_12m"):
                ppsqms.append(info["avg_price_12m"] / float(size))
        median_ppsqm = None
        if ppsqms:
            ppsqms.sort()
            mid = len(ppsqms) // 2
            median_ppsqm = (ppsqms[mid] if len(ppsqms) % 2
                            else (ppsqms[mid - 1] + ppsqms[mid]) / 2)

        row = {
            "city": city,
            "setl_code": b.get("setl_code"),
            "median_ppsqm": median_ppsqm,
            "avg_price_12m": b.get("all_rooms_avg_price"),
            "price_change_pct": b.get("price_change_pct"),
            "cagr_pct": (area_info or {}).get("cagr_pct"),
            "years_covered": (area_info or {}).get("years_covered"),
            "deals_12m_display": f'{b.get("quarters_with_data", 0)}/4 רבעונים עם עסקאות',
            "deals_12m": None,
            "active_listings": db.count_active(conn, city),
            "scanned_now": sum(1 for l in listings if l.get("city") == city),
            "population": b.get("population"),
            "data_version": b.get("data_version"),
            "nadlan_link": nadlan.search_link(city),
        }
        rows.append(row)
        try:
            db.save_city_stats(conn, city, {**row, **b}, today.isoformat())
        except Exception:
            log.error("כשל שמירת city_stats ל-%s:\n%s", city, traceback.format_exc())
    conn.commit()
    return rows


# ------------------------------------------------------------------
# ריצה מלאה
# ------------------------------------------------------------------

def _level_counts(scored):
    """התפלגות רמות ההשוואה — נכנסת לדו"ח כדי שרואים על מה מבוסס כל פער."""
    out = {}
    for s in scored:
        lvl = (s.get("value") or {}).get("comp_level_he") or "אין"
        out[lvl] = out.get(lvl, 0) + 1
    return out


def _confidence_counts(scored):
    out = {}
    for s in scored:
        c = (s.get("value") or {}).get("confidence_he") or "—"
        out[c] = out.get(c, 0) + 1
    return out


def print_report(report, sections_data):
    """
    דו"ח קצר למסך בסוף ריצה — מה נסרק, מה עלה, ומה נכשל.
    נכתב ל-stdout (ולא רק ללוג) כדי שהרצה ידנית תיתן תשובה מיידית.
    """
    sc = report.get("section_counts") or {}
    out = ['', '=== דו"ח ריצה — נדל"ן סקאוט ===']
    for k, v in report.get("summary_lines") or []:
        out.append(f"  {k}: {v}")
    hot = sections_data.get("hot_areas") or []
    if hot:
        out.append("  אזורים מובילים ב-CAGR: " + ", ".join(
            f'{h.get("area_name")} {h.get("cagr_pct"):+.1f}%' for h in hot))
    out.append(f'  מכ"ם: {sc.get("drops", 0)} ירידות, '
               f'{sc.get("sharp_drops", 0)} חדות | '
               f'ערך יחסי: {sc.get("best_value", 0)}')
    for n in report.get("notes") or []:
        out.append(f"  ⚠ {n}")
    print("\n".join(out))


def run(cfg, dry_run=False, today=None):
    today = today or date.today()
    offline = bool(cfg.get("no_scrape"))
    report = {"run_date": today.isoformat(), "notes": [], "summary_lines": [],
              "offline": offline, "credits_used": 0}
    threshold = float(cfg["alert_threshold"])

    # בדיקת השפיות של נוסחת ירידת המחיר — רצה בכל ריצה ונרשמת בדו"ח
    report["drop_assertion"] = pricing.assert_drop_math()
    if not report["drop_assertion"]["ok"]:
        report["notes"].append(
            "שימו לב: בדיקת נוסחת ירידת המחיר נכשלה — ראה לוג.")

    # --- ScraperAPI ---
    api_key = os.environ.get("SCRAPERAPI_KEY")

    # נדל"ן ממשלתי חוסם IP של רצים בענן (למשל GitHub Actions). כשמפעילים
    # nadlan_via_scraperapi, בקשות nadlan מנותבות דרך ScraperAPI (מצב רגיל,
    # ~קרדיט אחד לבקשה). ריצה חיה = timeout ארוך יותר לפרוקסי.
    nadlan_proxy = api_key if (api_key and not offline
                               and cfg.get("nadlan_via_scraperapi", True)) else None
    fetcher = Fetcher(
        delay=(0.5 if nadlan_proxy else cfg["request_delay_seconds"]),
        timeout=(70 if nadlan_proxy else cfg["request_timeout_seconds"]),
        proxy_key=nadlan_proxy)
    if nadlan_proxy:
        log.info("בקשות נדל\"ן ממשלתי מנותבות דרך ScraperAPI (מצב רגיל)")
    client, y2status = None, yad2.Yad2Status()
    if offline:
        # אף בקשה ל-ScraperAPI, גם לא ל-‎/account‎: הריצה כולה מהנתונים
        # שכבר נאספו. המדורים, הדשבורד והמייל נבנים מהם ומנדל"ן ממשלתי
        # (שהוא חינמי ובלי מכסה).
        log.info("מצב ללא סריקה (--no-scrape): לא תבוצע אף בקשה ל-ScraperAPI")
        report["notes"].append(
            "ריצה ללא סריקת יד2 (--no-scrape): המודעות נטענו מה-DB, "
            "לא נוצל אף קרדיט ScraperAPI. הנתונים הרשמיים נשלפו חיים.")
    elif not api_key:
        report["notes"].append(
            "חסר SCRAPERAPI_KEY בסביבה — יד2 לא נסרק. הוסף אותו ל-secrets.env.")
        log.error("חסר SCRAPERAPI_KEY — מדלג על סריקת יד2")
    else:
        client = ScraperApiClient(
            api_key,
            country_code=cfg.get("scraperapi_country", "il"),
            timeout=int(cfg.get("scraperapi_timeout_seconds", 90)),
            retries=int(cfg.get("scraperapi_retries", 3)),
            max_credits=int(cfg.get("scraperapi_max_credits_per_run", 3000)),
            ultra_premium=bool(cfg.get("scraperapi_ultra_premium", True)),
            credit_cost=int(cfg.get("scraperapi_credit_cost", 30)),
        )
        acct = account_info(api_key)
        if acct:
            log.info("ScraperAPI: %s קרדיטים נותרו מתוך %s",
                     acct.get("creditsLeft"), acct.get("requestLimit"))
            report["credits_left_before"] = acct.get("creditsLeft")

    # --- שלב 0: קודי ערים ---
    pairs, index = resolve_city_codes(fetcher, cfg, report)

    # --- שלב 1: סריקת יד2 ---
    listings = []
    if offline:
        pass                    # המודעות נטענות מה-DB אחרי פתיחת החיבור
    elif client and pairs:
        try:
            listings, y2status = yad2.fetch_all(client, pairs, cfg, y2status)
        except Exception:
            log.error("כשל כללי בסריקת יד2:\n%s", traceback.format_exc())
            report["notes"].append("סריקת יד2 נכשלה לחלוטין — ראה לוג.")
    elif client and not pairs:
        report["notes"].append("אין קודי ערים — יד2 לא נסרק.")

    if client and client.fatal_error:
        report["notes"].append(f"ScraperAPI החזיר שגיאה סופית: {client.fatal_error}")

    # --- שלב 2: התמדה ---
    conn = db.connect(cfg["paths"]["db"])
    scored, drops, city_rows, candidates = [], [], [], []
    area_svc = nadlan.AreaComps(fetcher, index)
    area_cache = {}          # area_key -> area_series(), משותף לכל השלבים
    dedup_stats, to_alert, watchlist = {}, [], []
    ladders, run_sections, radar_context = {}, {}, {}
    try:
        if offline:
            listings, meta, drops, latest_drop = load_from_db(conn, cfg)
        else:
            meta, drops, latest_drop = persist(conn, listings, cfg, today)

        # היסטוריית המחירים המצטברת — מקור המדור "מכ"ם ירידות מחיר"
        try:
            ladders = db.price_ladder(conn, "yad2",
                                      cfg.get("max_plausible_drop_pct"))
            # הקשר למודעות פעילות שלא נסרקו בריצה הזו (הסריקה חלקית לפי עמודים)
            radar_context = {r["id"]: {
                "id": r["id"], "city": r.get("city"),
                "neighborhood": r.get("neighborhood"), "url": r.get("url"),
                "rooms": r.get("rooms"), "size_sqm": r.get("size_sqm"),
                "nadlan_link": nadlan.search_link(r.get("city")),
            } for r in db.load_stored_listings(conn, "yad2")}
        except Exception:
            log.error("כשל בבניית סולם המחירים:\n%s", traceback.format_exc())

        # --- שלב 3: סינון מקדים ---
        scored, _benchmarks = prescreen(listings, cfg, meta, latest_drop, today)
        for s in scored:
            s["prescreen_score"] = s.get("score")

        gate = scoring.prescreen_gate(cfg)
        max_final = int(cfg.get("max_final_checks_per_run", 40))
        passing = [s for s in scored if (s.get("score") or 0) >= gate]
        candidates = passing[:max_final]
        if len(passing) > max_final:
            log.warning("%d מודעות עברו את השער אך נבדקות רק %d המובילות "
                        "(max_final_checks_per_run)", len(passing), max_final)
            report["notes"].append(
                f"{len(passing)} מודעות עברו את הסינון המקדים; נבדקו מול עסקאות "
                f"אמיתיות רק {max_final} המובילות (מגבלת max_final_checks_per_run).")
        log.info("סינון מקדים: %d מודעות, %d עברו שער %.0f, %d נבדקות לעומק",
                 len(scored), len(passing), gate, len(candidates))

        # --- שלב 4: ציון סופי ---
        observed = {}
        by_city = {}
        for l in listings:
            by_city.setdefault(l.get("city"), []).append(l)
        for c, ls in by_city.items():
            observed[c] = scoring.observed_median_sizes(ls)
        counts = {c: len(ls) for c, ls in by_city.items()}

        if candidates:
            try:
                finalize(candidates, client, area_svc, cfg, meta, latest_drop,
                         observed, counts, today, y2status, area_cache)
            except Exception:
                log.error("כשל בשלב הציון הסופי:\n%s", traceback.format_exc())
                report["notes"].append("שלב הציון הסופי נכשל חלקית — ראה לוג.")

        scored.sort(key=lambda s: s.get("score") or 0, reverse=True)

        # --- שלב 5: השוואת אזורים + עליית ערך ---
        try:
            city_rows = build_city_rows(conn, cfg, area_svc, listings, today,
                                        observed, area_cache)
        except Exception:
            log.error("כשל בבניית השוואת אזורים:\n%s", traceback.format_exc())

        # --- שלב 5ב: סולם ההשוואה המדורג — לכל המודעות, לא רק למועמדות ---
        # רץ אחרי בניית השוואת האזורים כי הוא נשען על הבסיסים הרשמיים
        # שכבר נשלפו שם. אפס בקשות נוספות.
        try:
            market = comps.MarketIndex(cfg)
            for c, b in area_svc.loaded_settlements().items():
                market.add_city(c, b, observed.get(c))
            market.build()
            for s in scored:
                s["value"] = comps.cascade(s, s.get("comp"), market, cfg)
                if not s.get("nadlan_link"):
                    s["nadlan_link"] = nadlan.search_link(
                        s.get("city"), (s.get("value") or {}).get("value_area"))
            report["comp_levels"] = _level_counts(scored)
            report["confidence_counts"] = _confidence_counts(scored)
        except Exception:
            log.error("כשל בסולם ההשוואה המדורג:\n%s", traceback.format_exc())
            report["notes"].append("סולם ההשוואה המדורג נכשל — ראה לוג.")

        # --- שלב 6: דדופ התראות ---
        # רק "לבדוק דחוף" נכנס לכותרת המייל; "שווה בדיקה" מוצג כרשימה.
        urgent = [s for s in scored if s.get("tier") == scoring.TIER_URGENT]
        watchlist = [s for s in scored if s.get("tier") == scoring.TIER_WORTH]
        try:
            state = db.alert_state(conn, [s["id"] for s in urgent])
            to_alert, _suppressed, dedup_stats = alerts.decide(
                urgent, state, cfg.get("alert_score_rise", 10))
        except Exception:
            log.error("כשל בדדופ ההתראות:\n%s", traceback.format_exc())
            report["notes"].append("דדופ ההתראות נכשל — נשלח בלי סינון כפילויות.")
            to_alert, dedup_stats = urgent, {}
    finally:
        conn.commit()
        conn.close()

    opportunities = to_alert
    report["dedup_stats"] = dedup_stats
    report["tier_counts"] = {
        "check-urgent": sum(1 for s in scored if s.get("tier") == scoring.TIER_URGENT),
        "worth-checking": len(watchlist),
        "watch": sum(1 for s in scored if s.get("tier") == scoring.TIER_WATCH),
    }

    # --- שלב 7: המדורים שתמיד יש בהם תוכן ---
    try:
        run_sections = sections.build(scored, ladders, area_cache, cfg,
                                      context=radar_context)
    except Exception:
        log.error("כשל בבניית המדורים:\n%s", traceback.format_exc())
        report["notes"].append("בניית המדורים נכשלה — ראה לוג.")
        run_sections = {"price_drop_radar": [], "best_relative_value": [],
                        "hot_areas": []}
    report["section_counts"] = sections.counts(run_sections)

    finals = [s for s in scored if s.get("stage") == "final"]
    with_comps = [s for s in finals if (s.get("comp") or {}).get("sufficient")]
    insufficient = len(finals) - len(with_comps)
    tc = report["tier_counts"]
    sc = report["section_counts"]

    data_versions = {r.get("data_version") for r in city_rows if r.get("data_version")}
    report["summary_lines"] = [
        ("ערים בסריקה", f"{len(pairs)}/{len(cfg['cities'])}"),
        ("מודעות" if offline else "מודעות שנסרקו",
         f"{len(listings)} (מה-DB, בלי סריקה)" if offline else len(listings)),
        ("עברו סינון מקדים", f"{len(candidates)} (שער {scoring.prescreen_gate(cfg):.0f})"),
        ("נבדקו מול עסקאות אמיתיות", len(finals)),
        ("נמצאו קומפים מספיקים", f"{len(with_comps)} (ל-{insufficient} אין מספיק)"),
        ("לבדוק דחוף", tc["check-urgent"]),
        ("שווה בדיקה", tc["worth-checking"]),
        ("למעקב", tc["watch"]),
        ("נשלחו בהתראה", alerts.summary_he(dedup_stats)),
        ('מכ"ם ירידות מחיר',
         f'{sc["drops"]} מודעות ({sc["sharp_drops"]} ירידות חדות)'),
        ("ערך יחסי מוביל", f'{sc["best_value"]} מודעות'),
        ("אזורים מתחממים", f'{sc["hot_areas"]} אזורים'),
        ("רמות ההשוואה",
         ", ".join(f"{k}: {v}" for k, v in
                   sorted((report.get("comp_levels") or {}).items(),
                          key=lambda x: -x[1])) or "—"),
        ("רמת ביטחון",
         ", ".join(f"{k}: {v}" for k, v in
                   sorted((report.get("confidence_counts") or {}).items(),
                          key=lambda x: -x[1])) or "—"),
        ("ירידות מחיר במעקב", len(drops)),
        ("בדיקת נוסחת ירידת מחיר", report["drop_assertion"]["text"]),
        ("מקור יד2", "לא נסרק בריצה הזו (--no-scrape)" if offline
         else y2status.summary_he()),
        ("נדל\"ן ממשלתי", area_svc.summary_he()),
    ]
    # עלות הריצה — תמיד בדו"ח, גם כשהיא אפס
    if client:
        report["summary_lines"].append(("ScraperAPI", client.summary_he()))
        report["credits_used"] = client.credits_used
    report["summary_lines"].append(
        ("קרדיטים שנוצלו בריצה", f'{report["credits_used"]}'
         + (" (ריצה ללא סריקה)" if offline else
            f' מתוך תקרה {cfg.get("scraperapi_max_credits_per_run", 200)}')))
    if report.get("credits_left_before") is not None:
        report["summary_lines"].append(
            ("קרדיטים שנותרו לפני הריצה", report["credits_left_before"]))
    if data_versions:
        report["summary_lines"].append(
            ("גרסת נתוני עסקאות רשמיים", ", ".join(sorted(data_versions))))

    if y2status.blocked:
        report["notes"].append(
            "יד2 לא החזיר מודעות בריצה הזו — ייתכן שההגנה (Radware) חסמה גם דרך "
            "ScraperAPI. ראה את הלוג לפירוט.")
    if y2status.budget_stopped:
        report["notes"].append(
            "הריצה נעצרה בתקרת הקרדיטים של ScraperAPI (scraperapi_max_credits_per_run).")
    if data_versions:
        report["notes"].append(
            "נתוני העסקאות הרשמיים מתעדכנים בפיגור — גרסת הנתונים הנוכחית: "
            + ", ".join(sorted(data_versions)) + ".")

    log.info("=== דו\"ח ריצה ===")
    for k, v in report["summary_lines"]:
        log.info("  %s: %s", k, v)
    for n in report["notes"]:
        log.info("  הערה: %s", n)

    # --- גרפים (לפני האקסל/JSON כדי ששמות הקבצים ייכללו בהם) ---
    charts_dir = cfg["paths"]["out"] / "charts"
    try:
        charts.build_all(opportunities + watchlist[:5], area_cache, charts_dir,
                         ladder_rows=run_sections.get("price_drop_radar"))
    except Exception:
        log.error("כשל בבניית הגרפים:\n%s", traceback.format_exc())
        report["notes"].append("בניית הגרפים נכשלה — ראה לוג.")

    xlsx = None
    try:
        xlsx, _ = excel.build_workbook(scored, drops, city_rows, report,
                                       cfg["paths"]["out"], threshold,
                                       areas=area_cache, sections=run_sections)
    except Exception:
        log.error("כשל בבניית האקסל:\n%s", traceback.format_exc())
        report["notes"].append("בניית האקסל נכשלה — ראה לוג.")

    dash = None
    try:
        dash = dashboard.write(run_sections, report, cfg, cfg["paths"]["out"],
                               areas=area_cache, listings_n=len(listings))
    except Exception:
        log.error("כשל בכתיבת הדשבורד:\n%s", traceback.format_exc())
        report["notes"].append("כתיבת dashboard.html נכשלה — ראה לוג.")

    # ── סימון "חדש": מודעה שהופיעה לראשונה בריצה הזו (first_seen == היום) ──
    # מאפשר ל-front-end להציג רק מודעות חדשות במקום את אותה רשימה כל יום.
    try:
        run_day = today.isoformat()
        _c = db.connect(cfg["paths"]["db"])
        try:
            seen_map, type_map = {}, {}
            for r in _c.execute(
                    "SELECT id, first_seen, last_seen, raw_json FROM listings").fetchall():
                seen_map[r["id"]] = (r["first_seen"], r["last_seen"])
                # פרטי מול תיווך — משוחזר מ-adType שב-raw_json
                at = ""
                try:
                    at = (json.loads(r["raw_json"] or "{}") or {}).get("adType") or ""
                except Exception:
                    at = ""
                type_map[r["id"]] = ("private" if at == "private"
                                     else "agency" if at else None)
        finally:
            _c.close()
        for s in scored:
            fs, ls = seen_map.get(s.get("id"), (None, None))
            s["first_seen"] = fs
            s["last_seen"] = ls
            s["listing_type"] = type_map.get(s.get("id"))
            s["is_new"] = bool(fs and str(fs).startswith(run_day))
            # ירידת מחיר טרייה = המחיר השתנה בריצה הזו
            lc = (ladders.get(s.get("id")) or {}).get("last_change_at") or ""
            s["dropped_today"] = bool(str(lc).startswith(run_day)
                                      and (s.get("drop_pct") or 0) > 0)
        log.info("סימון 'חדש': %d מודעות חדשות, %d ירידות טריות",
                 sum(1 for s in scored if s.get("is_new")),
                 sum(1 for s in scored if s.get("dropped_today")))
    except Exception:
        log.error("כשל בסימון 'חדש':\n%s", traceback.format_exc())

    try:
        jsonout.write(scored, area_cache, drops, city_rows, report,
                      cfg["paths"]["out"], report["notes"],
                      sections_data=run_sections, ladders=ladders)
    except Exception:
        log.error("כשל בכתיבת ה-JSON:\n%s", traceback.format_exc())
        report["notes"].append("כתיבת latest.json נכשלה — ראה לוג.")

    # ── front-end פרימיום (app.html) — קורא את latest.json ──
    try:
        webapp.write(cfg["paths"]["out"])
    except Exception:
        log.error("כשל בכתיבת app.html:\n%s", traceback.format_exc())
        report["notes"].append("כתיבת app.html נכשלה — ראה לוג.")

    # גרף המגמה של האזור המוביל — מצורף למייל
    trend_png = None
    hot = run_sections.get("hot_areas") or []
    for area in hot + list(area_cache.values()):
        name = area.get("chart") or area.get("chart_trend")
        if not name:
            key = area.get("area_key")
            name = (area_cache.get(key) or {}).get("chart_trend") if key else None
        if name and (charts_dir / name).is_file():
            trend_png = charts_dir / name
            break

    print_report(report, run_sections)

    if dry_run:
        log.info("dry-run: לא נשלח מייל")
        return report, xlsx, opportunities

    # הכותרת: קודם ממצא דחוף, ואם אין — התוכן שכן נמצא. "אין ממצאים"
    # לבדו הוא כותרת שמלמדת את הקורא להתעלם מהמייל.
    sc = report["section_counts"]
    if opportunities:
        subject = f'נדל"ן סקאוט — {len(opportunities)} לבדיקה דחופה'
    elif sc.get("sharp_drops"):
        subject = (f'נדל"ן סקאוט — {sc["sharp_drops"]} ירידות מחיר חדות, '
                   f'{sc["drops"]} ירידות בסך הכול')
    elif sc.get("drops"):
        subject = f'נדל"ן סקאוט — {sc["drops"]} ירידות מחיר במעקב'
    else:
        subject = f'נדל"ן סקאוט — {sc.get("best_value", 0)} מודעות בערך יחסי מוביל'
    if report.get("offline"):
        subject += " (ללא סריקה)"

    text, html = emailer.build_body(opportunities, report, threshold,
                                    watchlist=watchlist, dedup=dedup_stats,
                                    sections=run_sections)
    extra = [f for f in (dash, trend_png) if f]
    try:
        if emailer.send(cfg, subject, text, html, attachment=xlsx,
                        attachments=extra):
            # רושמים רק אחרי שליחה מוצלחת, אחרת מודעה "תדווח" בלי שנשלחה
            conn2 = db.connect(cfg["paths"]["db"])
            try:
                db.record_alerts(conn2, opportunities, today.isoformat())
                conn2.commit()
            finally:
                conn2.close()
    except Exception:
        log.error("כשל בשליחת המייל:\n%s", traceback.format_exc())

    return report, xlsx, opportunities


def main(argv=None):
    ap = argparse.ArgumentParser(description='נדל"ן סקאוט — סורק מודעות והזדמנויות בדרום')
    ap.add_argument("--config", default=None, help="נתיב ל-config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="בלי שליחת מייל")
    ap.add_argument("--verbose", action="store_true", help="לוג מפורט")
    ap.add_argument("--cities", default=None,
                    help="רשימת ערים מופרדת בפסיקים (עוקף את ה-config) — לבדיקות")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="עמודים לעיר (עוקף את ה-config) — לבדיקות")
    ap.add_argument("--no-scrape", action="store_true",
                    help="בלי אף בקשה ל-ScraperAPI: המודעות נטענות מה-DB "
                         "והנתונים הרשמיים נשלפים חיים (חינם)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cfg["no_scrape"] = bool(args.no_scrape)
    if args.cities:
        cfg["cities"] = [c.strip() for c in args.cities.split(",") if c.strip()]
    if args.max_pages is not None:
        cfg["yad2_max_pages_per_city"] = args.max_pages

    logfile = setup_logging(cfg, args.verbose)
    log.info('=== נדל"ן סקאוט — תחילת ריצה (לוג: %s) ===', logfile)

    try:
        run(cfg, dry_run=args.dry_run)
    except Exception:
        log.critical("כשל קריטי בריצה:\n%s", traceback.format_exc())
        return 1
    log.info("=== סוף ריצה ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
