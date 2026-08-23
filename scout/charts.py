"""
גרפים ל-‎out/charts/*.png‎ — שני סוגים:

  1. ‎area-<key>.png‎  — מגמת ₪/מ"ר באזור לאורך 5 שנים (קו).
  2. ‎comps-<id>.png‎  — התפלגות הקומפים של עסקה מוכתרת, עם המודעה מסומנת.

הערות עיצוב (לפי מתודת ה-dataviz):
  * ציר y יחיד תמיד; אין ציר כפול.
  * צבעים נלקחים כלשונם מפלטת הייחוס המאומתת — כחול סדרה ‎#2a78d6‎ ואדום
    ‎#d03b3b‎ למצב "קריטי" (כאן: המודעה עצמה). הם משמשים עם תווית ישירה
    ולא לבד, כך שהזיהוי אינו נשען על צבע בלבד.
  * גריד וצירים רסיסיים, קו 2px, סמנים ≥8px, תוויות ישירות נבחרות בלבד.

עברית ב-matplotlib: אין תמיכת bidi מובנית, ולכן כל מחרוזת עוברת ‎_rtl()‎
לפני הציור. בלי זה הכיתוב מופיע הפוך.
"""
import logging
import re

log = logging.getLogger(__name__)

# ---- פלטת הייחוס (ראה references/palette.md) ----
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES = "#2a78d6"          # קטגורי 1 — כחול
SERIES_SOFT = "#9ec5f4"     # אותו גוון, מדרגה בהירה
LISTING = "#d03b3b"         # status/critical — המודעה שאנחנו בודקים
GOOD = "#0ca30c"

_HEB = re.compile(r"[֐-׿]")


def _rtl(s):
    """היפוך bidi לטקסט עברי. מחרוזת בלי עברית מוחזרת כמות שהיא."""
    s = "" if s is None else str(s)
    if not _HEB.search(s):
        return s
    try:
        from bidi.algorithm import get_display
        return get_display(s)
    except Exception:
        return s


def _setup():
    """מייבא matplotlib במצב ללא-מסך ומחזיר (plt, ok)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams.update({
            "font.family": "DejaVu Sans",     # הגופן היחיד כאן עם גליפים עבריים
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_SECONDARY,
            "text.color": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
        })
        return plt, True
    except Exception as e:
        log.warning("matplotlib לא זמין — מדלג על גרפים: %s", e)
        return None, False


def _safe_name(s):
    """שם קובץ בטוח מתוך מזהה/שם עברי."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s))[:80].strip("_") or "x"


def area_trend(area, out_dir):
    """
    גרף מגמה: חציון ₪/מ"ר באזור לפי שנה. מחזיר שם קובץ או None.
    שנה בלי כיסוי מלא מסומנת בסמן חלול — הנתון שם חלקי.
    """
    plt, ok = _setup()
    if not ok:
        return None
    years = [y for y in (area.get("years") or []) if y.get("median_ppm")]
    if len(years) < 2:
        return None

    xs = [y["year"] for y in years]
    ys = [y["median_ppm"] for y in years]
    full = [(y.get("deal_quarters") or 0) >= 4 for y in years]

    fig, ax = plt.subplots(figsize=(7.2, 3.9), dpi=150)
    ax.plot(xs, ys, color=SERIES, linewidth=2.0, zorder=3,
            solid_capstyle="round")
    # סמן מלא = שנה עם 4 רבעונים; חלול = כיסוי חלקי
    for x, y, f in zip(xs, ys, full):
        ax.plot([x], [y], marker="o", markersize=8, zorder=4,
                color=SERIES if f else SURFACE,
                markeredgecolor=SERIES, markeredgewidth=2.0)

    # תוויות ישירות רק לקצוות — לא מספר על כל נקודה
    for i in (0, len(xs) - 1):
        ax.annotate(f"{ys[i]:,.0f}", (xs[i], ys[i]),
                    textcoords="offset points", xytext=(0, 11),
                    ha="center", fontsize=9, color=INK, fontweight="bold")

    # חשוב: בונים את המחרוזת **במלואה** ורק אז מפעילים _rtl. הפעלת bidi על
    # מקטעים ואיחודם אחר כך הופכת את סדר המקטעים ומייצרת כותרת משובשת.
    cagr = area.get("cagr_pct")
    level_he = "שכונה" if area.get("area_level") == "neighborhood" else "יישוב"
    sub = _rtl(f"רמת {level_he} · נתונים רשמיים {area.get('data_version') or ''}")
    title = f"מגמת מחיר — {area.get('area_name') or area.get('city') or ''}"
    if cagr is not None:
        title += f" ({cagr:+.1f}% לשנה)"

    ax.set_title(_rtl(title), fontsize=12.5, color=INK, fontweight="bold", pad=14)
    ax.set_ylabel(_rtl('חציון ₪ למ"ר'), fontsize=10)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(x) for x in xs], fontsize=9)
    ax.grid(axis="x", visible=False)
    ax.margins(x=0.08, y=0.22)
    ax.text(0.0, -0.19, sub, transform=ax.transAxes, fontsize=8.5,
            color=MUTED, ha="left")
    ax.text(1.0, -0.19, _rtl("סמן חלול = שנה עם כיסוי חלקי"),
            transform=ax.transAxes, fontsize=8.5, color=MUTED, ha="right")

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"area-{_safe_name(area.get('area_key') or area.get('area_name'))}.png"
    fig.savefig(out_dir / name, bbox_inches="tight")
    plt.close(fig)
    return name


