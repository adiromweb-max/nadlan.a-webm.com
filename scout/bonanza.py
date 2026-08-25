"""
מחולל "עסקת בוננזה" — פלט מוכן לשיתוף (וואטסאפ/קבוצה).

הרעיון: אחרי שהמערכת כבר רצה כמה ימים וצברה עסקאות מאומתות, שולפים ממנה
**עסקה אחת (או שתיים) שהמערכת הכי בטוחה בה** — לא בהכרח הפער הכי גדול,
אלא עסקה עם:
  • פער אמין (8–30%) — פער ענק כמעט תמיד מניפולציה, ולכן נפסל
  • ביטחון גבוה (comps מספיקים, לא חשוד)
  • אות תומך (ירידת מחיר / ותק / מילות הזדמנות)
  • עברה את שלב הבדיקה מול עסקאות שנסגרו בפועל (stage == final)
  • לא נפסלה ולא סומנה כמחיר חשוד

מייצר:
  1. out/bonanza/bonanza_<id>.txt   — טקסט עברי מוכן לשליחה
  2. out/bonanza/<id>/img_*.jpg      — התמונות של המודעה (להורדה בפייפליין)
  3. out/bonanza/sent.json           — דדופ: לא שולחים אותה עסקה פעמיים

הרצה עצמאית (0 קרדיטים — קורא נתונים קיימים):
    python -m scout.bonanza
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ---- רף הבוננזה (ניתן לעקיפה מ-config) ----
DEFAULTS = {
    "bonanza_gap_min_pct": 8.0,     # מתחת לזה — לא "מציאה"
    "bonanza_gap_max_pct": 30.0,    # מעל זה — חשוד, לא בוננזה
    "bonanza_require_high_conf": True,
    "bonanza_require_signal": True,
    "bonanza_max_per_run": 1,       # כמה עסקאות לפלוט
    "bonanza_stale_days": 7,        # last_seen ישן מזה → דגל "אמת לפני שליחה"
    "bonanza_max_images": 3,
}


def _cfg(cfg, key):
    if cfg and key in cfg:
        return cfg[key]
    return DEFAULTS[key]


def _gap(x):
    return x.get("value_gap_pct") or 0.0


def is_bonanza_grade(x, cfg=None):
    """האם המודעה עומדת ברף הוודאות של בוננזה."""
    if x.get("stage") != "final":
        return False
    if x.get("disqualified") or x.get("suspicious_price"):
        return False
    if x.get("value_suspect") or x.get("comp_suspect"):
        return False
    g = _gap(x)
    if not (_cfg(cfg, "bonanza_gap_min_pct") <= g <= _cfg(cfg, "bonanza_gap_max_pct")):
        return False
    if _cfg(cfg, "bonanza_require_high_conf") and x.get("confidence_he") != "גבוה":
        return False
    if _cfg(cfg, "bonanza_require_signal") and not x.get("has_signal"):
        return False
    if not x.get("comp_sufficient", True):
        return False
    return True


# דירוג המערכת עצמה — מוביל את הבחירה. הבוננזה חייבת להיות העסקה שהמערכת
# כבר הכתירה גבוה יותר, לא שיפוט מקביל שאנחנו ממציאים.
_TIER_RANK = {"check-urgent": 3, "worth-checking": 2, "watch": 1}


def _tier_rank(x):
    return _TIER_RANK.get(x.get("tier"), 0)


def _certainty(x):
    """
    שובר-שוויון בין עסקאות באותו דירוג. משקלל ערך + ודאות:
    תשואה, עליית ערך, ירידת מחיר, כמות קומפים, וציון.
    פער קרוב לקצה הבנד (30%) מקבל קנס קל — פער "יפה מדי" חשוד יותר.
    """
    g = _gap(x)
    edge_penalty = max(0.0, g - 25.0) * 1.5   # פער 25%→30% מאבד עד ~7.5 נק'
    return (
        (x.get("yield_pct") or 0) * 2.0
        + (x.get("area_cagr_pct") or 0) * 0.7
        + (x.get("total_drop_pct") or 0) * 1.5
        + (x.get("comp_count") or 0) * 1.5
        + (x.get("score") or 0) * 0.3
        - edge_penalty
    )


def select(listings, cfg=None, already_sent=None, active_ids=None, limit=None):
    """
    בוחר את עסקאות הבוננזה. `active_ids` (אופציונלי) = קבוצת מזהים שהמערכת
    מחזיקה כ**פעילים** (active=1). אם ניתן — נסנן רק לחיים, כי בוננזה חייבת
    להיות עסקה שחיה במערכת עכשיו, לא שלד ישן.
    סדר: דירוג המערכת → העדפת עסקה פרטית (ללא עמלה) → ציון ודאות.
    `limit` — כמה להחזיר (ברירת מחדל bonanza_max_per_run). None עם limit=0
    מחזיר את כל המדורגות (משמש כשמאמתים חיוּת ובוחרים את הראשונה שחיה).
    """
    already_sent = set(already_sent or [])
    cands = [x for x in listings
             if is_bonanza_grade(x, cfg)
             and x.get("id") not in already_sent
             and (active_ids is None or x.get("id") in active_ids)]
    cands.sort(
        key=lambda x: (_tier_rank(x),
                       1 if x.get("listing_type") == "private" else 0,
                       _certainty(x)),
        reverse=True,
    )
    n = int(_cfg(cfg, "bonanza_max_per_run")) if limit is None else int(limit)
    return cands if n <= 0 else cands[:n]


# ------------------------------------------------------------------
# בדיקת חיוּת — האם המודעה עדיין קיימת ביד2 (לפני שליחה)
# ------------------------------------------------------------------
def _token_of(x):
    tok = x.get("token")
    if tok:
        return tok
    url = x.get("url") or ""
    m = re.search(r"/item/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def verify_alive(client, x):
    """
    מחזיר True (המודעה חיה), False (הוסרה בוודאות), או None (לא ודאי —
    נחסם/נכשל, ואז *לא* מסמנים כמתה כדי לא לייצר אזעקות שווא).
    עולה בקשה אחת (~30 קרדיטים) — לכן מריצים רק על מועמדות בוננזה/מעקב.
    """
    if client is None:
        return None
    from . import yad2
    token = _token_of(x)
    if not token:
        return None
    try:
        html = client.get(yad2.ITEM_URL.format(token=token))
    except Exception as e:
        log.debug("verify_alive fetch failed %s: %s", x.get("id"), e)
        return None
    if html is None or yad2._looks_blocked(html):
        return None                      # לא ודאי
    data = yad2._parse_next_data(html)
    if not data:
        return None
    return yad2._find_item_node(data) is not None


# ------------------------------------------------------------------
# בניית טקסט הוואטסאפ
# ------------------------------------------------------------------
def _fmt_ils(n):
    try:
        return "₪" + f"{int(round(n)):,}"
    except Exception:
        return "—"


def _reasons(x):
    """שורות 'למה העסקה טובה' — נגזרות מהמדדים האמיתיים בלבד."""
    out = []
    g = _gap(x)
    city = x.get("city")
    nbhd = x.get("neighborhood")
    if nbhd:
        nb = nbhd if any(w in nbhd for w in ("שכונה", "שכ'", "רובע")) else f"שכונת {nbhd}"
        area = f"{nb} ב{city}" if city else nb
    else:
        area = city or "האזור"
    if g:
        out.append(f"מחיר נמוך בכ-{g:.0f}% מחציון ה-₪/מ״ר של עסקאות שנסגרו ב{area} (מקור: רשות המיסים).")
    if x.get("yield_pct"):
        out.append(f"תשואת שכירות ברוטו מוערכת ~{x['yield_pct']:.1f}% לשנה.")
    if x.get("area_cagr_pct"):
        out.append(f"האזור בעלייה: ~{x['area_cagr_pct']:.1f}% לשנה בממוצע (CAGR רב-שנתי).")
    if x.get("total_drop_pct"):
        out.append(f"המחיר כבר ירד ב-{x['total_drop_pct']:.0f}% מאז שעלה — המוכר גמיש.")
    if x.get("condition_text"):
        out.append(f"מצב הנכס: {x['condition_text']}.")
    dom = x.get("days_on_market")
    if dom and dom >= 60:
        out.append(f"{int(dom)} יום באוויר — שהות ארוכה שמגדילה סיכוי למיקוח.")
    lt = x.get("listing_type")
    if lt == "private":
        out.append("מודעה פרטית — ללא עמלת תיווך, המחיר הנקוב הוא העלות בפועל.")
    elif lt == "agency":
        out.append("עסקת תיווך — יש להוסיף ~2%+מע״מ עמלה לעלות הכניסה בפועל.")
    if x.get("comp_count"):
        out.append(f"מבוסס על {int(x['comp_count'])} תצפיות השוואה באזור (ביטחון גבוה).")
    return out


def build_text(x, cfg=None, run_date=None):
    run_date = run_date or date.today().isoformat()
    city = x.get("city") or ""
    nbhd = x.get("neighborhood")
    where = f"{city}" + (f", {nbhd}" if nbhd else "")
    rooms = x.get("rooms")
    size = x.get("size_sqm")
    price = x.get("price")
    ppm = x.get("price_per_sqm")

    head = "🏠 *עסקת בוננזה — נדל\"ן סקאוט* 🏠"
    line_where = f"📍 {where}"
    spec = " · ".join(
        p for p in [
            f"{rooms:g} חד׳" if rooms else None,
            f"{int(size)} מ״ר" if size else None,
            _fmt_ils(price) if price else None,
        ] if p
    )
    ppm_line = f"💰 {_fmt_ils(ppm)} למ״ר" if ppm else None

    reasons = _reasons(x)
    reasons_block = "\n".join(f"✅ {r}" for r in reasons)

    url = x.get("url") or ""
    nadlan = x.get("nadlan_link") or ""

    # דגל טריות — אם last_seen ישן, מזכיר לאמת לפני שליחה
    stale_note = ""
    ls = (x.get("last_seen") or "")[:10]
    if ls:
        try:
            days = (date.fromisoformat(run_date) - date.fromisoformat(ls)).days
            if days >= int(_cfg(cfg, "bonanza_stale_days")):
                stale_note = (f"\n⚠️ נראתה לאחרונה לפני {days} ימים — כדאי לוודא שהמודעה עדיין "
                              f"פעילה ביד2 לפני שליחה.")
        except Exception:
            pass

    disclaimer = ("————\n"
                  "השוואה מבוססת ₪/מ״ר מול חציון האזור — אינה מנרמלת קומה/גיל בניין/מצב מדויק. "
                  "בדוק את המודעה בפועל לפני החלטה. מידע, לא ייעוץ.")

    parts = [head, "", f"{line_where}"]
    if spec:
        parts.append(f"🔑 {spec}")
    if ppm_line:
        parts.append(ppm_line)
    parts += ["", "*למה זו עסקה טובה:*", reasons_block]
    if stale_note:
        parts.append(stale_note)
    parts += ["", f"🔗 המודעה: {url}"]
    if nadlan:
        parts.append(f"📊 עסקאות אזור: {nadlan}")
    parts += ["", disclaimer]
    return "\n".join(parts)


# ------------------------------------------------------------------
# כרטיס ויזואלי לוואטסאפ (HTML → PNG בפייפליין דרך chromium)
# ------------------------------------------------------------------
CARD_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;700;900&family=Frank+Ruhl+Libre:wght@700;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;font-family:'Heebo',sans-serif;direction:rtl;background:#fff;overflow:hidden}
.photo{height:560px;background:__PHOTO__;background-size:cover;background-position:center;position:relative}
.photo::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgba(26,16,48,.85),rgba(26,16,48,0) 55%)}
.badge{position:absolute;top:34px;right:34px;z-index:2;background:linear-gradient(120deg,#7b3ff2,#e0489e);color:#fff;font-weight:900;font-size:34px;padding:14px 26px;border-radius:16px;box-shadow:0 8px 24px rgba(123,63,242,.4)}
.brand{position:absolute;top:44px;left:40px;z-index:2;color:#fff;font-family:'Frank Ruhl Libre',serif;font-weight:900;font-size:30px;letter-spacing:1px}
.pov{position:absolute;bottom:28px;right:40px;left:40px;z-index:2;color:#fff}
.pov .loc{font-size:34px;font-weight:700;opacity:.95;margin-bottom:8px}
.pov .price{font-family:'Frank Ruhl Libre',serif;font-size:78px;font-weight:900;line-height:1}
.pov .spec{font-size:30px;opacity:.9;margin-top:8px}
.body{padding:38px 46px 0}
.h{font-family:'Frank Ruhl Libre',serif;font-size:40px;font-weight:900;color:#1a1030;margin-bottom:24px}
.r{display:flex;align-items:flex-start;gap:14px;font-size:31px;line-height:1.4;color:#2a2140;margin-bottom:20px}
.r .ic{color:#12a150;font-weight:900;flex:0 0 auto}
.foot{position:absolute;bottom:0;right:0;left:0;background:#1a1030;color:#cbc3e0;padding:26px 46px;font-size:22px;line-height:1.5}
.foot b{color:#e0489e}
"""


