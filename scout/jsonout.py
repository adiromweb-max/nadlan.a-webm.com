"""
‎out/latest.json‎ — הפלט המכונתי המלא.

כל מודעה עם כל השדות שמופיעים באקסל ועוד (פירוט הניקוד, ערכי הקומפים
הגולמיים), ובנוסף בלוק ‎areas‎ עם חציונים שנתיים, מספר רבעונים עם עסקאות
ו-CAGR לכל אזור. זהו הקובץ שמאפשר לבדוק כל מספר בדו"ח בלי לפתוח אקסל.
"""
import json
import logging

log = logging.getLogger(__name__)


def _num(v, nd=None):
    """מספר נקי ל-JSON — בלי numpy, בלי NaN, בלי מחרוזות מספריות."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):    # NaN / אינסוף
        return None
    return round(f, nd) if nd is not None else f


def listing_record(s, ladder=None):
    """מודעה אחת כרשומת JSON שטוחה-יחסית ומלאה."""
    comp = s.get("comp") or {}
    yinfo = s.get("yield_info") or {}
    ainfo = s.get("area_info") or {}
    value = s.get("value") or {}
    lad = ladder or {}
    return {
        # ── היסטוריית מחיר מצטברת ──
        "original_price": _num(lad.get("original_price")),
        "current_price": _num(lad.get("current_price") or s.get("price")),
        "total_drop_pct": _num(lad.get("total_drop_pct"), 2),
        "num_drops": lad.get("num_drops", 0),
        "num_price_points": lad.get("num_points", 0),
        "last_drop_pct": _num(lad.get("last_drop_pct"), 2),
        "last_price_change_at": lad.get("last_change_at"),
        "price_history_text": lad.get("history_text"),
        "price_history": [{"price": _num(p.get("price")),
                           "seen_at": p.get("seen_at")}
                          for p in (lad.get("points") or [])],
        "price_drop_suspect": lad.get("suspect"),

        # ── סולם ההשוואה המדורג ──
        "comp_level": value.get("comp_level"),
        "comp_level_he": value.get("comp_level_he"),
        "confidence": value.get("confidence"),
        "confidence_he": value.get("confidence_he"),
        "value_gap_pct": _num(value.get("value_gap_pct"), 2),
        "value_median_ppm": _num(value.get("value_median_ppm"), 1),
        "value_count": value.get("value_count"),
        "value_area": value.get("value_area"),
        "value_tag": value.get("value_tag"),
        "value_suspect": value.get("suspect"),
        "unverified_attractive": value.get("unverified_attractive"),
        "comp_ladder": [{
            "level": r.get("level"), "level_he": r.get("level_he"),
            "area_name": r.get("area_name"), "count": r.get("count"),
            "median_ppm": _num(r.get("median_ppm"), 1),
            "gap_pct": _num(r.get("gap_pct"), 2),
            "sufficient": r.get("sufficient"),
        } for r in (value.get("ladder") or [])],
        "id": s.get("id"),
        "source": s.get("source"),
        "url": s.get("url"),
        "nadlan_link": s.get("nadlan_link"),
        "first_seen": s.get("first_seen"),
        "last_seen": s.get("last_seen"),
        "listing_type": s.get("listing_type"),
        "disqualified": bool(s.get("disqualified")),
        "suspicious_price": bool(s.get("suspicious_price")),
        "is_new": bool(s.get("is_new")),
        "dropped_today": bool(s.get("dropped_today")),
        "city": s.get("city"),
        "neighborhood": s.get("neighborhood"),
        "street": s.get("street"),
        "lat": _num(s.get("lat")),
        "lon": _num(s.get("lon")),
        "property_type": s.get("property_type"),
        "condition_text": s.get("condition_text"),
        "rooms": _num(s.get("rooms")),
        "size_sqm": _num(s.get("size_sqm")),
        "price": _num(s.get("price")),
        "price_per_sqm": _num(s.get("price_per_sqm"), 1),

        # ── השוואה like-for-like ──
        "comp_median_ppm": _num(comp.get("comp_median_ppm"), 1),
        "comp_count": comp.get("comp_count"),
        "comp_basis": comp.get("comp_basis"),
        "comp_match_level": comp.get("comp_match_level"),
        "comp_match_level_raw": comp.get("comp_match_level_raw"),
        "comp_area": comp.get("comp_area"),
        "comp_rooms": comp.get("comp_rooms"),
        "comp_window_months": comp.get("comp_window_months"),
        "comp_size_used": _num(comp.get("comp_size_used"), 1),
        "comp_size_source": comp.get("comp_size_source"),
        "comp_ppm_values": comp.get("comp_ppm_values"),
        "comp_sufficient": comp.get("sufficient"),
        "comp_suspect": comp.get("suspect"),
        "comp_version": comp.get("comp_version"),

        # ── ניקוד ודירוג ──
        "gap_pct": _num(s.get("gap_pct"), 2),
        "drop_pct": _num(s.get("drop_pct"), 2),
        "days_on_market": s.get("days_on_market"),
        "yield_pct": _num(s.get("yield_pct"), 2),
        "monthly_rent_est": _num(s.get("monthly_rent_est")),
        "rent_basis": s.get("rent_basis"),
        "rent_is_estimate": True,
        "area_cagr_pct": _num(s.get("area_cagr_pct"), 2),
        "area_key": ainfo.get("area_key"),
        "score": _num(s.get("score"), 2),
        "prescreen_score": _num(s.get("prescreen_score"), 2),
        "stage": s.get("stage"),
        "tier": s.get("tier"),
        "tier_he": s.get("tier_he"),
        "opportunity_type": s.get("opportunity_type"),
        "opportunity_type_he": s.get("opportunity_type_he"),
        "has_signal": s.get("has_signal"),
        "signals": s.get("signals"),
        "keywords_found": s.get("keywords_found"),
        "data_quality": s.get("data_quality"),
        "reason": s.get("reason"),
        "breakdown": {k: _num(v, 2) for k, v in (s.get("breakdown") or {}).items()},

        # ── מצב התראה ──
        "alert_status": s.get("alert_status"),
        "alert_reason": s.get("alert_reason"),
        "first_alerted_at": s.get("first_alerted_at"),
        "chart": s.get("chart_comps"),
    }


def area_record(area):
    """אזור אחד: חציונים שנתיים, מספר רבעונים עם עסקאות, CAGR."""
    return {
        "area_key": area.get("area_key"),
        "area_name": area.get("area_name"),
        "area_level": area.get("area_level"),
        "city": area.get("city"),
        "data_version": area.get("data_version"),
        "cagr_pct": _num(area.get("cagr_pct"), 2),
        "cagr_from_year": area.get("cagr_from_year"),
        "cagr_to_year": area.get("cagr_to_year"),
        "years_covered": area.get("years_covered"),
        "ref_size_sqm": _num(area.get("ref_size_sqm"), 1),
        "chart": area.get("chart_trend"),
        "yearly": [{
            "year": y.get("year"),
            "median_ppm": _num(y.get("median_ppm"), 1),
            "median_price": _num(y.get("median_price")),
            "deal_quarters": y.get("deal_quarters"),
        } for y in (area.get("years") or [])],
    }


def sections_block(sections_data):
    """שלושת המדורים כפי שהם — הצרכן החיצוני (דשבורד/בוט) קורא מכאן."""
    s = sections_data or {}
    return {
        "price_drop_radar": [{
            "listing_id": r.get("id"), "city": r.get("city"),
            "neighborhood": r.get("neighborhood"), "url": r.get("url"),
            "rooms": _num(r.get("rooms")), "size_sqm": _num(r.get("size_sqm")),
            "original_price": _num(r.get("original_price")),
            "current_price": _num(r.get("current_price")),
            "total_drop_pct": _num(r.get("total_drop_pct"), 2),
            "num_drops": r.get("num_drops"),
            "last_drop_pct": _num(r.get("last_drop_pct"), 2),
            "last_change_at": r.get("last_change_at"),
            "price_history_text": r.get("history_text"),
            "price_history": [{"price": _num(p.get("price")),
                               "seen_at": p.get("seen_at")}
                              for p in (r.get("points") or [])],
            "sharp_drop": r.get("sharp"), "suspect": r.get("suspect"),
            "value_gap_pct": _num(r.get("value_gap_pct"), 2),
            "comp_level_he": r.get("comp_level_he"),
            "confidence": r.get("confidence"),
            "chart": r.get("chart"),
        } for r in (s.get("price_drop_radar") or [])],
        "best_relative_value": [{
            "listing_id": r.get("id"), "city": r.get("city"),
            "neighborhood": r.get("neighborhood"), "url": r.get("url"),
            "nadlan_link": r.get("nadlan_link"),
            "price": _num(r.get("price")),
            "price_per_sqm": _num(r.get("price_per_sqm"), 1),
            "rooms": _num(r.get("rooms")), "size_sqm": _num(r.get("size_sqm")),
            "value_gap_pct": _num(r.get("value_gap_pct"), 2),
            "value_median_ppm": _num(r.get("value_median_ppm"), 1),
            "value_count": r.get("value_count"),
            "value_area": r.get("value_area"),
            "comp_level": r.get("comp_level"),
            "comp_level_he": r.get("comp_level_he"),
            "confidence": r.get("confidence"),
            "confidence_he": r.get("confidence_he"),
            "value_tag": r.get("value_tag"),
            "unverified_attractive": r.get("unverified_attractive"),
            "suspect": r.get("suspect"),
            "score": _num(r.get("score"), 2), "tier_he": r.get("tier_he"),
            "yield_pct": _num(r.get("yield_pct"), 2),
            "area_cagr_pct": _num(r.get("area_cagr_pct"), 2),
            "chart": r.get("chart"),
        } for r in (s.get("best_relative_value") or [])],
        "hot_areas": [{
            "area_key": r.get("area_key"), "area_name": r.get("area_name"),
            "area_level": r.get("area_level"), "city": r.get("city"),
            "cagr_pct": _num(r.get("cagr_pct"), 2),
            "years_covered": r.get("years_covered"),
            "cagr_from_year": r.get("cagr_from_year"),
            "cagr_to_year": r.get("cagr_to_year"),
            "latest_median_ppm": _num(r.get("latest_median_ppm"), 1),
            "first_median_ppm": _num(r.get("first_median_ppm"), 1),
            "deal_quarters": r.get("deal_quarters"),
            "data_version": r.get("data_version"),
            "chart": r.get("chart"),
        } for r in (s.get("hot_areas") or [])],
    }


def write(scored, areas, drops, city_rows, report, out_dir, notes=None,
          sections_data=None, ladders=None):
    """
    כותב ‎out/latest.json‎. מחזיר את הנתיב, או None בכשל (לא מפיל ריצה).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.json"

    doc = {
        "run_date": report.get("run_date"),
        "generated_by": "nadlan-scout",
        "schema_version": 2,
        "notes": notes or [],
        "data_caveats": {
            "comps": ("מקור העסקאות אינו חושף עסקה בודדת. comp_count סופר "
                      "תצפיות רבעון (מחיר ממוצע מפורסם לקטגוריית חדרים), "
                      "לא עסקאות בודדות. ראה scout/comps.py."),
            "yield": ("תשואה ברוטו מוערכת משכר הדירה הממוצע הרשמי באזור, "
                      "לא משכר דירה בפועל של הנכס. בלי ניכוי הוצאות."),
            "size": ("₪/מ\"ר רשמי מחושב כמחיר עסקה ממוצע חלקי גודל טיפוסי — "
                     "הנתון הרשמי אינו כולל מ\"ר."),
            "lag": "נתוני העסקאות הרשמיים מתעדכנים בפיגור של מספר חודשים.",
        },
        "summary": {k: v for k, v in (report.get("summary_lines") or [])},
        "tier_counts": report.get("tier_counts") or {},
        "section_counts": report.get("section_counts") or {},
        "comp_levels": report.get("comp_levels") or {},
        "confidence_counts": report.get("confidence_counts") or {},
        "credits_used": report.get("credits_used"),
        "offline_run": bool(report.get("offline")),
        "drop_assertion": report.get("drop_assertion"),
        "dedup": report.get("dedup_stats"),
        "sections": sections_block(sections_data),
        "listings": [listing_record(s, (ladders or {}).get(s.get("id")))
                     for s in scored],
        "areas": [area_record(a) for a in (areas or {}).values()],
        "price_drops": [{
            "listing_id": d.get("listing_id"),
            "city": d.get("city"),
            "url": d.get("url"),
            "old_price": _num(d.get("old_price")),
            "new_price": _num(d.get("new_price")),
            "drop_pct": _num(d.get("drop_pct"), 2),
            "changed_at": d.get("changed_at"),
            "suspect": d.get("suspect"),
        } for d in (drops or [])],
        "cities": [{
            "city": c.get("city"),
            "setl_code": c.get("setl_code"),
            "median_ppsqm": _num(c.get("median_ppsqm"), 1),
            "avg_price_12m": _num(c.get("avg_price_12m")),
            "price_change_pct": _num(c.get("price_change_pct"), 2),
            "scanned_now": c.get("scanned_now"),
            "active_listings": c.get("active_listings"),
            "population": c.get("population"),
            "data_version": c.get("data_version"),
        } for c in (city_rows or [])],
    }

    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    log.info("נשמר JSON: %s (%d מודעות, %d אזורים)",
             path.name, len(doc["listings"]), len(doc["areas"]))
    return path
