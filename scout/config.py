"""טעינת קונפיגורציה וסודות."""
import os
import re
import logging
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SECRETS_CANDIDATES = [
    Path.home() / ".config" / "menutakim" / "secrets.env",
    ROOT / ".env",
]

log = logging.getLogger(__name__)

DEFAULTS = {
    "cities": ["באר שבע"],
    "city_groups": {},
    "price_min": 0,
    "price_max": 1500000,
    "property_type": "apartment",
    "alert_threshold": 70,
    "tiers": {"check_urgent": 78, "worth_checking": 66, "watch": 55},
    "weights": {
        "price_gap": 40,
        "price_drop": 15,
        "days_on_market": 8,
        "opportunity_keywords": 7,
        "area_liquidity": 5,
        "rental_yield": 15,
        "area_appreciation": 10,
    },
    "price_gap_full_at_pct": 25,
    "comps_months": 12,
    "days_on_market_full_at": 90,
    # השוואה like-for-like (ראה scout/comps.py למגבלות המקור)
    "comps_window_months": 18,
    "comps_min_count": 5,
    "comps_rooms_tolerance": 0.5,
    "comps_size_tolerance_pct": 20,
    "comps_gap_suspect_pct": 30,
    "comps_gap_min_pct": 8,
    "suspect_gap_score_factor": 0.5,
    # אותות תומכים
    "signal_min_drop_pct": 3,
    "signal_min_days_on_market": 60,
    "signal_liquidity_frac": 0.7,
    # תשואה ועליית ערך
    "rent_months_per_year": 12,
    "yield_floor_pct": 2.0,
    "yield_full_at_pct": 5.0,
    "yield_good_pct": 3.5,
    "appreciation_years": 5,
    "appreciation_min_quarters_per_year": 3,
    "cagr_full_at_pct": 6.0,
    "cagr_good_pct": 3.0,
    # שגיאות נתונים ודדופ התראות
    "max_plausible_drop_pct": 60,
    "alert_score_rise": 10,
    "keywords": ["דרוש שיפוץ", "גמיש", "בהזדמנות", "להשקעה", "מיידי", "ללא מעלית"],
    "alert_to": "adiromweb@gmail.com",
    "request_delay_seconds": 3,
    "request_timeout_seconds": 20,
    # סריקת יד2 דרך ScraperAPI (בקשה מוצלחת = קרדיט אחד)
    "yad2_max_pages_per_city": 3,
    "yad2_concurrency": 3,
    "scraperapi_country": "il",
    "scraperapi_retries": 3,
    "scraperapi_timeout_seconds": 90,
    "scraperapi_max_credits_per_run": 200,
    # שלב הבדיקה לעומק מול עסקאות אמיתיות
    "prescreen_margin": 15,
    "max_final_checks_per_run": 40,
    # תוכן שנשלח תמיד, גם כשאף מודעה לא עברה את השער המחמיר
    "sharp_drop_pct": 7.0,
    "best_value_top_n": 5,
    "hot_areas_top_n": 3,
    "market_tiers": 4,
    "comp_ladder_min_count": 5,
    "enrich_listings": True,
    "finalize_concurrency": 3,
    "inactive_after_days": 14,
    "apartment_types": ["דירה", "דירת גן", "דירת גג", "פנטהאוז",
                        "מיני פנטהאוז", "דופלקס", "סטודיו", "לופט"],
}


def load_secrets():
    """טוען משתני סביבה מקובץ secrets.env אם קיים. לא דורס משתנים קיימים."""
    pattern = re.compile(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$')
    for path in SECRETS_CANDIDATES:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                m = pattern.match(line)
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                os.environ.setdefault(key, val)
        except OSError as e:
            log.warning("לא ניתן לקרוא %s: %s", path, e)


def merge_cities(cities, groups):
    """
    ‎cities‎ + כל קבוצות ‎city_groups‎ → רשימה אחת, לפי הסדר ובלי כפילויות.

    הקבוצות קיימות כדי שאפשר יהיה להוסיף/להסיר אזור שלם (דרום / שפלה /
    מרכז) בעריכה אחת בלי לגעת בקוד. קבוצה ריקה או מוערת פשוט לא תורמת ערים.
    """
    out, seen = [], set()
    for name in list(cities or []) + [
            c for g in (groups or {}).values() for c in (g or [])]:
        key = str(name).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def load_config(path=None):
    """טוען config.yaml ומשלב עם ברירות מחדל."""
    load_secrets()
    cfg = dict(DEFAULTS)
    cfg["weights"] = dict(DEFAULTS["weights"])
    cfg["tiers"] = dict(DEFAULTS["tiers"])

    cfg_path = Path(path) if path else ROOT / "config.yaml"
    if cfg_path.is_file():
        try:
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            # מיזוג ולא דריסה — כדי שמפתח חסר ב-config לא ימחק ברירת מחדל
            weights = loaded.pop("weights", None)
            tiers = loaded.pop("tiers", None)
            cfg.update(loaded)
            if isinstance(weights, dict):
                cfg["weights"].update(weights)
            if isinstance(tiers, dict):
                cfg["tiers"].update(tiers)
        except (OSError, yaml.YAMLError) as e:
            log.error("שגיאה בקריאת %s: %s — ממשיך עם ברירות מחדל", cfg_path, e)
    else:
        log.warning("לא נמצא %s — ממשיך עם ברירות מחדל", cfg_path)

    cfg["cities"] = merge_cities(cfg.get("cities"), cfg.get("city_groups"))
    cfg["alert_to"] = os.environ.get("ALERT_TO") or cfg.get("alert_to")
    cfg["gmail_address"] = os.environ.get("GMAIL_ADDRESS")
    cfg["gmail_app_password"] = os.environ.get("GMAIL_APP_PASSWORD")
    cfg["paths"] = {
        "root": ROOT,
        "db": ROOT / "data" / "nadlan.db",
        "out": ROOT / "out",
        "logs": ROOT / "logs",
    }
    return cfg
