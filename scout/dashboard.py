"""
‎out/dashboard.html‎ — עמוד עברי RTL אחד, עצמאי לחלוטין.

"עצמאי" פשוטו כמשמעו: אין קובץ CSS חיצוני, אין פונט מרוחק, אין JavaScript
ואין ‎<img>‎ לקבצים שכנים. כל הגרפים הם SVG מוטבע. אפשר לצרף את הקובץ
למייל, לפתוח אותו במחשב מנותק או לשלוח אותו בוואטסאפ — והוא ייראה זהה.

מה יש בעמוד (בדיוק מה שהריצה ייצרה, בלי תלות בשער המחמיר):
  1. מכ"ם ירידות מחיר — התוכן הראשי.
  2. הערך היחסי הטוב ביותר — 5 מודעות, כל אחת עם רמת הביטחון שלה.
  3. מגמת אזורים ל-5 שנים — גרף קווים.
  4. לוח עליית הערך (CAGR) — דירוג האזורים.

עיצוב הגרפים לפי מתודת ה-dataviz:
  * ציר y יחיד, לעולם לא כפול.
  * צבעים כלשונם מפלטת הייחוס המאומתת: משבצות קטגוריות 1–3 בלבד
    (‎#2a78d6‎ כחול, ‎#eb6834‎ כתום, ‎#1baf7a‎ אקווה) — השלישייה הזו היא
    היחידה שמאומתת ל-‎--pairs all‎ בשני המצבים, ולכן גרף הקווים חסום
    ב-3 סדרות. סדרה רביעית לא מקבלת גוון מומצא אלא פשוט לא נכנסת לגרף.
  * ‎#d03b3b‎ (critical) לירידה חדה — תמיד עם תווית מילולית, לעולם לא צבע לבד.
  * מקרא קיים כשיש 2+ סדרות, ובנוסף תווית ישירה על כל קו.
  * מצב כהה נבחר במפורש מאותן רמפות, ולא היפוך אוטומטי.
"""
import html
import logging
from datetime import date

log = logging.getLogger(__name__)

# ---- פלטה: כרום במיתוג A-WEB, צבעי הגרפים מפלטת הייחוס המאומתת ----
# חשוב: --s1/s2/s3, --grid, --axis, --critical, --seq-soft הם צבעי הנתונים
# מ-references/palette.md ואסור לשנותם (הם מאומתים ל-CVD). האקסנט הסגול-
# מג'נטה (--accent) הוא כרום מותג בלבד ולעולם אינו משמש כצבע סדרה בגרף.
LIGHT = {
    "page": "#f2f1ec", "surface": "#ffffff", "surface2": "#faf8f4",
    "border": "#e8e7e2", "hair2": "#f1f0ec",
    "ink": "#111820", "ink2": "#454b54", "muted": "#8b8f96",
    "grid": "#e9e8e3", "axis": "#c9c8c2",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a",
    "good": "#137a4f", "warning": "#b26b00", "critical": "#c23b39",
    "seq_soft": "#9ec5f4",
    "accent": "#6a2fd0", "accent2": "#d6338f", "accent_soft": "#efe7fb",
    "accent_deep": "#54249f",
}
DARK = {
    "page": "#0d0d0d", "surface": "#1a1a19", "surface2": "#232322",
    "border": "#383835", "hair2": "#2c2c2a",
    "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#383835", "axis": "#52514e",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70",
    "good": "#37d29a", "warning": "#fab219", "critical": "#e66767",
    "seq_soft": "#1c5cab",
    "accent": "#9a7ff0", "accent2": "#e06aa8", "accent_soft": "#2a2140",
    "accent_deep": "#b7a6f5",
}
SERIES_SLOTS = ("s1", "s2", "s3")
MAX_SERIES = 3          # מגבלת הפלטה המאומתת — ראה docstring


def _e(v):
    """escape בטוח לכל ערך שנכנס ל-HTML."""
    return html.escape("" if v is None else str(v), quote=True)


def _money(v, suffix=" ₪"):
    try:
        return f"{float(v):,.0f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _pct(v, nd=1, signed=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:+.{nd}f}%" if signed else f"{f:.{nd}f}%"