def build_card_html(x, photo_css="linear-gradient(135deg,#5a3a9a,#e0489e)"):
    """
    כרטיס ויזואלי מוכן לשיתוף. `photo_css` = ערך CSS ל-background של אזור
    התמונה: בפייפליין מעבירים url('file://.../img_1.jpg') של התמונה שהורדה;
    בהדגמה — גרדיאנט ממותג כ-placeholder.
    """
    city = x.get("city") or ""
    nbhd = x.get("neighborhood")
    where = city + (f" · {nbhd}" if nbhd else "")
    rooms = x.get("rooms")
    size = x.get("size_sqm")
    spec = " · ".join(p for p in [
        f"{rooms:g} חדרים" if rooms else None,
        f"{int(size)} מ״ר" if size else None,
        ("פרטי" if x.get("listing_type") == "private" else "תיווך") if x.get("listing_type") else None,
    ] if p)
    gap = _gap(x)
    reasons = _reasons(x)[:5]
    rows = "".join(f'<div class="r"><span class="ic">✓</span><span>{r}</span></div>' for r in reasons)
    css = CARD_CSS.replace("__PHOTO__", photo_css)
    return f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="UTF-8">
<style>{css}</style></head><body>
<div class="photo">
  <div class="brand">נדל״ן סקאוט</div>
  <div class="badge">{gap:.0f}%- מתחת לשוק</div>
  <div class="pov">
    <div class="loc">📍 {where}</div>
    <div class="price">{_fmt_ils(x.get('price'))}</div>
    <div class="spec">{spec}</div>
  </div>
