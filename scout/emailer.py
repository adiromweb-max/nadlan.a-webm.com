"""שליחת מייל דרך Gmail SMTP עם App Password. מייל אחד לריצה."""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
# סדר הניסיונות: 587 עם STARTTLS קודם, אחר כך 465 עם SSL.
# הרבה ספקי שרתים (Hetzner בכללם) חוסמים 465 ו-25 כברירת מחדל, אבל 587 פתוח.
SMTP_ATTEMPTS = ((587, "starttls"), (465, "ssl"))
SMTP_TIMEOUT = 45


def _fmt_money(v, suffix=" ₪"):
    try:
        return f"{float(v):,.0f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _pct(v, nd=1):
    try:
        return f"{float(v):.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


def _sections_text(sections):
    """שלושת המדורים כטקסט — הגיבוי הקריא של המייל, גם בלי HTML וצרופות."""
    s = sections or {}
    radar = s.get("price_drop_radar") or []
    best = s.get("best_relative_value") or []
    hot = s.get("hot_areas") or []
    lines = []

    lines += ["", f"— מכ\"ם ירידות מחיר ({len(radar)}) —"]
    if radar:
        for r in radar[:8]:
            mark = " ▼ירידה חדה" if r.get("sharp") else ""
            mark += " [בדיקה ידנית]" if r.get("suspect") else ""
            lines.append(
                f'• {r.get("city")} — {_fmt_money(r.get("original_price"))} → '
                f'{_fmt_money(r.get("current_price"))} '
                f'({_pct(r.get("total_drop_pct"))} ב-{r.get("num_drops")} שלבים){mark}')
            lines.append(f'   {r.get("history_text") or ""}')
            lines.append(f'   {r.get("url") or ""}')
        if len(radar) > 8:
            lines.append(f"  ...ועוד {len(radar) - 8} באקסל ובדשבורד.")
    else:
        lines.append("אין ירידות מחיר בהיסטוריה שנצברה עד כה.")

    lines += ["", f"— הערך היחסי הטוב ביותר ({len(best)}) —"]
    if best:
        for i, r in enumerate(best, 1):
            tag = f' [{r.get("value_tag")}]' if r.get("value_tag") else ""
            lines.append(
                f'{i}. {r.get("city")} — {_fmt_money(r.get("price"))} — '
                f'{_pct(r.get("value_gap_pct"))} מתחת לחציון '
                f'ב{r.get("comp_level_he")} — ביטחון {r.get("confidence_he")}{tag}')
            lines.append(f'   {r.get("url") or ""}')
    else:
        lines.append("אין מודעה מתחת לחציון ההשוואה בריצה הזו.")

    lines += ["", f"— אזורים מתחממים ({len(hot)}) —"]
    if hot:
        for i, r in enumerate(hot, 1):
            lines.append(
                f'{i}. {r.get("area_name")} ({r.get("area_level_he")}) — '
                f'{_pct(r.get("cagr_pct"))} לשנה, {r.get("years_covered") or 0} '
                f'שנות נתונים, חציון היום '
                f'{_fmt_money(r.get("latest_median_ppm"), " ₪ למ\"ר")}')
    else:
        lines.append("אין נתוני עליית ערך בריצה הזו.")
    return lines


# ── רכיבי מייל מובייל-פירסט (כרטיסים בטור אחד) ──────────────────────
# מיילים לא תומכים ב-flexbox/grid אמין, ו-Gmail במובייל מקצץ טבלאות
# רחבות. לכן כל פריט הוא "כרטיס" בטור אחד עם רוחב 100%, פונט גדול,
# וכפתור קישור בגודל מגע. אותו מבנה נראה טוב גם בדסקטופ.

INK = "#111820"
MUTED = "#8b8f96"
ACCENT = "#0e7c86"
RED = "#c23b39"
GREEN = "#137a4f"
HAIR = "#e8e7e2"


def _section_title(text, count=None):
    label = f"{text} ({count})" if count is not None else text
    return (f'<div style="font-size:18px;font-weight:700;color:{INK};'
            f'margin:26px 0 10px;padding-bottom:6px;'
            f'border-bottom:2px solid {INK}">{label}</div>')


def _btn(url, label):
    return (f'<a href="{url or "#"}" style="background:{ACCENT};color:#ffffff;'
            f'text-decoration:none;padding:9px 16px;border-radius:8px;'
            f'font-size:14px;font-weight:700;display:inline-block">{label}</a>')