def _num(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------
# גרפים — SVG מוטבע
# ------------------------------------------------------------------

def _area_trend_svg(areas, width=760, height=320):
    """
    מגמת חציון ₪/מ"ר לפי שנה, עד 3 אזורים.

    ציר הזמן משמאל לימין (עולה) גם בעמוד RTL — זו המוסכמה בגרפי זמן,
    והשנים על הציר מסירות כל אי-בהירות.
    """
    series = [a for a in (areas or []) if len([
        y for y in (a.get("yearly") or []) if y.get("median_ppm")]) >= 2][:MAX_SERIES]
    if not series:
        return ('<p class="empty">אין מספיק נתוני מגמה רב-שנתיים באף אזור '
                'בריצה הזו.</p>')

    years, vals = set(), []
    for a in series:
        for y in a["yearly"]:
            if y.get("median_ppm"):
                years.add(y["year"])
                vals.append(y["median_ppm"])
    years = sorted(years)
    if len(years) < 2 or not vals:
        return '<p class="empty">אין מספיק שנים להצגת מגמה.</p>'

    pad_l, pad_r, pad_t, pad_b = 62, 132, 22, 40
    x0, x1 = pad_l, width - pad_r
    y0, y1 = pad_t, height - pad_b
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or (hi * 0.1 or 1)
    lo, hi = lo - span * 0.15, hi + span * 0.15

    def px(year):
        return x0 + (x1 - x0) * (years.index(year) / max(len(years) - 1, 1))

    def py(v):
        return y1 - (y1 - y0) * ((v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="מגמת חציון שקל למטר רבוע לפי שנה" class="chart">']

    # גריד אופקי רסיסי + תוויות ציר y
    for i in range(4):
        v = lo + (hi - lo) * i / 3
        y = py(v)
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                     f'class="grid"/>')
        parts.append(f'<text x="{x0 - 10}" y="{y + 4:.1f}" class="tick" '
                     f'text-anchor="end">{_num(v)}</text>')

    for yr in years:
        parts.append(f'<text x="{px(yr):.1f}" y="{y1 + 22}" class="tick" '
                     f'text-anchor="middle">{yr}</text>')

    # תוויות ישירות בקצה — מפוזרות אנכית כדי שקווים קרובים לא יתנגשו
    label_y = {}
    ends = sorted(((i, py(max((y for y in a["yearly"] if y.get("median_ppm")),
                              key=lambda y: y["year"])["median_ppm"]))
                   for i, a in enumerate(series)), key=lambda t: t[1])
    last = None
    for idx, y in ends:
        if last is not None and y - last < 15:
            y = last + 15
        label_y[idx] = y
        last = y

    for i, a in enumerate(series):
        color = f"var(--{SERIES_SLOTS[i]})"
        pts = [(px(y["year"]), py(y["median_ppm"]), y)
               for y in a["yearly"] if y.get("median_ppm")]
        d = " ".join(f'{"M" if j == 0 else "L"}{x:.1f},{y:.1f}'
                     for j, (x, y, _p) in enumerate(pts))
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" '
                     f'stroke-width="2" stroke-linecap="round" '
                     f'stroke-linejoin="round"/>')
        for x, y, p in pts:
            # סמן מלא = שנה מלאה; חלול = כיסוי חלקי (פחות מ-4 רבעונים)
            full = (p.get("deal_quarters") or 0) >= 4
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="'
                + (color if full else "var(--surface)")
                + f'" stroke="{color}" stroke-width="2">'
                f'<title>{_e(a.get("area_name"))} · {p["year"]} · '
                f'{_num(p["median_ppm"])} ₪ למ"ר · '
                f'{p.get("deal_quarters") or 0} רבעונים עם עסקאות</title></circle>')
        # תווית ישירה בקצה — הזהות אינה נשענת על צבע בלבד
        lx, _ly, _lp = pts[-1]
        parts.append(f'<text x="{lx + 10:.1f}" y="{label_y[i] + 4:.1f}" '
                     f'class="direct" text-anchor="start" '
                     f'fill="{color}">{_e(a.get("area_name"))}</text>')

    parts.append(f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" class="axis"/>')
    parts.append("</svg>")

    legend = "".join(
        f'<span class="lg"><i style="background:var(--{SERIES_SLOTS[i]})"></i>'
        f'{_e(a.get("area_name"))} '
        f'<b>{_pct(a.get("cagr_pct"), 1, True)}</b> לשנה</span>'
        for i, a in enumerate(series))
    return (f'<div class="legend">{legend}</div>{"".join(parts)}'
            f'<p class="cap">סמן מלא = שנה עם 4 רבעונים; סמן חלול = כיסוי חלקי. '
            f'ציר הזמן משמאל לימין.</p>')


def _cagr_bars_svg(rows, width=760, bar_h=30):
    """
    לוח עליית ערך — עמודות אופקיות. גודל = עוצמה, ולכן גוון אחד (סדרתי)
    ולא צבע לכל שורה: הצבע כאן אינו זהות אלא כמות.
    """
    rows = [r for r in (rows or []) if r.get("cagr_pct") is not None]
    if not rows:
        return '<p class="empty">אין נתוני עליית ערך בריצה הזו.</p>'
    rows = sorted(rows, key=lambda r: -(r["cagr_pct"] or 0))[:12]

    top = max(abs(r["cagr_pct"]) for r in rows) or 1
    pad_r, pad_l = 250, 70      # RTL: התוויות בצד ימין
    height = len(rows) * bar_h + 26
    x_end = width - pad_r
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="דירוג אזורים לפי עליית ערך שנתית" class="chart">']

    for i, r in enumerate(rows):
        y = i * bar_h + 6
        w = max(2.0, (abs(r["cagr_pct"]) / top) * (x_end - pad_l))
        # RTL: העמודה גדלה ימינה→שמאלה מהקצה הימני של אזור הגרף
        x = x_end - w
        color = "var(--s1)" if i < 3 else "var(--seq-soft)"
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{bar_h - 12}" '
            f'rx="4" fill="{color}">'
            f'<title>{_e(r.get("area_name"))} · {_pct(r.get("cagr_pct"), 1, True)} '
            f'לשנה · {r.get("years_covered") or 0} שנות נתונים</title></rect>')
        parts.append(
            f'<text x="{width - 8}" y="{y + bar_h - 17}" class="barlbl" '
            f'text-anchor="end">{_e(r.get("area_name"))}'
            f'<tspan class="sub"> · {_e(r.get("area_level_he"))}</tspan></text>')
        parts.append(
            f'<text x="{x - 8:.1f}" y="{y + bar_h - 17}" class="barval" '
            f'text-anchor="end">{_pct(r.get("cagr_pct"), 1, True)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _mini_bar(pct, top, kind="critical"):
    """עמודה זעירה בתוך תא טבלה — עוצמת הירידה/הפער במבט אחד."""
    try:
        frac = min(1.0, abs(float(pct)) / (top or 1))
    except (TypeError, ValueError):
        return ""
    return (f'<span class="mb"><span class="mbf" style="width:{frac * 100:.0f}%;'
            f'background:var(--{kind})"></span></span>')


# ------------------------------------------------------------------
# מדורים
# ------------------------------------------------------------------

def _radar_table(rows, cfg):
    if not rows:
        return ('<p class="empty">לא נרשמה אף ירידת מחיר בהיסטוריה שנצברה '
                'עד כה. הטבלה תתמלא ברגע שמודעה במעקב תשנה מחיר.</p>')
    sharp_at = float(cfg.get("sharp_drop_pct", 7.0))
    top = max((r.get("total_drop_pct") or 0) for r in rows) or 1

    body = []
    for r in rows:
        tags = []
        if r.get("sharp"):
            tags.append('<span class="tag crit">▼ ירידה חדה</span>')
        if (r.get("num_drops") or 0) >= 2:
            tags.append(f'<span class="tag warn">{r["num_drops"]} ירידות</span>')
        if r.get("suspect"):
            tags.append('<span class="tag susp">בדיקה ידנית</span>')
        conf = r.get("confidence_he")
        gap = r.get("value_gap_pct")
        gap_txt = (f'{_pct(gap)} מתחת ל{_e(r.get("comp_level_he") or "השוואה")}'
                   if gap is not None and gap > 0 else "—")
        link = (f'<a href="{_e(r.get("url"))}" target="_blank">מודעה</a>'
                if r.get("url") else "—")
        body.append(
            f'<tr>'
            f'<td><b>{_e(r.get("city"))}</b>'
            f'<div class="sub">{_e(r.get("neighborhood") or "")}</div></td>'
            f'<td class="n">{_e(r.get("rooms") or "—")} חד\''
            f'<div class="sub">{_num(r.get("size_sqm"))} מ"ר</div></td>'
            f'<td class="n">{_money(r.get("original_price"))}</td>'
            f'<td class="n"><b>{_money(r.get("current_price"))}</b></td>'
            f'<td class="n big">{_pct(r.get("total_drop_pct"))}'
            f'{_mini_bar(r.get("total_drop_pct"), top)}</td>'
            f'<td class="hist"><bdi>{_e(r.get("history_text"))}</bdi>'
            f'<div class="sub">{_e(r.get("last_change_at") or "")}</div></td>'
            f'<td class="n">{gap_txt}'
            f'<div class="sub">ביטחון {_e(conf or "—")}</div></td>'
            f'<td>{"".join(tags) or "—"}</td>'
            f'<td>{link}</td>'
            f'</tr>')

    return (
        f'<p class="lede">כל מודעה שהמחיר שלה ירד מאז שנכנסה למעקב, לפי גודל '
        f'הירידה המצטברת. ירידה של {sharp_at:.0f}% ומעלה מסומנת '
        f'<span class="tag crit">▼ ירידה חדה</span>. זו עובדה מדודה מתוך '
        f'היסטוריית המחירים של המערכת — לא הערכה.</p>'
        '<div class="tw"><table><thead><tr>'
        '<th>עיר / שכונה</th><th>נכס</th><th>מחיר מקורי</th><th>מחיר נוכחי</th>'
        '<th>ירידה מצטברת</th><th>מסלול המחיר</th><th>מול השוואה</th>'
        '<th>סימונים</th><th>קישור</th>'
        f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def _value_cards(rows, cfg):
    if not rows:
        return ('<p class="empty">אין מודעה שמבוקשת מתחת לחציון ההשוואה שלה '
                'בריצה הזו.</p>')
    top = max((r.get("value_gap_pct") or 0) for r in rows) or 1
    conf_class = {"high": "good", "medium": "warn", "low": "low"}

    cards = []
    for i, r in enumerate(rows, 1):
        cls = conf_class.get(r.get("confidence"), "low")
        tag = ""
        if r.get("unverified_attractive"):
            tag = f'<div class="note low">⚠ {_e(r.get("value_tag"))}</div>'
        elif r.get("suspect"):
            tag = f'<div class="note susp">⚠ {_e(r.get("value_tag"))}</div>'
        elif r.get("value_tag"):
            tag = f'<div class="note">{_e(r.get("value_tag"))}</div>'
        cards.append(
            f'<article class="card">'
            f'<div class="rank">{i}</div>'
            f'<div class="cbody">'
            f'<h3>{_e(r.get("city"))}'
            f'<span class="sub"> · {_e(r.get("neighborhood") or "")}</span></h3>'
            f'<div class="row"><b class="big">{_pct(r.get("value_gap_pct"))}</b> '
            f'מתחת לחציון ב{_e(r.get("comp_level_he"))}'
            f'{_mini_bar(r.get("value_gap_pct"), top, "s1")}</div>'
            f'<div class="row">{_money(r.get("price"))} · '
            f'{_e(r.get("rooms") or "—")} חד\' · {_num(r.get("size_sqm"))} מ"ר · '
            f'{_num(r.get("price_per_sqm"))} ₪ למ"ר</div>'
            f'<div class="row sub">חציון ההשוואה '
            f'{_num(r.get("value_median_ppm"))} ₪ למ"ר · '
            f'{r.get("value_count") or 0} תצפיות · '
            f'{_e(r.get("value_area") or "")}</div>'
            f'<div class="row"><span class="tag {cls}">רמת ביטחון: '
            f'{_e(r.get("confidence_he"))}</span>'
            + (f'<span class="tag">{_e(r.get("tier_he"))}</span>'
               if r.get("tier_he") and r.get("tier_he") != "—" else "")
            + (f'<span class="tag">תשואה {_pct(r.get("yield_pct"))}</span>'
               if r.get("yield_pct") is not None else "")
            + f'</div>{tag}'
            f'<div class="row"><a href="{_e(r.get("url"))}" target="_blank">'
            f'המודעה ביד2</a> · '
            f'<a href="{_e(r.get("nadlan_link"))}" target="_blank">'
            f'עסקאות באזור</a></div>'
            f'</div></article>')
    return (
        '<p class="lede">חמש המודעות הרחוקות ביותר מתחת לחציון ההשוואה שלהן. '
        '<b>אינן מותנות בשער ההתראה המחמיר</b> — מודעה נכנסת לכאן לפי הפער '
        'ורמת הביטחון בלבד. מודעה שנראית זולה אבל אין עליה די עסקאות מקומיות '
        'מוצגת כאן מסומנת "לא אומת", ולא נעלמת מהפלט.</p>'
        f'<div class="cards">{"".join(cards)}</div>')


def _hot_areas_block(rows):
    if not rows:
        return '<p class="empty">אין אזור עם נתוני עליית ערך בריצה הזו.</p>'
    items = []
    for i, r in enumerate(rows, 1):
        yrs = r.get("years_covered") or 0
        span = (f'{r.get("cagr_from_year") or "—"}–{r.get("cagr_to_year") or "—"}')
        # הכותרת: העלייה הכוללת במחיר הדירה — המספר שקורא כמו "הנדל"ן
        # באזור עלה". ₪ למ"ר וה-CAGR יורדים לשורת פירוט משנית.
        rise = r.get("total_rise_pct")
        headline = (f'<b class="big">{_pct(rise, 0, True)}</b> ב-{yrs} שנים'
                    if rise is not None else
                    f'<b class="big">{_pct(r.get("cagr_pct"), 1, True)}</b> לשנה')
        price_line = ""
        if r.get("first_median_price") and r.get("latest_median_price"):
            price_line = (
                f'<div class="row">מחיר דירה חציוני: '
                f'<b>{_money(r.get("first_median_price"))}</b> '
                f'<span dir="ltr">→</span> '
                f'<b>{_money(r.get("latest_median_price"))}</b></div>')
        items.append(
            f'<article class="card">'
            f'<div class="rank hot">{i}</div><div class="cbody">'
            f'<h3>{_e(r.get("area_name"))}'
            f'<span class="sub"> · {_e(r.get("area_level_he"))}'
            + (f' · {_e(r.get("city"))}' if r.get("city") and
               r.get("city") != r.get("area_name") else "")
            + '</span></h3>'
            f'<div class="row">עליית מחירי נדל"ן: {headline} '
            f'<span class="sub">({span})</span></div>'
            f'{price_line}'
            f'<div class="row sub">₪ למ"ר: {_num(r.get("first_median_ppm"))} '
            f'<span dir="ltr">→</span> {_num(r.get("latest_median_ppm"))} · '
            f'{_pct(r.get("cagr_pct"), 1, True)} לשנה · '
            f'{r.get("deal_quarters") or 0} רבעונים עם עסקאות</div>'
            f'</div></article>')
    return (f'<p class="lede">שלושת האזורים שבהם מחירי הנדל"ן עלו הכי הרבה, '
            f'לפי מחיר הדירה החציוני מהעסקאות הרשמיות. המספר הגדול = העלייה '
            f'הכוללת בתקופה; ₪ למ"ר וקצב שנתי (CAGR) מופיעים כפירוט.</p>'
            f'<div class="cards">{"".join(items)}</div>')


# ------------------------------------------------------------------
# העמוד
# ------------------------------------------------------------------

CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:0 0 48px;background:var(--page);color:var(--ink);
 font-family:"Heebo","Segoe UI",Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55}
.viz-root{%LIGHT%}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{%DARK%}}
:root[data-theme="dark"] .viz-root{%DARK%}
.serif{font-family:"Frank Ruhl Libre",Georgia,"Segoe UI",serif}
.brandbar{max-width:1180px;margin:0 auto;padding:16px 20px 0;display:flex;
 align-items:center;justify-content:space-between;gap:12px}