</div>
<div class="body">
  <div class="h">למה זו עסקה טובה</div>
  {rows}
</div>
<div class="foot">השוואה לפי ₪/מ״ר מול חציון עסקאות שנסגרו באזור (רשות המיסים) — אינה מנרמלת קומה/גיל/מצב מדויק. <b>מידע, לא ייעוץ.</b></div>
</body></html>"""


# ------------------------------------------------------------------
# תמונות — כתובות מ-raw_json ב-DB. ההורדה עצמה מתבצעת בפייפליין (לא כאן).
# ------------------------------------------------------------------
def image_urls(db_path, listing_id, limit=3):
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT raw_json FROM listings WHERE id=? AND raw_json IS NOT NULL", (listing_id,)
        ).fetchone()
        conn.close()
    except Exception as e:
        log.debug("image_urls failed: %s", e)
        return []
    if not row or not row[0]:
        return []
    urls = re.findall(r'https?://img\.yad2\.co\.il/[^\s"\\]+?\.(?:jpe?g|png|webp)', row[0])
    # דדופ תוך שמירת סדר
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def download_images(urls, dest_dir):
    """מוריד תמונות לתיקייה. נועד לרוץ בפייפליין (שרת/Actions), לא בסשן זה."""
    import urllib.request
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    saved = []
    for i, u in enumerate(urls, 1):
        try:
            ext = os.path.splitext(u.split("?")[0])[1] or ".jpg"
            p = Path(dest_dir) / f"img_{i}{ext}"
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(p, "wb") as f:
                f.write(r.read())
            saved.append(str(p))
        except Exception as e:
            log.debug("image download failed %s: %s", u, e)
    return saved


# ------------------------------------------------------------------
# נקודת כניסה
# ------------------------------------------------------------------
def _mark_inactive(db_path, lid):
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE listings SET active=0 WHERE id=?", (lid,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug("mark inactive failed %s: %s", lid, e)


def run(out_dir="out", cfg=None, download=True, client=None):
    """
    client (אופציונלי) — ScraperApiClient. אם ניתן, כל מועמדת בוננזה עוברת
    **בדיקת חיוּת** לפני שנבחרת: מודעה שהוסרה מיד2 נפסלת (ומסומנת active=0),
    ובוחרים את המדורגת הבאה שחיה. בלי client — בוחרים לפי הדאטה בלבד ומסתמכים
    על דגל הטריות בטקסט.
    """
    out_dir = Path(out_dir)
    latest = out_dir / "latest.json"
    db_path = "data/nadlan.db"
    if not latest.exists():
        log.warning("bonanza: אין out/latest.json — הרץ סריקה קודם")
        return None

    data = json.load(open(latest, encoding="utf-8"))
    listings = data.get("listings") or []
    run_date = data.get("run_date") or date.today().isoformat()

    # רק עסקאות שחיות במערכת עכשיו (active=1)
    active_ids = None
    if Path(db_path).exists():
        try:
            conn = sqlite3.connect(db_path)
            active_ids = {r[0] for r in conn.execute(
                "SELECT id FROM listings WHERE active=1").fetchall()}
            conn.close()
        except Exception as e:
            log.debug("active_ids load failed: %s", e)

    bdir = out_dir / "bonanza"
    bdir.mkdir(parents=True, exist_ok=True)
    sent_path = bdir / "sent.json"
    sent = json.load(open(sent_path, encoding="utf-8")) if sent_path.exists() else {}

    want = int(_cfg(cfg, "bonanza_max_per_run"))
    # כשמאמתים חיוּת — שולפים את כל המדורגות ובוחרים את הראשונות שחיות
    pool = select(listings, cfg, already_sent=sent.keys(), active_ids=active_ids,
                  limit=0 if client else want)
    if not pool:
        log.info("bonanza: אין עסקה שעומדת ברף היום — לא נוצר פלט")
        return None

    picks = []
    for x in pool:
        if len(picks) >= want:
            break
        if client is not None:
            alive = verify_alive(client, x)
            if alive is False:
                log.info("bonanza: %s הוסרה מיד2 — מדלג ומסמן לא-פעילה", x.get("id"))
                _mark_inactive(db_path, x.get("id"))
                continue
            x["_alive_verified"] = bool(alive)
        picks.append(x)

    if not picks:
        log.info("bonanza: כל המועמדות הוסרו מיד2 — אין פלט היום")
        return None

    results = []
    for x in picks:
        lid = x.get("id")
        text = build_text(x, cfg, run_date)
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(lid))
        txt_path = bdir / f"bonanza_{safe}.txt"
        txt_path.write_text(text, encoding="utf-8")

        urls = image_urls(db_path, lid, int(_cfg(cfg, "bonanza_max_images")))
        imgs = []
        if download and urls:
            imgs = download_images(urls, bdir / safe)

        # כרטiv ויזואלי — התמונה הראשונה שהורדה כרקע, אחרת גרדיאנט ממותג
        if imgs:
            photo_css = f"url('file://{Path(imgs[0]).resolve()}')"
        else:
            photo_css = "linear-gradient(135deg,#5a3a9a,#e0489e)"
        card_path = bdir / f"card_{safe}.html"
        card_path.write_text(build_card_html(x, photo_css), encoding="utf-8")
        card_png = render_card_png(card_path, bdir / f"card_{safe}.png")

        sent[lid] = {"date": run_date, "city": x.get("city"), "price": x.get("price"),
                     "gap": _gap(x), "sent_at": datetime.now().isoformat(timespec="seconds")}
        results.append({"id": lid, "text_file": str(txt_path), "card_html": str(card_path),
                        "card_png": card_png, "images": imgs, "image_urls": urls})
        log.info("bonanza: נוצרה עסקה %s (%s, פער %.0f%%)", lid, x.get("city"), _gap(x))

    json.dump(sent, open(sent_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return results


# ------------------------------------------------------------------
# רינדור הכרטיס ל-PNG (מיטבי — דורש chromium/playwright; אחרת מדלג)
# ------------------------------------------------------------------
def render_card_png(html_path, png_path):
    """מרנדר את כרטיס ה-HTML ל-PNG דרך playwright/chromium. מחזיר נתיב או None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        exe = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
        with sync_playwright() as p:
            launch = {"executablePath": exe} if Path(exe).exists() else {}
            b = p.chromium.launch(**launch)
            pg = b.new_page(viewport={"width": 1080, "height": 1350})
            pg.goto("file://" + str(Path(html_path).resolve()), wait_until="networkidle")
            pg.wait_for_timeout(1000)
            pg.screenshot(path=str(png_path))
            b.close()
        return str(png_path)
    except Exception as e:
        log.debug("render_card_png failed: %s", e)
        return None