def _card(inner, border=HAIR):
    return (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border-collapse:separate;width:100%;'
            f'margin:10px 0;background:#ffffff;border:1px solid {border};'
            f'border-radius:12px"><tr><td style="padding:14px 16px">'
            f'{inner}</td></tr></table>')


def _chip(text, bg, fg):
    return (f'<span style="background:{bg};color:{fg};padding:3px 9px;'
            f'border-radius:20px;font-size:12px;font-weight:700;'
            f'white-space:nowrap">{text}</span>')


def _sections_html(sections):
    """שלושת המדורים ב-HTML מובייל-פירסט — כרטיסים בטור אחד."""
    s = sections or {}
    radar = s.get("price_drop_radar") or []
    best = s.get("best_relative_value") or []
    hot = s.get("hot_areas") or []
    out = []

    # ── מכ"ם ירידות מחיר ──
    out.append(_section_title('מכ"ם ירידות מחיר', len(radar)))
    if radar:
        for r in radar[:10]:
            chips = ""
            if r.get("sharp"):
                chips += _chip("▼ ירידה חדה", "#f7e7e6", RED) + " "
            if r.get("suspect"):
                chips += _chip("בדיקה ידנית", "#f4f1ea", MUTED)
            head = (f'<div style="font-size:16px;font-weight:700;color:{INK}">'
                    f'{r.get("city") or "—"} '
                    f'<span style="color:{MUTED};font-weight:400;font-size:13px">'
                    f'{r.get("neighborhood") or ""}</span></div>')
            price = (f'<div style="font-size:15px;margin:6px 0;color:{INK}">'
                     f'<span dir="ltr">{_fmt_money(r.get("original_price"))} → '
                     f'<b>{_fmt_money(r.get("current_price"))}</b></span> '
                     f'<b style="color:{RED}">'
                     f'<span dir="ltr">-{_pct(r.get("total_drop_pct")).lstrip("-")}</span>'
                     f'</b></div>')
            meta = (f'<div style="color:{MUTED};font-size:12px">'
                    f'{r.get("num_drops") or 0} ירידות · '
                    f'<span dir="ltr">{r.get("history_text") or ""}</span></div>')
            foot = f'<div style="margin-top:10px">{_btn(r.get("url"), "פתח מודעה ↗")} {chips}</div>'
            out.append(_card(head + price + meta + foot))
        if len(radar) > 10:
            out.append(f'<div style="color:{MUTED};font-size:12px">ועוד '
                       f'{len(radar) - 10} בדשבורד ובאקסל המצורפים.</div>')
    else:
        out.append(f'<div style="color:{MUTED}">אין ירידות מחיר בהיסטוריה '
                   f'שנצברה עד כה.</div>')

    # ── הערך היחסי הטוב ביותר ──
    out.append(_section_title("הערך היחסי הטוב ביותר", len(best)))
    if best:
        for i, r in enumerate(best, 1):
            note = (f'<div style="color:#b26b00;font-size:12px;margin-top:4px">'
                    f'{r.get("value_tag")}</div>' if r.get("value_tag") else "")
            head = (f'<div style="font-size:16px;font-weight:700;color:{INK}">'
                    f'{i}. {r.get("city")} '
                    f'<span style="color:{MUTED};font-weight:400;font-size:13px">'
                    f'{r.get("neighborhood") or ""}</span></div>')
            body = (f'<div style="font-size:15px;margin:6px 0;color:{INK}">'
                    f'{_fmt_money(r.get("price"))} · '
                    f'<b style="color:{GREEN}">'
                    f'<span dir="ltr">{_pct(r.get("value_gap_pct"))}</span></b> '
                    f'מתחת לחציון ב{r.get("comp_level_he")}</div>'
                    f'<div style="color:{MUTED};font-size:12px">'
                    f'{r.get("value_count") or 0} תצפיות · '
                    f'ביטחון {r.get("confidence_he")}</div>')
            foot = f'<div style="margin-top:10px">{_btn(r.get("url"), "פתח מודעה ↗")}</div>'
            out.append(_card(head + body + note + foot))
    else:
        out.append(f'<div style="color:{MUTED}">אין מודעה מתחת לחציון '
                   f'ההשוואה בריצה הזו.</div>')

    # ── אזורים מתחממים ──
    out.append(_section_title("אזורים מתחממים", len(hot)))
    if hot:
        for i, r in enumerate(hot, 1):
            inner = (f'<div style="font-size:16px;font-weight:700;color:{INK}">'
                     f'{i}. {r.get("area_name")} '
                     f'<span style="color:{MUTED};font-weight:400;font-size:13px">'
                     f'{r.get("area_level_he")}</span></div>'
                     f'<div style="font-size:15px;margin-top:6px;color:{INK}">'
                     f'<b style="color:{GREEN}"><span dir="ltr">'
                     f'{_pct(r.get("cagr_pct"))}</span></b> לשנה · חציון היום '
                     f'{_fmt_money(r.get("latest_median_ppm"), " ₪ למ\"ר")}</div>'
                     f'<div style="color:{MUTED};font-size:12px;margin-top:3px">'
                     f'{r.get("years_covered") or 0} שנות נתונים</div>')
            out.append(_card(inner))
    else:
        out.append(f'<div style="color:{MUTED}">אין נתוני עליית ערך בריצה '
                   f'הזו.</div>')
    return "".join(out)