.wm{font-size:20px;font-weight:800;letter-spacing:.5px;color:var(--ink)}
.wm i{color:var(--accent2);font-style:normal}
.live{font-size:11px;color:var(--accent-deep);background:var(--accent-soft);
 border-radius:20px;padding:5px 12px;font-weight:700;letter-spacing:.3px}
.accentline{max-width:1180px;margin:14px auto 0;height:3px;border-radius:3px;
 background:linear-gradient(90deg,var(--accent),var(--accent2))}
header{padding:26px 20px 8px;max-width:1180px;margin:0 auto}
.kicker{font-size:11.5px;letter-spacing:3px;text-transform:uppercase;
 color:var(--accent-deep);font-weight:700}
h1{margin:6px 0 6px;font-size:34px;font-weight:900;color:var(--ink)}
h2{margin:0 0 6px;font-size:20px;font-weight:700;color:var(--ink);
 font-family:"Frank Ruhl Libre",Georgia,"Segoe UI",serif}
.meta{color:var(--ink2);font-size:13px}
main{max-width:1180px;margin:0 auto;padding:0 20px}
section{margin:26px 0;padding:24px 26px;background:var(--surface);
 border:1px solid var(--border);border-radius:18px;
 box-shadow:0 6px 22px rgba(17,24,32,.05)}