def comps_distribution(listing, out_dir):
    """
    התפלגות ₪/מ"ר של הקומפים, עם המודעה מסומנת ביחס אליהם.
    רצועת נקודות ולא היסטוגרמה — מספר התצפיות קטן (5–13), והיסטוגרמה
    בגודל כזה ממציאה צורה שאין בנתונים. מחזיר שם קובץ או None.
    """
    plt, ok = _setup()
    if not ok:
        return None
    comp = listing.get("comp") or {}
    vals = sorted(v for v in (comp.get("comp_ppm_values") or []) if v)
    mine = listing.get("price_per_sqm")
    med = comp.get("comp_median_ppm")
    if len(vals) < 2 or not mine or not med:
        return None

    fig, ax = plt.subplots(figsize=(7.4, 3.3), dpi=150)

    # פיזור אנכי קל כדי שנקודות קרובות לא יסתירו זו את זו
    span = (max(vals) - min(vals)) or 1.0
    offsets, seen = [], []
    for v in vals:
        k = sum(1 for s in seen if abs(s - v) < span * 0.04)
        seen.append(v)
        offsets.append((k % 3 - 1) * 0.14)

    ax.scatter(vals, offsets, s=95, color=SERIES, alpha=0.85, zorder=3,
               edgecolors=SURFACE, linewidths=2.0)
    ax.axvline(med, color=INK_SECONDARY, linewidth=1.6, linestyle="--", zorder=2)
    ax.axvline(mine, color=LISTING, linewidth=2.4, zorder=5)
    ax.plot([mine], [0.52], marker="v", markersize=12, color=LISTING, zorder=6,
            markeredgecolor=SURFACE, markeredgewidth=1.5)

    # התוויות בגבהים שונים כדי שלא יתנגשו זו בזו; יישור לפי הצד שבו
    # הקו יושב, כדי שטקסט בקצה לא ייחתך מחוץ לשטח הציור
    lo, hi = min(min(vals), mine), max(max(vals), mine)
    def _ha(x):
        return "left" if (x - lo) < (hi - lo) * 0.5 else "right"

    ax.annotate(_rtl(f"חציון {med:,.0f}"), (med, 0.80),
                textcoords="offset points", xytext=(6 if _ha(med) == "left" else -6, 0),
                ha=_ha(med), fontsize=9, color=INK_SECONDARY)
    gap = listing.get("gap_pct")
    lbl = f"המודעה {mine:,.0f}"
    if gap is not None:
        lbl += f" ({gap:+.0f}%)"
    ax.annotate(_rtl(lbl), (mine, -0.72),
                textcoords="offset points", xytext=(6 if _ha(mine) == "left" else -6, 0),
                ha=_ha(mine), fontsize=9.5, color=LISTING, fontweight="bold")

    lvl = comp.get("comp_match_level")
    area = comp.get("comp_area") or listing.get("city")
    ax.set_title(_rtl(f"{listing.get('city') or ''} · {listing.get('rooms') or '?'} חד' · "
                      f"מול עסקאות ב{lvl} {area}"),
                 fontsize=11.5, color=INK, fontweight="bold", pad=12)
    ax.set_xlabel(_rtl('₪ למ"ר'), fontsize=10)
    ax.set_yticks([])
    ax.set_ylim(-1.0, 1.0)
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.16)
    ax.spines["left"].set_visible(False)
    # בלי תיבת מקרא: היא התנגשה בתוויות הישירות כשהמודעה יושבת בקצה ימין.
    # זהות הסימנים נמסרת במילים בכיתוב התחתון, כך שהיא אינה נשענת על צבע.
    ax.text(0.0, -0.34,
            _rtl(f"נקודות = עסקאות להשוואה ({len(vals)} תצפיות רבעון) · "
                 f"קו מקווקו = חציון · קו מלא = המודעה · "
                 f"חלון {comp.get('comp_window_months')} חודשים"),
            transform=ax.transAxes, fontsize=8.5, color=MUTED, ha="left")

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"comps-{_safe_name(listing.get('id'))}.png"
    fig.savefig(out_dir / name, bbox_inches="tight")
    plt.close(fig)
    return name