# ------------------------------------------------------------------
# שליחת הבוננזה במייל (מוכן להעתקה לוואטסאפ + תמונות אמיתיות מצורפות)
# ------------------------------------------------------------------
def email_bonanza(cfg, results):
    if not results:
        return False
    from . import emailer
    from html import escape

    texts = [Path(r["text_file"]).read_text(encoding="utf-8") for r in results]
    body = "\n\n" + ("\n\n" + "—" * 20 + "\n\n").join(texts)
    intro = ("מצורפת עסקת בוננזה טרייה שהמערכת בטוחה בה — הטקסט למטה מוכן "
             "להעתקה לקבוצת הוואטסאפ, והתמונות/כרטיס מצורפים.\n")
    text = intro + body
    html = ("<div style='direction:rtl;font-family:Arial,sans-serif'>"
            f"<p>{escape(intro)}</p><pre style='white-space:pre-wrap;font-family:inherit;"
            f"font-size:15px;line-height:1.6'>{escape(body)}</pre></div>")

    atts = []
    for r in results:
        if r.get("card_png"):
            atts.append(Path(r["card_png"]))
        for img in (r.get("images") or []):
            atts.append(Path(img))
    n = len(results)
    subject = "🏠 עסקת בוננזה חדשה — נדל\"ן סקאוט" + (f" ({n})" if n > 1 else "")
    return emailer.send(cfg, subject, text, html, attachments=atts)