.lede{color:var(--ink2);font-size:13.5px;margin:2px 0 16px;max-width:76ch}
.empty{color:var(--muted);font-style:italic;margin:8px 0}
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;
 max-width:1180px;margin:16px auto 0;padding:0 20px}
@media (max-width:820px){.tiles{grid-template-columns:repeat(3,1fr)}}
@media (max-width:520px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:14px;
 padding:18px 16px;position:relative;overflow:hidden;
 box-shadow:0 4px 16px rgba(17,24,32,.04)}
.tile::before{content:"";position:absolute;inset-inline-start:0;top:0;bottom:0;
 width:3px;background:linear-gradient(180deg,var(--accent),var(--accent2));opacity:0}
.tile.hot::before{opacity:1}
.tile b{display:block;font-size:28px;font-weight:700;line-height:1.1;
 font-family:"Frank Ruhl Libre",Georgia,serif}
.tile.hot b{color:var(--critical)}
.tile span{color:var(--muted);font-size:11px;letter-spacing:.5px;
 text-transform:uppercase;display:block;margin-top:8px}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{color:var(--muted);text-align:right;font-weight:500;font-size:11.5px;
 letter-spacing:.4px;text-transform:uppercase;
 padding:8px 11px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:12px 11px;border-bottom:1px solid var(--hair2);vertical-align:top}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--surface2)}