def build_body(opportunities, report, threshold, watchlist=None, dedup=None,
               sections=None):
    """
    גוף המייל. מחזיר (text, html).

    ‎opportunities‎ — רק מדרגת "לבדוק דחוף" **ואחרי דדופ**: מה שבאמת חדש.
    ‎watchlist‎     — "שווה בדיקה", מוצג כרשימה מקוצרת ולא ככותרת.
    ‎dedup‎         — סטטיסטיקת הדדופ, כדי להציג "דווחו כבר: N".
    ‎sections‎      — שלושת המדורים שתמיד יש בהם תוכן. הם הלב של המייל
                      כשאין ממצא דחוף, ולכן הם מופיעים **תמיד**, גם כשיש.
    """
    top = opportunities[:5]
    watchlist = watchlist or []
    already = (dedup or {}).get("repeat", 0)

    lines = [f'נדל"ן סקאוט — סיכום ריצה {report.get("run_date")}', ""]
    if top:
        lines.append(f'{len(opportunities)} מודעות בדירוג "לבדוק דחוף" שלא דווחו קודם:')
        lines.append("")
        for i, o in enumerate(top, 1):
            gap = (f'{o["gap_pct"]:.1f}% מתחת לעסקאות באזור'
                   if o.get("gap_pct") is not None else "אין פער מחושב")
            lines.append(f'{i}. {o.get("city")} — {_fmt_money(o.get("price"))} — '
                         f'{gap} — תשואה {_pct(o.get("yield_pct"))} — '
                         f'ציון {o.get("score")} [{o.get("opportunity_type_he")}]')
            lines.append(f'   {o.get("alert_reason") or ""}')
            lines.append(f'   {o.get("url")}')
    else:
        lines.append('רצתי כמו שצריך — אין מודעות חדשות בדירוג "לבדוק דחוף".')
    if already:
        lines.append("")
        lines.append(f"דווחו כבר בריצות קודמות ולכן לא נשלחו שוב: {already}")
    if watchlist:
        lines += ["", f'שווה בדיקה ({len(watchlist)}):']
        for o in watchlist[:5]:
            lines.append(f'  • {o.get("city")} — {_fmt_money(o.get("price"))} — '
                         f'פער {_pct(o.get("gap_pct"))} — ציון {o.get("score")}')
    lines += _sections_text(sections)
    lines += ["", "— סיכום ריצה —"]
    for k, v in report.get("summary_lines", []):
        lines.append(f"• {k}: {v}")
    if report.get("notes"):
        lines += ["", "הערות:"]
        lines += [f"• {n}" for n in report["notes"]]
    lines += ["", "מצורפים: האקסל המלא, dashboard.html (עמוד עצמאי) "
                  "וגרף מגמת האזור המוביל."]
    text = "\n".join(lines)

    # ── כותרת "לבדוק דחוף" — כרטיסים בטור אחד ──
    if top:
        cards = [f'<p style="font-size:15px;color:{INK}">נמצאו '
                 f'<b>{len(opportunities)}</b> מודעות בדירוג '
                 f'<b>"לבדוק דחוף"</b> שלא דווחו קודם:</p>']
        for i, o in enumerate(top, 1):
            comp = o.get("comp") or {}
            gap = (f'<b style="color:{GREEN}"><span dir="ltr">'
                   f'{o["gap_pct"]:.1f}%</span></b> מתחת לעסקאות באזור'
                   if o.get("gap_pct") is not None else "אין פער מחושב")
            head = (f'<div style="font-size:17px;font-weight:700;color:{INK}">'
                    f'{i}. {o.get("city") or "—"} '
                    f'<span style="color:{MUTED};font-weight:400;font-size:13px">'
                    f'{o.get("neighborhood") or ""}</span>'
                    f'<span style="float:left">'
                    f'{_chip("ציון " + str(o.get("score")), "#efe7fb", "#54249f")}'
                    f'</span></div>')
            body = (f'<div style="font-size:16px;margin:8px 0;color:{INK}">'
                    f'{_fmt_money(o.get("price"))}</div>'
                    f'<div style="font-size:14px;color:{INK}">{gap}</div>'
                    f'<div style="color:{MUTED};font-size:12px;margin-top:4px">'
                    f'תשואה {_pct(o.get("yield_pct"))} · '
                    f'עליית ערך {_pct(o.get("area_cagr_pct"))} · '
                    f'{o.get("opportunity_type_he") or "—"} · '
                    f'{comp.get("comp_count") or 0} קומפים '
                    f'({comp.get("comp_match_level") or "—"})</div>')
            reason = (f'<div style="font-size:13px;color:{INK};margin-top:8px">'
                      f'{o.get("alert_reason")}</div>' if o.get("alert_reason") else "")
            foot = (f'<div style="margin-top:12px">{_btn(o.get("url"), "פתח מודעה ↗")} '
                    f'<a href="{o.get("nadlan_link") or "#"}" '
                    f'style="color:{ACCENT};font-size:13px;font-weight:700;'
                    f'text-decoration:none;margin-right:6px">עסקאות באזור ↗</a></div>')
            cards.append(_card(head + body + reason + foot, border="#e0d3b8"))
        table = "".join(cards)
    else:
        table = (f'<table role="presentation" width="100%" cellpadding="0" '
                 f'cellspacing="0"><tr><td style="background:#fbf7ef;'
                 f'border:1px solid #eadfc7;border-radius:10px;padding:14px 16px;'
                 f'font-size:15px;color:{INK}">רצתי כמו שצריך — '
                 f'<b>אין מודעות חדשות בדירוג "לבדוק דחוף"</b> בריצה הזו. '
                 f'התוכן החם למטה: ירידות מחיר ואזורים מתחממים.</td></tr></table>')

    if already:
        table += (f'<div style="color:{MUTED};font-size:13px;margin-top:8px">'
                  f'דווחו כבר בריצות קודמות ולכן <b>לא נשלחו שוב: {already}</b> '
                  f'(מופיעות באקסל).</div>')
    if watchlist:
        table += _section_title("שווה בדיקה", len(watchlist))
        for o in watchlist[:5]:
            inner = (f'<div style="font-size:15px;font-weight:700;color:{INK}">'
                     f'{o.get("city")} '
                     f'<span style="color:{MUTED};font-weight:400;font-size:13px">'
                     f'{_fmt_money(o.get("price"))}</span></div>'
                     f'<div style="color:{MUTED};font-size:12px;margin:4px 0 10px">'
                     f'פער <span dir="ltr">{_pct(o.get("gap_pct"))}</span> · '
                     f'ציון {o.get("score")}</div>'
                     f'{_btn(o.get("url"), "פתח מודעה ↗")}')
            table += _card(inner)

    summary_html = "".join(
        f'<li style="margin:3px 0"><b>{k}:</b> {v}</li>'
        for k, v in report.get("summary_lines", []))
    notes_html = ""
    if report.get("notes"):
        notes_html = (f"<div style='font-weight:700;color:{INK};margin-top:14px'>"
                      "הערות:</div><ul style='font-size:13px;color:#444'>"
                      + "".join(f"<li>{n}</li>" for n in report["notes"]) + "</ul>")

    html = f"""<!DOCTYPE html><html lang="he" dir="rtl"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; background:#f2f1ec; }}
  a {{ color:{ACCENT}; }}
  @media only screen and (max-width:600px) {{
    .wrap {{ width:100% !important; padding:14px !important; }}
    .brand {{ font-size:24px !important; }}
  }}
</style></head>
<body style="margin:0;padding:0;background:#f2f1ec">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f1ec">
<tr><td align="center">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0"
       style="width:600px;max-width:600px;background:#ffffff;margin:0 auto;
              padding:22px;font-family:'Segoe UI',Arial,Helvetica,sans-serif;
              color:{INK};text-align:right">
<tr><td>
  <div style="height:4px;background:linear-gradient(90deg,#6a2fd0,#d6338f);
              border-radius:3px;margin-bottom:14px"></div>
  <div class="brand" style="font-size:28px;font-weight:800;color:{INK}">נדל"ן סקאוט</div>
  <div style="color:{MUTED};font-size:13px;margin-bottom:6px">
    A-WEB · Real Estate Intelligence · ריצה {report.get("run_date")}</div>
  {table}
  {_sections_html(sections)}
  {_section_title("סיכום ריצה")}
  <ul style="font-size:14px;color:{INK};padding-right:20px;margin:0">{summary_html}</ul>
  {notes_html}
  <div style="font-size:12px;color:{MUTED};margin-top:18px;
              border-top:1px solid {HAIR};padding-top:12px">
    מצורפים: האקסל המלא, dashboard.html (עמוד עצמאי לדפדפן), וגרף מגמת
    האזור המוביל.</div>
</td></tr></table>
</td></tr></table>
</body></html>"""
    return text, html