def price_ladder(row, out_dir):
    """
    סולם המחירים של מודעה אחת לאורך זמן — כל ירידה כמדרגה.

    צורת המדרגות (‎step‎) ולא קו ישר: המחיר אינו משתנה ברציפות בין
    התאריכים אלא קופץ ביום שהמוכר עדכן אותו. קו ישר היה ממציא ירידה
    הדרגתית שלא קרתה.
    מחזיר שם קובץ או None.
    """
    plt, ok = _setup()
    if not ok:
        return None
    pts = [p for p in (row.get("points") or []) if p.get("price")]
    if len(pts) < 2:
        return None

    from datetime import datetime
    xs, ys = [], []
    for p in pts:
        try:
            xs.append(datetime.fromisoformat(str(p["seen_at"])[:10]).date())
        except (TypeError, ValueError):
            continue
        ys.append(float(p["price"]))
    if len(xs) != len(ys) or len(xs) < 2:
        return None

    fig, ax = plt.subplots(figsize=(7.0, 3.4), dpi=150)
    down = (ys[-1] < ys[0])
    color = LISTING if down else SERIES
    ax.step(xs, ys, where="post", color=color, linewidth=2.0, zorder=3)
    ax.scatter(xs, ys, s=64, color=color, zorder=4,
               edgecolors=SURFACE, linewidths=2.0)

    # תווית על כל מדרגה — כאן זה מוצדק: יש 2–6 נקודות וכל אחת היא אירוע
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:,.0f}", (x, y), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=8.5, color=INK)

    total = row.get("total_drop_pct") or 0
    title = f"{row.get('city') or ''} · {row.get('rooms') or '?'} חד' · "
    title += (f"ירדה {total:.1f}% ב-{row.get('num_drops') or 0} שלבים"
              if down else "מהלך מחיר")
    ax.set_title(_rtl(title), fontsize=11.5, color=INK, fontweight="bold", pad=12)
    ax.set_ylabel(_rtl("מחיר מבוקש (₪)"), fontsize=10)
    ax.set_xticks(xs)
    ax.set_xticklabels([d.strftime("%d/%m") for d in xs], fontsize=9)
    ax.grid(axis="x", visible=False)
    ax.margins(x=0.12, y=0.28)
    ax.text(0.0, -0.22, _rtl(f"מקור: היסטוריית המחירים שנצברה במערכת "
                             f"({row.get('num_points') or len(xs)} תצפיות)"),
            transform=ax.transAxes, fontsize=8.5, color=MUTED, ha="left")

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"ladder-{_safe_name(row.get('id'))}.png"
    fig.savefig(out_dir / name, bbox_inches="tight")
    plt.close(fig)
    return name


def build_all(headlined, areas, out_dir, ladder_rows=None, max_ladders=12):
    """
    מייצר את כל הגרפים. לא זורק חריגות — גרף שנכשל פשוט לא נוצר.
    מחזיר dict סיכום לדו"ח.
    """
    made = {"area": 0, "comps": 0, "ladder": 0, "failed": 0}
    for key, area in (areas or {}).items():
        try:
            name = area_trend(area, out_dir)
            if name:
                area["chart_trend"] = name
                made["area"] += 1
        except Exception as e:
            made["failed"] += 1
            log.warning("כשל בגרף מגמה לאזור %s: %s", key, e)

    for s in headlined or []:
        try:
            name = comps_distribution(s, out_dir)
            if name:
                s["chart_comps"] = name
                made["comps"] += 1
        except Exception as e:
            made["failed"] += 1
            log.warning("כשל בגרף קומפים למודעה %s: %s", s.get("id"), e)

    # סולם מחירים — למובילות במכ"ם הירידות (הכי הרבה לספר עליהן)
    for row in (ladder_rows or [])[:max_ladders]:
        try:
            name = price_ladder(row, out_dir)
            if name:
                row["chart"] = name
                made["ladder"] += 1
        except Exception as e:
            made["failed"] += 1
            log.warning("כשל בגרף סולם מחירים למודעה %s: %s", row.get("id"), e)

    log.info("גרפים: %d מגמת אזור, %d התפלגות קומפים, %d סולם מחירים, %d נכשלו",
             made["area"], made["comps"], made["ladder"], made["failed"])
    return made