td.n{white-space:nowrap}
td.big,b.big{font-size:16px;font-weight:700}
.sub{color:var(--muted);font-size:11.5px}
.hist{font-size:12px;direction:ltr;text-align:right}
.mb{display:block;height:5px;margin-top:4px;background:var(--border);
 border-radius:3px;overflow:hidden}
.mbf{display:block;height:100%;border-radius:3px}
.tag{display:inline-block;padding:2px 8px;margin:2px 3px 2px 0;border-radius:999px;
 font-size:11.5px;border:1px solid var(--border);color:var(--ink2);
 background:var(--surface);white-space:nowrap}
.tag.crit{color:#fff;background:var(--critical);border-color:var(--critical)}
.tag.warn{color:#3d2c00;background:var(--warning);border-color:var(--warning)}
.tag.good{color:#fff;background:var(--good);border-color:var(--good)}
.tag.susp{color:var(--critical);border-color:var(--critical)}
.tag.low{color:var(--ink2);border-style:dashed}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(330px,1fr))}
.card{display:flex;gap:12px;background:var(--surface);border:1px solid var(--border);
 border-radius:12px;padding:14px 16px;box-shadow:0 3px 12px rgba(17,24,32,.04)}
.rank{font-size:20px;font-weight:700;color:var(--s1);min-width:24px}
.rank.hot{color:var(--s2)}
.cbody{flex:1;min-width:0}
.card h3{margin:0 0 6px;font-size:15.5px}
.row{margin:4px 0}
.note{margin-top:6px;font-size:12.5px;color:var(--ink2);border-right:3px solid var(--border);
 padding-right:8px}
.note.low{border-color:var(--warning)}
.note.susp{border-color:var(--critical)}
a{color:var(--s1)}
.chart{width:100%;height:auto;display:block;margin-top:6px}
/* טקסט בגרפים מיושר כ-LTR **בכוונה**: ב-SVG המשמעות של text-anchor
   מתהפכת לפי כיוון הבסיס, ובעמוד RTL תווית עם anchor=end הייתה נדחפת
   החוצה מהקנבס. המילים העבריות עצמן עדיין מוצגות נכון — ה-bidi הפנימי
   של הדפדפן מטפל בהן בלי קשר לכיוון הבסיס. */
.chart text{direction:ltr}
.grid{stroke:var(--grid);stroke-width:1}
.axis{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:11px}
.direct{font-size:12px;font-weight:600}
.barlbl{fill:var(--ink);font-size:12.5px}
.barval{fill:var(--ink2);font-size:12.5px;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:6px;font-size:12.5px;
 color:var(--ink2)}
.lg i{display:inline-block;width:11px;height:11px;border-radius:3px;
 margin-left:5px;vertical-align:-1px}
.cap{color:var(--muted);font-size:11.5px;margin:6px 0 0}
footer{max-width:1180px;margin:0 auto;padding:18px 20px;color:var(--muted);
 font-size:12px}
footer li{margin:3px 0}

/* ── שכבת אפליקציה: סרגל טאבים דביק, סינון, מצב כהה ── */
.appbar{position:sticky;top:0;z-index:30;background:var(--surface);
 border-bottom:1px solid var(--border);backdrop-filter:saturate(1.2)}
.appbar .in{max-width:1180px;margin:0 auto;padding:10px 20px;display:flex;
 gap:10px;align-items:center;flex-wrap:wrap}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{border:1px solid var(--border);background:var(--surface2);color:var(--ink2);
 border-radius:999px;padding:8px 16px;font-size:13.5px;font-weight:600;cursor:pointer;
 font-family:inherit;white-space:nowrap}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.spacer{flex:1}
.search{position:relative}
.search input{border:1px solid var(--border);background:var(--surface2);color:var(--ink);
 border-radius:10px;padding:8px 32px 8px 12px;font-size:13.5px;font-family:inherit;width:180px}
.search::before{content:"⌕";position:absolute;inset-inline-end:10px;top:7px;color:var(--muted);font-size:15px}
.ghost{border:1px solid var(--border);background:var(--surface2);color:var(--ink2);
 border-radius:10px;width:38px;height:36px;font-size:16px;cursor:pointer;font-family:inherit}
.updated{font-size:12px;color:var(--muted);white-space:nowrap}
.updated b{color:var(--green)}
.hide{display:none !important}
.norows{color:var(--muted);font-style:italic;padding:10px 2px;display:none}
"""


def _vars(palette):
    return ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in palette.items())


def _tiles(report, sec_counts, listings_n):
    t = [("ירידות מחיר", sec_counts.get("drops", 0), True),
         ("ירידות חדות", sec_counts.get("sharp_drops", 0), False),
         ("מודעות במעקב", listings_n, False),
         ("ערך יחסי מוביל", sec_counts.get("best_value", 0), False),
         ("אזורים מתחממים", sec_counts.get("hot_areas", 0), False),
         ("קרדיטים בריצה", report.get("credits_used", 0), False)]
    return "".join(
        f'<div class="tile{" hot" if hot else ""}"><b>{_num(v)}</b>'
        f'<span>{_e(k)}</span></div>' for k, v, hot in t)


def write(sections_data, report, cfg, out_dir, areas=None, listings_n=0,
          web_fonts=True):
    """
    כותב ‎out/dashboard.html‎ ומחזיר את הנתיב (או None בכשל — לא מפיל ריצה).

    ‎web_fonts‎: כשTrue (ברירת מחדל) מוסיף קישור לפונטי המותג (Frank Ruhl +
    Heebo) מ-Google Fonts. זה **שיפור פרוגרסיבי**: הדשבורד המתארח (המוצר,
    נפתח אונליין) מקבל את הפונטים המלאים, וצירוף מייל שנפתח אופליין פשוט
    נופל חזרה לפונט המערכת — בלי לשבור דבר, בלי תמונות/CSS/JS חיצוניים.
    למי שרוצה קובץ 100% ללא אף בקשה חיצונית — להעביר web_fonts=False.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "dashboard.html"

    radar = sections_data.get("price_drop_radar") or []
    best = sections_data.get("best_relative_value") or []
    hot = sections_data.get("hot_areas") or []
    sec_counts = report.get("section_counts") or {}

    # לגרף המגמה: קודם האזורים המתחממים, ואחריהם שאר האזורים עם סדרה
    trend_pool = list(hot)
    seen = {a.get("area_key") for a in trend_pool}
    for a in (areas or {}).values():
        if a.get("area_key") in seen:
            continue
        yearly = [y for y in (a.get("years") or []) if y.get("median_ppm")]
        if len(yearly) >= 2:
            trend_pool.append({"area_name": a.get("area_name"),
                               "area_level_he": "שכונה" if a.get("area_level") ==
                               "neighborhood" else "יישוב",
                               "city": a.get("city"),
                               "cagr_pct": a.get("cagr_pct"),
                               "years_covered": a.get("years_covered"),
                               "yearly": yearly})

    all_cagr = [{"area_name": a.get("area_name"),
                 "area_level_he": "שכונה" if a.get("area_level") == "neighborhood"
                 else "יישוב",
                 "cagr_pct": a.get("cagr_pct"),
                 "years_covered": a.get("years_covered")}
                for a in (areas or {}).values() if a.get("cagr_pct") is not None]

    summary = "".join(f"<li><b>{_e(k)}:</b> {_e(v)}</li>"
                      for k, v in (report.get("summary_lines") or []))
    notes = "".join(f"<li>{_e(n)}</li>" for n in (report.get("notes") or []))

    css = (CSS.replace("%LIGHT%", _vars(LIGHT)).replace("%DARK%", _vars(DARK)))

    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?'
             'family=Frank+Ruhl+Libre:wght@500;700;900&'
             'family=Heebo:wght@300;400;500;600;700&display=swap" '
             'rel="stylesheet">') if web_fonts else ''

    doc = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>נדל"ן סקאוט — {_e(report.get('run_date'))}</title>
{fonts}
<style>{css}</style></head>
<body class="viz-root">
<div class="brandbar">
  <span class="wm">A<i>-</i>WEB</span>
  <span class="live">● סקירה · דרום</span>