def main():
    """נקודת כניסה לפייפליין: טוען קונפיג+סודות, בונה client לחיוּת/תמונות,
    מריץ, מרנדר כרטיס, ושולח מייל אם נמצאה עסקה."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from . import config
    try:
        from .scraperapi import ScraperApiClient
    except Exception:
        ScraperApiClient = None

    cfg = config.load_config()
    api_key = os.environ.get("SCRAPERAPI_KEY")
    client = None
    if api_key and ScraperApiClient and not cfg.get("bonanza_skip_liveness"):
        try:
            client = ScraperApiClient(
                api_key,
                country_code=cfg.get("scraperapi_country", "il"),
                timeout=int(cfg.get("scraperapi_timeout_seconds", 90)),
                retries=int(cfg.get("scraperapi_retries", 3)),
                max_credits=int(cfg.get("bonanza_max_credits", 400)),
                ultra_premium=bool(cfg.get("scraperapi_ultra_premium", True)),
                credit_cost=int(cfg.get("scraperapi_credit_cost", 30)),
            )
        except Exception as e:
            log.warning("client init failed, ממשיך בלי בדיקת חיוּת: %s", e)

    res = run(cfg=cfg, download=True, client=client)
    if not res:
        print("אין עסקת בוננזה היום.")
        return

    delivered = False
    # 1) וואטסאפ (Green-API) — אם מוגדר
    try:
        from . import whatsapp
        if whatsapp.configured():
            delivered = whatsapp.send_bonanza(res) or delivered
    except Exception as e:
        log.warning("whatsapp delivery failed: %s", e)
    # 2) מייל — אם מוגדר (אפשר גם וגם)
    if cfg.get("gmail_address") and cfg.get("gmail_app_password"):
        delivered = email_bonanza(cfg, res) or delivered
    if not delivered:
        log.info("לא הוגדר אף ערוץ שליחה — הפלט נשמר ב-out/bonanza/ בלבד")

    for r in res:
        print("\n" + "=" * 50)
        print(Path(r["text_file"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