MIME_BY_SUFFIX = {
    ".xlsx": ("application",
              "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".html": ("text", "html"),
    ".htm": ("text", "html"),
    ".png": ("image", "png"),
    ".json": ("application", "json"),
    ".csv": ("text", "csv"),
}


def _attach(msg, path):
    """מצרף קובץ אחד לפי הסיומת. כשל בקובץ אחד לא מבטל את המייל."""
    try:
        data = path.read_bytes()
    except (OSError, AttributeError) as e:
        log.warning("לא ניתן לצרף %s: %s — נשלח בלי הצרופה הזו", path, e)
        return False
    maintype, subtype = MIME_BY_SUFFIX.get(
        path.suffix.lower(), ("application", "octet-stream"))
    msg.add_attachment(data, maintype=maintype, subtype=subtype,
                       filename=path.name)
    return True


def send(cfg, subject, text, html, attachment=None, attachments=None):
    """שולח מייל. מחזיר True/False — כשל לא מפיל את הריצה."""
    sender = cfg.get("gmail_address")
    password = cfg.get("gmail_app_password")
    to_addr = cfg.get("alert_to")

    if not sender or not password:
        log.error("חסרים GMAIL_ADDRESS / GMAIL_APP_PASSWORD — לא נשלח מייל")
        return False
    if not to_addr:
        log.error("חסרה כתובת יעד (ALERT_TO / alert_to) — לא נשלח מייל")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(('נדל"ן סקאוט', sender))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    files = [f for f in ([attachment] if attachment else []) + list(attachments or [])
             if f]
    attached = [f.name for f in files if _attach(msg, f)]
    if attached:
        log.info("צרופות: %s", ", ".join(attached))

    ctx = ssl.create_default_context()
    last_err = None
    for port, mode in SMTP_ATTEMPTS:
        try:
            if mode == "ssl":
                with smtplib.SMTP_SSL(SMTP_HOST, port, context=ctx,
                                      timeout=SMTP_TIMEOUT) as smtp:
                    smtp.login(sender, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(SMTP_HOST, port, timeout=SMTP_TIMEOUT) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ctx)
                    smtp.ehlo()
                    smtp.login(sender, password)
                    smtp.send_message(msg)
            log.info("מייל נשלח ל-%s דרך פורט %d (נושא: %s)", to_addr, port, subject)
            return True
        except smtplib.SMTPAuthenticationError as e:
            # סיסמה שגויה — אין טעם לנסות פורט אחר
            log.error("אימות Gmail נכשל (בדוק את GMAIL_APP_PASSWORD): %s", e)
            return False
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            last_err = e
            log.warning("שליחה בפורט %d (%s) נכשלה: %s — מנסה חלופה", port, mode, e)

    log.error("שליחת המייל נכשלה בכל הפורטים: %s", last_err)
    return False