</div>
<div class="accentline"></div>
<header>
  <div class="kicker">A-WEB · Real Estate Intelligence</div>
  <h1 class="serif">נדל"ן סקאוט</h1>
  <div class="meta">ריצה {_e(report.get('run_date'))} ·
    {_e(len(cfg.get('cities') or []))} יישובים בהגדרה ·
    נתוני עסקאות רשמיים מ-data.nadlan.gov.il
    {' · ריצה ללא סריקת יד2 (מתוך הנתונים שנצברו)' if report.get('offline') else ''}
  </div>
</header>
<div class="appbar"><div class="in">
  <div class="tabs">
    <button class="tab active" data-cat="all">הכול</button>
    <button class="tab" data-cat="drops">ירידות מחיר</button>
    <button class="tab" data-cat="opps">הזדמנויות</button>
    <button class="tab" data-cat="areas">מגמות ואזורים</button>
  </div>
  <div class="spacer"></div>
  <div class="search"><input id="q" type="search" placeholder="חיפוש עיר / שכונה" aria-label="חיפוש"></div>
  <button class="ghost" id="theme" title="מצב כהה/בהיר">◐</button>
  <span class="updated">עודכן <b>{_e(report.get('run_date'))}</b></span>
</div></div>
<div class="tiles">{_tiles(report, sec_counts, listings_n)}</div>
<main>
  <section data-cat="drops">
    <h2>מכ"ם ירידות מחיר</h2>
    {_radar_table(radar, cfg)}
    <div class="norows">אין תוצאה תואמת לחיפוש.</div>
  </section>
  <section data-cat="opps">
    <h2>הערך היחסי הטוב ביותר</h2>
    {_value_cards(best, cfg)}
    <div class="norows">אין תוצאה תואמת לחיפוש.</div>
  </section>
  <section data-cat="areas">
    <h2>מגמת אזורים — 5 שנים</h2>
    {_area_trend_svg(trend_pool)}
  </section>
  <section data-cat="areas">
    <h2>לוח עליית הערך (CAGR)</h2>
    <p class="lede">עליית ערך שנתית ממוצעת באזור, מחושבת מחציוני ה-₪ למ"ר
      השנתיים של העסקאות הרשמיות. שלושת המובילים מודגשים.</p>
    {_cagr_bars_svg(all_cagr)}
  </section>
  <section data-cat="areas">
    <h2>אזורים מתחממים</h2>
    {_hot_areas_block(hot)}
  </section>
</main>
<script>
(function(){{
  var tabs=document.querySelectorAll('.tab');
  var secs=document.querySelectorAll('main section');
  function applyTab(cat){{
    secs.forEach(function(s){{
      s.classList.toggle('hide', !(cat==='all'||s.getAttribute('data-cat')===cat));
    }});
  }}
  tabs.forEach(function(t){{
    t.addEventListener('click',function(){{
      tabs.forEach(function(x){{x.classList.remove('active')}});
      t.classList.add('active'); applyTab(t.getAttribute('data-cat'));
    }});
  }});
  // חיפוש חי: מסנן שורות טבלה וכרטיסים לפי טקסט
  var q=document.getElementById('q');
  q.addEventListener('input',function(){{
    var v=q.value.trim();
    secs.forEach(function(s){{
      var items=s.querySelectorAll('tbody tr, .card');
      var shown=0;
      items.forEach(function(el){{
        var hit=(v==='')||el.textContent.indexOf(v)>-1;
        el.classList.toggle('hide',!hit); if(hit)shown++;
      }});
      var nr=s.querySelector('.norows');
      if(nr) nr.style.display=(items.length&&shown===0)?'block':'none';
    }});
  }});
  // מצב כהה/בהיר
  var root=document.documentElement, tb=document.getElementById('theme');
  tb.addEventListener('click',function(){{
    var dark=root.getAttribute('data-theme')==='dark';
    root.setAttribute('data-theme',dark?'light':'dark');
  }});
}})();
</script>
<footer>
  <p><b>סיכום הריצה</b></p><ul>{summary}</ul>
  {f'<p><b>הערות</b></p><ul>{notes}</ul>' if notes else ''}
  <p><b>מגבלות הנתונים:</b> המקור הרשמי אינו חושף עסקה בודדת, ולכן "תצפיות"
    הן מחיר עסקה ממוצע רבעוני לפי קטגוריית חדרים. ₪ למ"ר רשמי מחושב כמחיר
    ממוצע חלקי גודל טיפוסי. תשואה היא ברוטו ומוערכת. נתוני העסקאות
    מתעדכנים בפיגור של מספר חודשים.</p>
  <p>נוצר אוטומטית ב-{_e(date.today().isoformat())} · הקובץ עצמאי לחלוטין
    (בלי CSS/JS/תמונות חיצוניים).</p>
</footer>
</body></html>"""

    path.write_text(doc, encoding="utf-8")
    log.info("נשמר דשבורד: %s (%d ירידות, %d ערך יחסי, %d אזורים חמים)",
             path.name, len(radar), len(best), len(hot))
    return path
