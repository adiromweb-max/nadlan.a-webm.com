"""
webapp.py — מחולל ה-front-end הפרימיום של A-WEB.

קורא את out/latest.json (הפלט של jsonout) ומייצר out/app.html — עמוד
עצמאי, ממותג, שמרנדר את הנתונים האמיתיים. ברירת המחדל: "חדש היום" —
רק מודעות שהתגלו/השתנו בריצה הזו, כדי שהמשתמש לא יראה את אותה רשימה כל
יום. יש טאב "הכול" למי שרוצה את התמונה המלאה.

פיצ'רים: מעקב אישי (★, נשמר ב-localStorage), ייצוא לאקסל (CSV), ופירוט
מלא לכל עסקה (היסטוריית מחיר, קומפים, דגלים אדומים, מחשבון תשואה).

הנתונים מוטמעים ישירות בתוך ה-HTML (window.DATA) — בלי fetch, בלי שרת,
בלי CORS. זה בדיוק מודל "השרת מזריק את הנתונים לדשבורד".
"""
import json
import logging

log = logging.getLogger("scout.webapp")


def _trim(listings):
    """מצמצם כל מודעה לשדות שה-front-end צריך (קובץ קטן)."""
    out = []
    for s in listings:
        gap = s.get("value_gap_pct")
        if gap is None:
            gap = s.get("gap_pct")
        tier = s.get("tier_he")
        if tier in (None, "", "—", "-"):
            tier = None
        suspect = bool(s.get("value_suspect") or s.get("comp_suspect"))
        # שמירת שפיות: פער > 45% מהשוק כמעט תמיד = נתון פגום (גודל/חדרים) → לאימות
        if gap is not None and gap > 45:
            suspect = True
        out.append({
            "id": s.get("id"),
            "city": s.get("city"),
            "hood": s.get("neighborhood"),
            "street": s.get("street"),
            "akey": s.get("area_key"),
            "price": s.get("price"),
            "rooms": s.get("rooms"),
            "size": s.get("size_sqm"),
            "ppm": s.get("price_per_sqm"),
            "mppm": s.get("value_median_ppm") or s.get("comp_median_ppm"),
            "gap": gap,
            "drop": s.get("total_drop_pct") or s.get("drop_pct") or 0,
            "yield": s.get("yield_pct"),
            "rent": s.get("monthly_rent_est"),
            "cagr": s.get("area_cagr_pct"),
            "dom": s.get("days_on_market"),
            "cond": s.get("condition_text"),
            "tier": tier,
            "otype": s.get("opportunity_type_he"),
            "reason": s.get("reason"),
            "conf": s.get("confidence_he"),
            "comps": s.get("comp_count"),
            "complevel": s.get("comp_level_he"),
            "dtype": s.get("listing_type"),
            "disq": bool(s.get("disqualified")),
            "isnew": bool(s.get("is_new")),
            "dropped": bool(s.get("dropped_today")),
            "stage": s.get("stage"),
            "suspect": suspect,
            "url": s.get("url"),
            "nadlan": s.get("nadlan_link"),
            "first_seen": s.get("first_seen"),
            "hist": [{"p": p.get("price"), "d": p.get("seen_at")}
                     for p in (s.get("price_history") or []) if p.get("price")],
        })
    return out


def build_payload(data):
    """בונה את אובייקט הנתונים הרזה שמוטמע בעמוד."""
    listings = _trim(data.get("listings") or [])
    return {
        "run_date": data.get("run_date"),
        "data_version": _official_version(data),
        "summary_counts": {
            "new": sum(1 for x in listings if x["isnew"] and not x["disq"]),
            "drops": (data.get("section_counts") or {}).get("drops", 0),
            "scanned": len(listings),
            "hot_areas": (data.get("section_counts") or {}).get("hot_areas", 0),
            "private": sum(1 for x in listings if x.get("dtype") == "private"),
            "agency": sum(1 for x in listings if x.get("dtype") == "agency"),
        },
        "listings": listings,
        "areas": [{
            "key": a.get("area_key"),
            "name": a.get("area_name"), "city": a.get("city"),
            "level": a.get("area_level"),
            "cagr": a.get("cagr_pct"),
            "from": a.get("cagr_from_year"), "to": a.get("cagr_to_year"),
            "yearly": [{"y": p.get("year"), "ppm": p.get("median_ppm"),
                        "price": p.get("median_price")}
                       for p in (a.get("yearly") or []) if p.get("median_ppm")],
        } for a in (data.get("areas") or [])],
        "cities": [{
            "city": c.get("city"), "ppm": c.get("median_ppsqm"),
            "change": c.get("price_change_pct"),
            "active": c.get("active_listings"),
        } for c in (data.get("cities") or [])],
        "caveats": data.get("data_caveats") or [],
    }


def _official_version(data):
    for a in (data.get("areas") or []):
        if a.get("data_version"):
            return a["data_version"]
    return None


def write(out_dir):
    """קורא latest.json ומייצר app.html. מחזיר את הנתיב או None."""
    latest = out_dir / "latest.json"
    if not latest.exists():
        log.warning("webapp: latest.json לא נמצא — מדלג")
        return None
    data = json.loads(latest.read_text(encoding="utf-8"))
    payload = build_payload(data)
    html = HTML_TEMPLATE.replace(
        "/*__DATA__*/", "window.DATA=" + json.dumps(payload, ensure_ascii=False))
    dest = out_dir / "app.html"
    dest.write_text(html, encoding="utf-8")
    log.info("נשמר app.html (%d מודעות, %d חדשות)",
             len(payload["listings"]), payload["summary_counts"]["new"])
    return dest


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>נדל״ן סקאוט · A-WEB</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@500;700;900&family=Heebo:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--accent:#7b3ff2;--accent2:#e0489e;--grad:linear-gradient(120deg,#7b3ff2,#e0489e);
    --ink:#14121c;--ink2:#4a4756;--muted:#8b8896;--page:#f5f3f7;--panel:#fff;
    --hair:#ece8f2;--hair2:#f3f0f8;--green:#0f9d63;--greenbg:#e4f6ee;--red:#e0455e;
    --redbg:#fdeaee;--gold:#e0a63a;--dark:#161022;}
  [data-theme="dark"]{--ink:#f3f0fa;--ink2:#c9c4d8;--muted:#928da3;--page:#100c1a;--panel:#1a1428;
    --hair:#2a2140;--hair2:#221a34;--greenbg:#0f2b20;--redbg:#331722;}
  *{box-sizing:border-box}html,body{margin:0}
  body{background:var(--page);color:var(--ink);font-family:"Heebo","Segoe UI",Arial,sans-serif;font-size:15px;line-height:1.55}
  .serif{font-family:"Frank Ruhl Libre",Georgia,serif}
  .nl{direction:ltr;unicode-bidi:isolate;display:inline-block}
  button,a{font-family:inherit}
  .grad-text{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}

  .appbar{position:sticky;top:0;z-index:40;background:var(--dark);color:#fff}
  .appbar .in{max-width:1120px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px}
  .wm{font-size:19px;font-weight:800;cursor:pointer}.wm i{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
  .tabs{display:flex;gap:4px;flex:1;flex-wrap:wrap;justify-content:center}
  .tabs a{color:#cfc9dd;text-decoration:none;font-size:14px;font-weight:600;padding:8px 13px;border-radius:999px;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;gap:6px;transition:background .15s,color .15s}
  .tabs a svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
  .tabs a:hover{color:#fff;background:rgba(255,255,255,.07)}
  .tabs a.on svg{stroke:#e0489e}
  .tabs a.on{background:rgba(255,255,255,.13);color:#fff}
  .tabs a .cnt{opacity:.7;font-weight:700}
  .abtools{display:flex;align-items:center;gap:8px}
  .search{display:flex;align-items:center;gap:7px;background:rgba(255,255,255,.1);border-radius:999px;padding:7px 13px}
  .search input{background:none;border:none;color:#fff;font-size:13.5px;width:120px;outline:none}
  .search input::placeholder{color:#a8a2ba}
  .search svg{width:15px;height:15px;stroke:#a8a2ba;fill:none;stroke-width:2}
  .iconbtn{width:36px;height:36px;border-radius:999px;background:rgba(255,255,255,.1);border:none;color:#fff;font-size:15px;cursor:pointer}

  .hero{position:relative;background:var(--dark);color:#fff;overflow:hidden}
  .hero::before{content:"";position:absolute;inset:0;
    background:radial-gradient(680px 300px at 84% -30%,rgba(224,72,158,.34),transparent 60%),
               radial-gradient(680px 300px at 6% -10%,rgba(123,63,242,.4),transparent 60%)}
  .hero .in{position:relative;max-width:1120px;margin:0 auto;padding:24px 20px 32px;display:flex;gap:28px;align-items:center;justify-content:space-between;z-index:2}
  .htext{flex:1;min-width:0}
  .hero h1{font-size:30px;font-weight:900;margin:2px 0 8px;line-height:1.12}
  .hero p{color:#cfc9dd;font-size:14.5px;max-width:520px;margin:0}
  /* side stat panel */
  .hstat{width:308px;flex-shrink:0;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:16px 18px;backdrop-filter:blur(6px)}
  .hstat .top{display:flex;align-items:center;gap:16px;margin-bottom:14px}
  .donut{flex-shrink:0}
  .dlabel{font-size:12.5px;color:#cfc9dd;line-height:1.7}
  .dlabel .dot{display:inline-block;width:9px;height:9px;border-radius:3px;margin-inline-start:6px;vertical-align:middle}
  .dlabel b{color:#fff}
  .kpis{display:grid;grid-template-columns:1fr 1fr;gap:12px 10px;border-top:1px solid rgba(255,255,255,.1);padding-top:13px}
  .kpi b{font-size:22px;font-weight:800;font-family:"Frank Ruhl Libre",serif;display:block;line-height:1}
  .kpi span{color:#a8a2ba;font-size:11.5px}
  .kpi .g{color:#6ee7b0}.kpi .r{color:#ff8fa3}.kpi .v{color:#c9b0ff}
  .skyline{position:absolute;left:0;right:0;bottom:0;width:100%;height:74px;opacity:.5}

  .wrap{max-width:1120px;margin:0 auto;padding:22px 20px 80px}
  .sec-h{display:flex;align-items:baseline;justify-content:space-between;margin:6px 0 3px}
  .sec-h h2{font-size:22px;font-weight:900;margin:0}
  .sec-sub{color:var(--muted);font-size:13px;margin:0 0 16px}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0 18px}
  .chipf{border:1px solid var(--hair);background:var(--panel);color:var(--ink2);border-radius:999px;padding:7px 13px;font-size:13px;font-weight:600;cursor:pointer}
  .chipf.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .toolbar select{border:1px solid var(--hair);background:var(--panel);color:var(--ink);border-radius:10px;padding:8px 11px;font-size:13px;font-weight:600}
  .exp{margin-inline-start:auto;border:1px solid var(--hair);background:var(--panel);color:var(--ink);border-radius:10px;padding:8px 14px;font-weight:700;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px}
  .exp svg{width:15px;height:15px;stroke:var(--green);fill:none;stroke-width:2}

  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
  @media(max-width:880px){.cards{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:600px){.cards{grid-template-columns:1fr}}
  .card{background:var(--panel);border-radius:18px;overflow:hidden;box-shadow:0 10px 30px rgba(20,18,28,.08);border:1px solid var(--hair);transition:transform .18s,box-shadow .18s}
  .card:hover{transform:translateY(-4px);box-shadow:0 20px 44px rgba(123,63,242,.16)}
  .thumb{position:relative;height:120px;display:flex;align-items:flex-end;justify-content:center;overflow:hidden;cursor:pointer}
  .thumb svg.bld{position:absolute;bottom:-6px;width:150px;height:110px;opacity:.5}
  .ribbon{position:absolute;top:12px;inset-inline-start:12px;background:var(--red);color:#fff;font-size:12px;font-weight:800;padding:5px 11px;border-radius:999px;box-shadow:0 4px 12px rgba(224,69,94,.4)}
  .ribbon.gold{background:var(--gold)}
  .newbadge{position:absolute;top:12px;inset-inline-start:12px;background:var(--grad);color:#fff;font-size:11px;font-weight:800;padding:5px 11px;border-radius:999px}
  .tierbadge{position:absolute;top:12px;inset-inline-end:12px;background:rgba(20,18,28,.72);color:#fff;font-size:11px;font-weight:800;padding:5px 11px;border-radius:999px;backdrop-filter:blur(4px)}
  .savebtn{position:absolute;bottom:10px;inset-inline-end:10px;width:34px;height:34px;border-radius:999px;border:none;background:rgba(255,255,255,.9);color:var(--accent);font-size:16px;cursor:pointer;box-shadow:0 3px 10px rgba(0,0,0,.15)}
  .savebtn.on{background:var(--accent);color:#fff}
  .cbody{padding:14px 16px 16px}
  .ctop{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .cprice{font-size:21px;font-weight:900;font-family:"Frank Ruhl Libre",serif;white-space:nowrap}
  .cplace{font-weight:800;font-size:15px;cursor:pointer}.cplace span{color:var(--muted);font-weight:400;font-size:13px}
  .facts{display:flex;gap:13px;margin:10px 0;color:var(--ink2);font-size:13px;flex-wrap:wrap}
  .facts .f{display:flex;align-items:center;gap:5px}
  .facts svg{width:16px;height:16px;stroke:var(--accent);fill:none;stroke-width:1.7}
  .kv{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:10px}
  .kv .b{background:var(--greenbg);color:var(--green);font-weight:800;font-size:12px;padding:4px 9px;border-radius:8px}
  .kv .n{background:var(--hair2);color:var(--accent);font-weight:700;font-size:12px;padding:4px 9px;border-radius:8px}
  .kv .s{background:var(--redbg);color:var(--red);font-weight:700;font-size:12px;padding:4px 9px;border-radius:8px}
  .own{font-weight:800;font-size:12px;padding:4px 9px;border-radius:8px;background:var(--greenbg);color:var(--green)}
  .own.ag{background:var(--goldbg,#fbf1dc);color:var(--gold)}
  .why{color:var(--ink2);font-size:12.5px;line-height:1.55;border-top:1px solid var(--hair);padding-top:10px;min-height:32px}
  .why b{color:var(--ink)}
  .cta{margin-top:12px;display:flex;gap:8px}
  .cta a,.cta button{flex:1;text-align:center;text-decoration:none;border-radius:10px;padding:9px;font-weight:800;font-size:13px;cursor:pointer;border:1px solid var(--hair)}
  .cta .go{background:var(--grad);color:#fff;border:none}
  .cta .det{background:var(--panel);color:var(--ink2)}
  .empty{text-align:center;padding:50px 20px;color:var(--muted)}.empty .ic{font-size:36px;margin-bottom:8px}

  .trend{background:var(--panel);border:1px solid var(--hair);border-radius:16px;padding:6px 18px;box-shadow:0 8px 24px rgba(20,18,28,.06)}
  .trend table{width:100%;border-collapse:collapse}
  .trend th{text-align:right;color:var(--muted);font-size:12px;font-weight:700;padding:10px 6px;border-bottom:1px solid var(--hair)}
  .trend td{padding:11px 6px;border-bottom:1px solid var(--hair2);font-size:14px}
  .trend tr:last-child td{border-bottom:none}
  .up{color:var(--green);font-weight:800}
  .bar{height:7px;border-radius:999px;background:var(--hair);position:relative;overflow:hidden;min-width:80px}
  .bar i{position:absolute;inset-inline-start:0;top:0;bottom:0;background:var(--grad);border-radius:999px}
  .foot{color:var(--muted);font-size:12px;text-align:center;margin-top:26px;line-height:1.7}

  /* ===== deal detail modal ===== */
  .overlay{position:fixed;inset:0;background:rgba(16,10,28,.62);backdrop-filter:blur(4px);z-index:60;display:none;align-items:flex-start;justify-content:center;padding:20px;overflow:auto}
  .overlay.show{display:flex}
  .modal{background:var(--panel);border-radius:22px;max-width:820px;width:100%;box-shadow:0 30px 80px rgba(0,0,0,.4);margin:20px auto 40px}
  .mhead{position:relative;background:var(--dark);color:#fff;padding:24px 26px;border-radius:22px 22px 0 0;overflow:hidden}
  .mhead::before{content:"";position:absolute;inset:0;background:radial-gradient(500px 200px at 85% -30%,rgba(224,72,158,.4),transparent 60%),radial-gradient(500px 200px at 6% 0,rgba(123,63,242,.44),transparent 60%)}
  .mhead .z{position:relative;z-index:2}
  .mhead h2{margin:0;font-size:24px;font-weight:900}
  .mhead .loc{color:#cfc9dd;font-size:13.5px;margin-top:3px}
  .mhead .pr{font-size:26px;font-weight:900;font-family:"Frank Ruhl Libre",serif;margin-top:8px}
  .xbtn{position:absolute;top:16px;inset-inline-end:18px;z-index:3;background:rgba(255,255,255,.16);border:none;color:#fff;width:34px;height:34px;border-radius:999px;font-size:16px;cursor:pointer}
  .mbody{padding:22px 26px 26px}
  .keynums{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
  .keynum{flex:1;min-width:96px;background:var(--hair2);border-radius:13px;padding:13px;text-align:center}
  .keynum b{display:block;font-size:22px;font-weight:900;font-family:"Frank Ruhl Libre",serif}
  .keynum span{font-size:11.5px;color:var(--muted)}
  .dsec{margin-bottom:20px}
  .dsec h3{font-size:15px;font-weight:900;margin:0 0 3px}
  .dsec .ph{color:var(--muted);font-size:12.5px;margin:0 0 12px}
  .flags{list-style:none;padding:0;margin:0}
  .flags li{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--hair2);font-size:13.5px;align-items:flex-start}
  .flags li:last-child{border-bottom:none}
  .flags .ok{color:var(--green);font-weight:900}.flags .warn{color:var(--gold);font-weight:900}
  .roi label{display:block;font-size:13px;font-weight:700;color:var(--ink2);margin:12px 0 6px}
  .roi input[type=range]{width:100%;accent-color:var(--accent)}
  .roi .out{display:flex;justify-content:space-between;margin-top:14px;padding-top:12px;border-top:1px solid var(--hair);gap:8px}
  .roi .out div{text-align:center;flex:1}
  .roi .out div b{display:block;font-size:20px;font-weight:900;font-family:"Frank Ruhl Libre",serif;color:var(--accent)}
  .roi .out div span{font-size:11px;color:var(--muted)}
  .mrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  .mrow a,.mrow button{flex:1;min-width:120px;text-align:center;text-decoration:none;border:1px solid var(--hair);background:var(--panel);color:var(--ink);border-radius:10px;padding:11px;font-weight:800;font-size:13px;cursor:pointer}
  .mrow .prim{background:var(--grad);color:#fff;border:none}
  .toast{position:fixed;bottom:24px;inset-inline-start:50%;transform:translateX(50%) translateY(20px);background:var(--dark);color:#fff;padding:12px 20px;border-radius:12px;font-weight:700;font-size:14px;box-shadow:0 14px 40px rgba(0,0,0,.4);opacity:0;pointer-events:none;transition:.25s;z-index:80}
  .toast.show{opacity:1;transform:translateX(50%) translateY(0)}
  @media(max-width:760px){
    .appbar .in{gap:8px;padding:10px 12px;flex-wrap:wrap}
    .appbar .wm{order:1}.abtools{order:2;margin-inline-start:auto}
    .tabs{order:3;flex-basis:100%;overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch;padding-bottom:2px}
    .tabs::-webkit-scrollbar{display:none}
    .hero .in{padding:16px 14px 22px;flex-direction:column;align-items:stretch;gap:16px}.hero h1{font-size:22px}
    .hstat{width:100%}.kpi b{font-size:20px}
    .wrap{padding:16px 14px 70px}
    .search input{width:88px}
    .sec-h h2{font-size:19px}
    .overlay{padding:0;align-items:stretch}
    .modal{border-radius:0;min-height:100vh}
    .mhead{border-radius:0;padding:20px 18px}.mhead h2{font-size:21px}
    .mbody{padding:18px}
    .keynum{min-width:calc(50% - 5px);flex:1 1 calc(50% - 5px)}
    .keynum b{font-size:20px}
    .roi .out{flex-direction:column;gap:12px}
    .roi .out div{text-align:start;display:flex;justify-content:space-between;align-items:baseline}
    .mrow a,.mrow button{min-width:calc(50% - 4px)}
  }
  @media(max-width:420px){.keynum{min-width:100%;flex-basis:100%}}
</style>
</head>
<body>
  <div class="appbar"><div class="in">
    <span class="wm serif" onclick="go('new')">A<i>-</i>WEB</span>
    <nav class="tabs" id="tabs"></nav>
    <div class="abtools">
      <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
        <input id="q" placeholder="עיר / שכונה" oninput="render()"></div>
      <button class="iconbtn" onclick="toggleTheme()">◐</button>
    </div>
  </div></div>

  <div class="hero"><div class="in">
    <div class="htext">
      <h1 class="serif" id="heroTitle"></h1>
      <p id="heroSub"></p>
    </div>
    <aside class="hstat" id="hstat"></aside>
  </div>
  <svg class="skyline" viewBox="0 0 1200 90" preserveAspectRatio="none"><g fill="#241a38">
    <rect x="0" y="46" width="70" height="44"/><rect x="80" y="30" width="54" height="60"/><rect x="146" y="54" width="60" height="36"/><rect x="216" y="20" width="46" height="70"/><rect x="272" y="40" width="70" height="50"/><rect x="352" y="58" width="52" height="32"/><rect x="414" y="26" width="60" height="64"/><rect x="484" y="48" width="66" height="42"/><rect x="560" y="34" width="48" height="56"/><rect x="618" y="52" width="70" height="38"/><rect x="698" y="22" width="52" height="68"/><rect x="760" y="44" width="64" height="46"/><rect x="834" y="56" width="54" height="34"/><rect x="898" y="30" width="58" height="60"/><rect x="966" y="50" width="70" height="40"/><rect x="1046" y="38" width="50" height="52"/><rect x="1106" y="54" width="94" height="36"/></g></svg>
  </div>

  <div class="wrap"><div id="body"></div>
    <div class="foot" id="foot"></div>
  </div>

  <div class="overlay" id="ov" onclick="if(event.target===this)closeDeal()"><div class="modal" id="modal"></div></div>
  <div class="toast" id="toast"></div>

<script>
/*__DATA__*/
const D = window.DATA || {listings:[],areas:[],cities:[],summary_counts:{},caveats:[]};
const S = {tab:'new', theme:'light', sort:'gap', owner:'all'};
const GRADS=['linear-gradient(135deg,#efe7fb,#f7e3ef)','linear-gradient(135deg,#e8eefb,#efe7fb)','linear-gradient(135deg,#f7e3ef,#efe7fb)','linear-gradient(135deg,#e8f6ef,#eef7fb)'];
const BYID={}; D.listings.forEach(d=>BYID[d.id]=d);
/* area lookup: by exact area_key, and city settlement fallback */
const AREA_BY_KEY={}, CITY_TREND={};
(D.areas||[]).forEach(a=>{
  if(a.key) AREA_BY_KEY[a.key]=a;
  if((a.yearly||[]).length>=2 && a.city){
    // prefer settlement-level for the city fallback
    if(!CITY_TREND[a.city] || a.level==='settlement') CITY_TREND[a.city]=a;
  }
});
function areaFor(d){ return (d.akey&&AREA_BY_KEY[d.akey]) || CITY_TREND[d.city] || null; }

/* smart "why" — narrative from structured fields, not raw debug text */
function smartWhy(d){
  const p=[];
  if(d.gap!=null && d.gap>=3 && !d.suspect) p.push(`מתומחר <b>${(+d.gap).toFixed(0)}% ${d.stage==='final'?'מתחת לעסקאות שנסגרו':'מתחת למחיר המבוקש'}</b> באזור`);
  if((d.drop||0)>=5) p.push(`ירד <b>${(+d.drop).toFixed(0)}%</b> במחיר`);
  if(d['yield']>=4.5) p.push(`תשואת שכירות <b>${(+d['yield']).toFixed(1)}%</b>`);
  if(d.cagr>=4) p.push(`באזור שעולה <b>${(+d.cagr).toFixed(1)}%</b> בשנה`);
  if((d.dom||0)>=90) p.push(`<b>${d.dom} ימים</b> באוויר — מוכר שעשוי להיות גמיש`);
  if(!p.length){
    if(d.suspect) return 'הפער מהשוק גדול מהרגיל — שווה בדיקה, אך ודא שאין הבדל גודל או קטגוריה.';
    return 'מלאי טרי שנכנס למעקב. עדיין לא אומת מול עסקאות סגורות — ההזדמנויות המאומתות בטאב "הזדמנויות".';
  }
  return p.join(' · ') + '.';
}

function fmt(n){return (n==null||n==='')?'—':Math.round(n).toLocaleString('en-US');}
function toggleTheme(){S.theme=S.theme==='light'?'dark':'light';document.documentElement.setAttribute('data-theme',S.theme==='dark'?'dark':'');}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2000);}

/* ---- watchlist (localStorage) ---- */
function wlGet(){try{return new Set(JSON.parse(localStorage.getItem('aweb_wl')||'[]'));}catch(e){return new Set();}}
function wlSave(s){try{localStorage.setItem('aweb_wl',JSON.stringify([...s]));}catch(e){}}
let WL=wlGet();
function toggleSave(id,ev){if(ev)ev.stopPropagation();
  if(WL.has(id)){WL.delete(id);toast('הוסר מהמעקב');}else{WL.add(id);toast('נוסף לרשימת המעקב ★');}
  wlSave(WL);render();}

/* ---- nadlan link: specific area ---- */
function nadlanLink(d){
  const parts=[d.street,d.hood,d.city].filter(Boolean);
  const q=encodeURIComponent(parts.join(', '));
  return 'https://www.nadlan.gov.il/?search='+q;
}

const TAB_IC={
  new:'<svg viewBox="0 0 24 24"><path d="M12 3l2 5.5L19.5 10 14 12l-2 5.5L10 12 4.5 10 10 8.5z"/></svg>',
  drops:'<svg viewBox="0 0 24 24"><path d="M3 7l6 6 4-4 8 8"/><path d="M21 17v-4h-4"/></svg>',
  opps:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/></svg>',
  watch:'<svg viewBox="0 0 24 24"><path d="M12 4l2.3 4.7 5.2.8-3.8 3.6.9 5.1L12 15.6 7.4 18.2l.9-5.1L4.5 9.5l5.2-.8z"/></svg>',
  trends:'<svg viewBox="0 0 24 24"><path d="M4 19V5M4 15l5-4 4 3 7-7"/></svg>'};
const TABS=[{k:'new',label:'חדש היום'},{k:'drops',label:'ירידות מחיר'},
  {k:'opps',label:'הזדמנויות'},{k:'watch',label:'המעקב שלי'},{k:'trends',label:'מגמות'}];
function go(k){S.tab=k;render();}

/* ---- hero side stat panel (donut פרטי/תיווך + KPIs) ---- */
function donutSVG(a,b){
  const tot=(a+b)||1, fp=a/tot, r=30, C=2*Math.PI*r, cx=38, cy=38;
  return `<svg class="donut" width="76" height="76" viewBox="0 0 76 76" role="img" aria-label="פרטי ${a} מול תיווך ${b}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#a78bfa" stroke-width="11"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#34d399" stroke-width="11"
      stroke-dasharray="${(fp*C).toFixed(1)} ${(C).toFixed(1)}" transform="rotate(-90 ${cx} ${cy})"/>
    <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central" font-size="16" font-weight="800" fill="#fff" font-family="Frank Ruhl Libre,serif">${Math.round(fp*100)}%</text>
  </svg>`;
}
function statPanel(c){
  const a=c.private||0, b=c.agency||0;
  const opps=D.listings.filter(isOpp).length;
  return `<div class="top">${donutSVG(a,b)}
    <div class="dlabel">
      <div style="font-size:16px;color:#fff;font-weight:800;font-family:'Frank Ruhl Libre',serif;line-height:1.2">${fmt(c.scanned)}<div style="font-weight:400;font-size:11.5px;color:#a8a2ba">מודעות במעקב</div></div>
      <div style="margin-top:8px"><span class="dot" style="background:#34d399"></span>פרטי <b>${a}</b></div>
      <div><span class="dot" style="background:#a78bfa"></span>תיווך <b>${b}</b></div>
    </div></div>
  <div class="kpis">
    <div class="kpi"><b class="v">${c.new||0}</b><span>חדשות היום</span></div>
    <div class="kpi"><b class="r">${c.drops||0}</b><span>ירידות מחיר</span></div>
    <div class="kpi"><b class="g">${opps}</b><span>הזדמנויות מאומתות</span></div>
    <div class="kpi"><b class="g">${c.hot_areas||0}</b><span>אזורים מתחממים</span></div>
  </div>`;
}

function bld(){return '<svg class="bld" viewBox="0 0 100 80"><g stroke="#7b3ff2" fill="none" stroke-width="2" opacity=".8"><rect x="20" y="26" width="42" height="50"/><rect x="62" y="36" width="22" height="40"/><line x1="28" y1="36" x2="54" y2="36"/><line x1="28" y1="48" x2="54" y2="48"/><line x1="28" y1="60" x2="54" y2="60"/><line x1="68" y1="48" x2="80" y2="48"/><line x1="68" y1="60" x2="80" y2="60"/></g></svg>';}
function iR(){return '<svg viewBox="0 0 24 24"><path d="M3 21V10l9-6 9 6v11"/><rect x="9" y="14" width="6" height="7"/></svg>';}
function iS(){return '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16"/></svg>';}
function gapLabel(d){return d.stage==='final'?'מתחת לשוק':'מתחת למבוקש';}

function card(d,i){
  const g=GRADS[i%GRADS.length];
  let rib='';
  if(d.dropped||d.drop>=5) rib=`<span class="ribbon">▼ ירד ${(+d.drop).toFixed(0)}%</span>`;
  else if(d.isnew) rib=`<span class="newbadge">✦ חדש היום</span>`;
  else if(d.cagr) rib=`<span class="ribbon gold">עליית ערך ${(+d.cagr).toFixed(1)}%</span>`;
  const chips=[];
  if(d.gap!=null && d.gap>0 && !d.suspect) chips.push(`<span class="b">${gapLabel(d)} ${(+d.gap).toFixed(0)}%</span>`);
  if(d.suspect) chips.push(`<span class="s">לאימות</span>`);
  if(d['yield']) chips.push(`<span class="n">תשואה ${(+d['yield']).toFixed(1)}%</span>`);
  else if(d.cagr) chips.push(`<span class="n">אזור צומח</span>`);
  const why = smartWhy(d);
  const saved=WL.has(d.id);
  return `<article class="card">
    <div class="thumb" style="background:${g}" onclick="openDeal('${d.id}')">${rib}${d.tier?`<span class="tierbadge">${d.tier}</span>`:''}
      <button class="savebtn ${saved?'on':''}" onclick="toggleSave('${d.id}',event)" title="מעקב">★</button>${bld()}</div>
    <div class="cbody">
      <div class="ctop"><div class="cplace" onclick="openDeal('${d.id}')">${d.city||''} ${d.hood?`<span>· ${d.hood}</span>`:''}</div>
        <div class="cprice serif">${fmt(d.price)} ₪</div></div>
      <div class="facts">
        <span class="f">${iR()}${d.rooms||'?'} חד׳</span>
        <span class="f">${iS()}${d.size||'?'} מ״ר</span>
        ${d.ppm?`<span class="f">${fmt(d.ppm)} ₪/מ״ר</span>`:''}
      </div>
      <div class="kv">${d.dtype?`<span class="own ${d.dtype==='agency'?'ag':''}">${d.dtype==='agency'?'תיווך':'פרטי'}</span>`:''}${chips.join('')||'<span class="n">במעקב</span>'}</div>
      <div class="why">${why?`<b>למה:</b> ${why}`:''}</div>
      <div class="cta">
        <button class="go" onclick="openDeal('${d.id}')">פירוט מלא ←</button>
        ${d.url?`<a class="det" href="${d.url}" target="_blank" rel="noopener">יד2 ↗</a>`:''}
      </div>
    </div></article>`;
}

function feed(list, emptyMsg){
  const q=(document.getElementById('q').value||'').trim();
  let arr=list.slice();
  if(q) arr=arr.filter(d=>((d.city||'')+' '+(d.hood||'')).includes(q));
  if(S.owner==='private') arr=arr.filter(d=>d.dtype==='private');
  else if(S.owner==='agency') arr=arr.filter(d=>d.dtype==='agency');
  const key=S.sort;
  arr.sort((a,b)=>{
    if(key==='drop') return (b.drop||0)-(a.drop||0);
    if(key==='yield') return (b['yield']||0)-(a['yield']||0);
    if(key==='price') return (a.price||0)-(b.price||0);
    return (b.gap||-99)-(a.gap||-99);
  });
  S._view=arr;
  if(!arr.length) return `<div class="empty"><div class="ic">🔍</div>${emptyMsg}</div>`;
  return `<div class="cards">${arr.slice(0,90).map(card).join('')}</div>`;
}

function toolbar(){
  const oc=(k,l)=>`<button class="chipf ${S.owner===k?'on':''}" onclick="S.owner='${k}';render()">${l}</button>`;
  return `<div class="toolbar">
    ${oc('all','הכול')}${oc('private','פרטי בלבד')}${oc('agency','תיווך')}
    <select onchange="S.sort=this.value;render()">
      <option value="gap"${S.sort==='gap'?' selected':''}>מיון: מתחת לשוק</option>
      <option value="drop"${S.sort==='drop'?' selected':''}>מיון: ירידת מחיר</option>
      <option value="yield"${S.sort==='yield'?' selected':''}>מיון: תשואה</option>
      <option value="price"${S.sort==='price'?' selected':''}>מיון: מחיר</option>
    </select>
    <button class="exp" onclick="exportCSV()"><svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>ייצוא לאקסל</button>
  </div>`;
}

function render(){
  document.documentElement.setAttribute('data-theme',S.theme==='dark'?'dark':'');
  const c=D.summary_counts||{};
  document.getElementById('tabs').innerHTML = TABS.map(t=>{
    let n='';
    if(t.k==='new') n=c.new; else if(t.k==='drops') n=D.listings.filter(d=>(d.drop||0)>0 && !d.disq).length;
    else if(t.k==='opps') n=D.listings.filter(isOpp).length; else if(t.k==='watch') n=WL.size;
    return `<a class="${S.tab===t.k?'on':''}" onclick="go('${t.k}')">${TAB_IC[t.k]||''}<span>${t.label}</span>${(n!==''&&n!=null)?`<span class="cnt">${n}</span>`:''}</a>`;
  }).join('');
  const H={new:['מה חדש היום','רק מודעות שהתגלו או השתנו מאז אתמול — לא אותה רשימה כל יום.'],
    drops:['ראדאר ירידות מחיר','כל מודעה שירדה במחיר — כמה ירדה וכמה היא מתחת לשוק.'],
    opps:['ההזדמנויות','מתומחר מתחת לעסקאות סגורות דומות באזור, אחרי אימות מול רשות המיסים.'],
    watch:['המעקב שלי','הנכסים ששמרת. נשמרים במכשיר שלך.'],
    trends:['לאן השוק זז','עליית מחירי הנדל״ן לפי אזור — לפי עסקאות אמיתיות.']};
  const words=H[S.tab][0].split(' ');
  document.getElementById('heroTitle').innerHTML = `${words.slice(0,-1).join(' ')} <span class="grad-text">${words.slice(-1)}</span>`;
  document.getElementById('heroSub').textContent = H[S.tab][1];
  document.getElementById('hstat').innerHTML = statPanel(c);

  const body=document.getElementById('body');
  if(S.tab==='trends'){ body.innerHTML=trends(); }
  else {
    let list, empty;
    if(S.tab==='new'){ list=D.listings.filter(d=>d.isnew && !d.disq); empty='אין מודעות חדשות בריצה האחרונה — חזור מחר.'; }
    else if(S.tab==='drops'){ list=D.listings.filter(d=>(d.drop||0)>0 && !d.disq); empty='אין ירידות מחיר כרגע.'; }
    else if(S.tab==='watch'){ list=D.listings.filter(d=>WL.has(d.id)); empty='עדיין לא שמרת נכסים. לחץ על ★ בכל כרטיס.'; }
    else { list=D.listings.filter(isOpp); empty='אין הזדמנויות מאומתות בריצה הזו.'; }
    body.innerHTML = toolbar() + feed(list, empty);
  }
  document.getElementById('foot').innerHTML =
    `הנתונים מ‑${D.run_date||''} · עסקאות רשות המיסים גרסה ${D.data_version||'—'}<br>מופעל על ידי מנוע נדל״ן סקאוט של A‑WEB.`;
  if(!S._noscroll) window.scrollTo(0,0); S._noscroll=false;
}
function isOpp(d){ return !d.disq && d.stage==='final' && ((d.gap!=null && d.gap>=5 && !d.suspect) || d.tier); }

/* ---- CSV export (Excel, UTF-8 BOM) ---- */
function exportCSV(){
  const arr=S._view||[];
  if(!arr.length){toast('אין מה לייצא בתצוגה הזו');return;}
  const own=d=>d.dtype==='agency'?'תיווך':d.dtype==='private'?'פרטי':'';
  const cols=[['עיר','city'],['שכונה','hood'],['סוג מוכר',own],['מחיר','price'],['חדרים','rooms'],['מ"ר','size'],
    ['₪ למ"ר','ppm'],['מתחת לשוק %','gap'],['ירידת מחיר %','drop'],['תשואה %','yield'],
    ['עליית ערך %','cagr'],['מצב','cond'],['דירוג','tier'],['רמת השוואה','complevel'],['קישור יד2','url']];
  const esc=v=>{v=(v==null)?'':(''+v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
  const val=(d,c)=>typeof c==='function'?c(d):d[c];
  const rows=[cols.map(c=>c[0]).join(',')];
  arr.forEach(d=>rows.push(cols.map(c=>esc(val(d,c[1]))).join(',')));
  const csv='﻿'+rows.join('\r\n');
  const fname='nadlan-scout-'+(S.tab)+'-'+(D.run_date||'')+'.csv';
  try{
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob); a.download=fname;
    document.body.appendChild(a); a.click();
    setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},1500);
    toast('הקובץ ירד — נפתח באקסל');
  }catch(e){
    // fallback for sandboxed viewers that block Blob downloads
    const a=document.createElement('a');
    a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
    a.download=fname; a.target='_blank'; a.click();
    toast('אם ההורדה נחסמה — פתח את הקובץ בדפדפן מלא');
  }
}

/* ---- charts ---- */
function lineChart(series, valKey){
  const pts=series.map(s=>s[valKey]).filter(v=>v!=null);
  if(pts.length<2) return '';
  const W=460,Hh=150,pad=32;
  const max=Math.max(...pts),min=Math.min(...pts),rng=(max-min)||1;
  const xs=i=>pad+i*((W-pad*2)/(pts.length-1));const ys=v=>pad+(1-(v-min)/rng)*(Hh-pad*2);
  const line=pts.map((v,i)=>`${i?'L':'M'}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ');
  const area=`M${xs(0)},${ys(pts[0])} `+pts.map((v,i)=>`L${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ')+` L${xs(pts.length-1)},${Hh-pad} L${xs(0)},${Hh-pad} Z`;
  return `<svg viewBox="0 0 ${W} ${Hh}" style="width:100%;height:auto">
    <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7b3ff2" stop-opacity=".22"/><stop offset="1" stop-color="#7b3ff2" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#ag)"/><path d="${line}" fill="none" stroke="#7b3ff2" stroke-width="2.5" stroke-linejoin="round"/>
    ${pts.map((v,i)=>`<circle cx="${xs(i).toFixed(1)}" cy="${ys(v).toFixed(1)}" r="${i===pts.length-1?4:2.5}" fill="${i===pts.length-1?'#e0489e':'#7b3ff2'}"/>`).join('')}
    ${series.map((s,i)=>`<text x="${xs(i).toFixed(1)}" y="${Hh-8}" font-size="10" fill="#8b8896" text-anchor="middle">${s.y||''}</text>`).join('')}
    <text x="${pad}" y="16" font-size="10" fill="#8b8896">${fmt(pts[0])} ₪/מ״ר</text>
    <text x="${W-pad}" y="16" font-size="10" fill="#e0489e" text-anchor="end">${fmt(pts[pts.length-1])} ₪/מ״ר</text>
  </svg>`;
}
function positionBar(mine, market){
  if(!mine||!market) return '';
  const lo=market*0.65, hi=market*1.35, cl=v=>Math.max(lo,Math.min(hi,v)), pc=v=>((cl(v)-lo)/(hi-lo))*100;
  const mp=pc(mine), kp=pc(market), below=mine<market;
  const diff=Math.round((mine/market-1)*100);
  return `<div style="position:relative;height:60px;margin:10px 0 2px">
    <div style="position:absolute;top:30px;left:0;right:0;height:8px;border-radius:999px;background:linear-gradient(90deg,#e4f6ee,#f3f0f8,#fdeaee)"></div>
    <div style="position:absolute;top:24px;left:${kp}%;transform:translateX(-50%);text-align:center">
      <div style="width:2px;height:20px;background:#8b8896;margin:0 auto"></div>
      <div style="font-size:10px;color:#8b8896;white-space:nowrap;margin-top:2px">חציון אזור</div></div>
    <div style="position:absolute;top:2px;left:${mp}%;transform:translateX(-50%);text-align:center">
      <div style="font-size:11px;font-weight:800;color:${below?'#0f9d63':'#e0455e'};white-space:nowrap">הדירה ${below?diff+'%':'+'+diff+'%'}</div>
      <div style="width:15px;height:15px;border-radius:999px;background:${below?'#0f9d63':'#e0455e'};margin:3px auto;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.25)"></div></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:10px;color:#8b8896"><span>← זול יותר</span><span>יקר יותר →</span></div>`;
}

/* ---- price timeline (dated) ---- */
function fmtDate(s){ if(!s) return ''; const m=String(s).slice(0,10).split('-'); return m.length===3?(m[2]+'/'+m[1]):String(s).slice(0,10); }
function priceTimeline(d){
  const h=(d.hist||[]).filter(x=>x&&x.p);
  if(h.length<2){
    const pub=d.first_seen?fmtDate(d.first_seen):(h[0]&&fmtDate(h[0].d));
    const pr=h[0]?fmt(h[0].p):fmt(d.price);
    return `<div style="color:var(--ink2);font-size:13.5px;background:var(--hair2);border-radius:12px;padding:14px 16px">פורסמה${pub?' ב-'+pub:''} במחיר <b>${pr} ₪</b> — המחיר <b>לא השתנה</b> מאז.</div>`;
  }
  const W=470,H=180,pad=36,padTop=34;
  const ps=h.map(x=>x.p),max=Math.max(...ps),min=Math.min(...ps),rng=(max-min)||1;
  const xs=i=>pad+i*((W-pad*2)/(h.length-1));
  const ys=v=>padTop+(1-(v-min)/rng)*(H-padTop-pad);
  const line=h.map((x,i)=>`${i?'L':'M'}${xs(i).toFixed(1)},${ys(x.p).toFixed(1)}`).join(' ');
  const area=`M${xs(0)},${ys(ps[0])} `+h.map((x,i)=>`L${xs(i).toFixed(1)},${ys(x.p).toFixed(1)}`).join(' ')+` L${xs(h.length-1)},${H-pad} L${xs(0)},${H-pad} Z`;
  const drop=((h[0].p-h[h.length-1].p)/h[0].p*100), down=drop>0, col=down?'#0f9d63':'#e0455e';
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
    <defs><linearGradient id="pt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".18"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#pt)"/>
    <path d="${line}" fill="none" stroke="${col}" stroke-width="2.6" stroke-linejoin="round"/>
    ${h.map((x,i)=>{const first=i===0,last=i===h.length-1;return `
      <circle cx="${xs(i).toFixed(1)}" cy="${ys(x.p).toFixed(1)}" r="${(first||last)?4.5:3}" fill="${last?'#e0489e':col}"/>
      <text x="${xs(i).toFixed(1)}" y="${(ys(x.p)-9).toFixed(1)}" font-size="10.5" font-weight="700" fill="${last?'#e0489e':'#4a4756'}" text-anchor="middle">${fmt(x.p)}</text>
      <text x="${xs(i).toFixed(1)}" y="${H-10}" font-size="10" fill="#8b8896" text-anchor="middle">${fmtDate(x.d)}</text>
      ${first?`<text x="${xs(i).toFixed(1)}" y="16" font-size="10" fill="#8b8896" text-anchor="middle">פורסמה</text>`:''}
      ${last?`<text x="${xs(i).toFixed(1)}" y="16" font-size="10" fill="#e0489e" text-anchor="middle">היום</text>`:''}`;}).join('')}
  </svg>
  <div style="text-align:center;margin-top:4px;font-weight:800;font-size:14px;color:${col}">${down?'▼ ירד '+drop.toFixed(1)+'% מאז הפרסום':'▲ עלה '+Math.abs(drop).toFixed(1)+'% מאז הפרסום'}</div>`;
}

/* ---- deal detail modal ---- */
function openDeal(id){
  const d=BYID[id]; if(!d) return;
  const area=areaFor(d);
  const marketPpm = d.mppm || (area && area.yearly && area.yearly.length? area.yearly[area.yearly.length-1].ppm : null);
  const flags=[];
  flags.push(d.comps && d.comps>=3 ? ['ok','המחיר מושווה מול עסקאות אמיתיות שנסגרו באזור'] : ['warn','עדיין אין מספיק עסקאות סגורות להשוואה מלאה']);
  flags.push(d.size && d.rooms && d.size/d.rooms>=15 && d.size/d.rooms<=60 ? ['ok','גודל הדירה סביר למספר החדרים'] : ['warn','בדוק את יחס גודל/חדרים']);
  flags.push((d.drop||0)<60 ? ['ok', (d.drop>0?`ירידת המחיר (${(+d.drop).toFixed(0)}%) בטווח הגיוני`:'אין קפיצות מחיר חשודות')] : ['warn','ירידת מחיר חריגה — ייתכן שגיאת נתונים']);
  if(d.suspect) flags.push(['warn','הפער מהשוק גדול מהרגיל — ודא שאין הבדל גודל/קטגוריה']);
  flags.push(['warn','ודא היטל השבחה / חובות ועד בית לפני חתימה']);
  const rentGuess = d.rent || Math.round((d.price||1000000)*0.0035/100)*100 || 3200;
  const trendBlock = (area && area.yearly && area.yearly.length>=2)
    ? `<div class="dsec"><h3>מגמת מחירים באזור${area.name?' · '+area.name:''}</h3>
        <p class="ph">מחיר עסקה חציוני למ״ר לפי שנה — לפי עסקאות שנסגרו ברשות המיסים.${area.cagr?` עלייה של ${(+area.cagr).toFixed(1)}% בשנה בממוצע.`:''}</p>
        ${lineChart(area.yearly,'ppm')}</div>`
    : '';
  const posBlock = (d.ppm && marketPpm)
    ? `<div class="dsec"><h3>מיקום מול השוק</h3><p class="ph">₪/מ״ר של הדירה מול חציון האזור.</p>${positionBar(d.ppm,marketPpm)}</div>`
    : '';
  const histBlock = `<div class="dsec"><h3>מסלול המחיר של המודעה</h3><p class="ph">מתי פורסמה, מתי ירד המחיר, וכמה — לפי הרישום בפועל ביד2.</p>${priceTimeline(d)}</div>`;
  document.getElementById('modal').innerHTML = `
    <div class="mhead"><button class="xbtn" onclick="closeDeal()">✕</button><div class="z">
      <h2 class="serif">${d.city||''} ${d.hood?'· '+d.hood:''}</h2>
      <div class="loc">${d.rooms||'?'} חדרים · ${d.size||'?'} מ״ר${d.cond?' · '+d.cond:''}${d.dtype?(' · '+(d.dtype==='agency'?'תיווך':'מוכר פרטי')):''}${d.tier?' · '+d.tier:''}${d.isnew?' · ✦ חדש היום':''}</div>
      <div class="pr serif">${fmt(d.price)} ₪</div>
    </div></div>
    <div class="mbody">
      <div class="keynums">
        <div class="keynum"><b class="grad-text">${d.suspect?'⚠':(d.gap!=null?(+d.gap).toFixed(0)+'%':'—')}</b><span>${d.suspect?'פער לאימות':gapLabel(d)}</span></div>
        <div class="keynum"><b class="grad-text">${d.cagr!=null?(+d.cagr).toFixed(1)+'%':'—'}</b><span>עליית ערך שנתית</span></div>
        <div class="keynum"><b class="grad-text">${d['yield']!=null?(+d['yield']).toFixed(1)+'%':'—'}</b><span>תשואת שכירות</span></div>
        <div class="keynum"><b class="grad-text">${d.ppm?fmt(d.ppm):'—'}</b><span>₪ למ״ר</span></div>
      </div>
      <div class="dsec"><h3>למה זו עסקה</h3><p class="ph" style="color:var(--ink2);font-size:13.5px">${smartWhy(d)}</p></div>
      ${histBlock}${posBlock}${trendBlock}
      <div class="dsec"><h3>צ׳ק־ליסט דגלים אדומים</h3><ul class="flags">${flags.map(f=>`<li><span class="${f[0]}">${f[0]==='ok'?'✓':'!'}</span> ${f[1]}</li>`).join('')}</ul></div>
      <div class="dsec roi"><h3>מחשבון השקעה ומשכנתא</h3><p class="ph">כל הפרמטרים אינטראקטיביים — גרור וראה איך התשואה משתנה.</p>
        <div style="display:flex;gap:16px;flex-wrap:wrap">
          <div style="flex:1;min-width:200px">
            <label>הון עצמי: <span id="eqV">50%</span></label>
            <input type="range" id="eqp" min="25" max="100" value="50" oninput="calcRoi('${id}')">
            <label>ריבית משכנתא (שנתית): <span id="rateV">5.0%</span></label>
            <input type="range" id="rate" min="2" max="8" step="0.1" value="5" oninput="calcRoi('${id}')">
          </div>
          <div style="flex:1;min-width:200px">
            <label>תקופת משכנתא: <span id="termV">25 שנה</span></label>
            <input type="range" id="term" min="10" max="30" step="1" value="25" oninput="calcRoi('${id}')">
            <label>שכר דירה חודשי צפוי: <span id="rentV">₪${fmt(rentGuess)}</span></label>
            <input type="range" id="rent" min="1500" max="9000" step="100" value="${rentGuess}" oninput="calcRoi('${id}')">
          </div>
        </div>
        <div class="out">
          <div><b id="rPay">—</b><span>החזר חודשי</span></div>
          <div><b id="rFlow">—</b><span>תזרים חודשי (שכ״ד − החזר)</span></div>
          <div><b id="rCoc">—</b><span>תשואה על ההון (Cash-on-Cash)</span></div>
        </div>
        <div class="out" style="border-top:none;padding-top:2px">
          <div><b id="rGross">—</b><span>תשואה ברוטו</span></div>
          <div><b id="rCash">—</b><span>הון עצמי נדרש</span></div>
          <div><b id="rLoan">—</b><span>סכום המשכנתא</span></div>
        </div>
      </div>
      <div class="dsec">
        <h3>תחזית וערך — התמונה המלאה</h3>
        <p class="ph">עלות הכניסה האמיתית (כולל מס רכישה) והקרנת שווי לפי קצב עליית הערך ההיסטורי של האזור.</p>
        <div class="out" style="border-top:none;padding-top:0">
          <div><b id="pTax">—</b><span>מס רכישה (משקיע)</span></div>
          <div><b id="pEntry">—</b><span>מזומן נדרש בכניסה</span></div>
          <div><b id="pNet">—</b><span>תשואה נטו משוערת</span></div>
        </div>
        <div class="out">
          <div><b id="pV5">—</b><span>שווי צפוי · 5 שנים</span></div>
          <div><b id="pV10">—</b><span>שווי צפוי · 10 שנים</span></div>
          <div><b id="pTot">—</b><span>תשואה כוללת · 10 שנים</span></div>
        </div>
        <p class="ph" id="pNote" style="margin-top:10px;color:var(--muted)"></p>
      </div>
      <div class="mrow">
        ${d.url?`<a class="prim" href="${d.url}" target="_blank" rel="noopener">פתח ביד2 ↗</a>`:''}
        <a href="${nadlanLink(d)}" target="_blank" rel="noopener">עסקאות באזור ↗</a>
        <button onclick="toggleSave('${id}');closeDeal()">${WL.has(id)?'★ במעקב':'☆ הוסף למעקב'}</button>
      </div>
    </div>`;
  document.getElementById('ov').classList.add('show');
  calcRoi(id);
}
function closeDeal(){document.getElementById('ov').classList.remove('show');}
function calcRoi(id){
  const d=BYID[id]; if(!d) return;
  const price=d.price||1000000;
  const eqp=+document.getElementById('eqp').value, rate=+document.getElementById('rate').value;
  const term=+document.getElementById('term').value, rent=+document.getElementById('rent').value;
  document.getElementById('eqV').textContent=eqp+'%';
  document.getElementById('rateV').textContent=rate.toFixed(1)+'%';
  document.getElementById('termV').textContent=term+' שנה';
  document.getElementById('rentV').textContent='₪'+fmt(rent);
  const equity=price*eqp/100, loan=price-equity;
  const r=rate/100/12, n=term*12;
  const pay = loan<=0?0:(r>0? loan*r/(1-Math.pow(1+r,-n)) : loan/n);
  const flow=rent-pay, annual=rent*12, gross=annual/price*100;
  const coc = equity>0? (flow*12)/equity*100 : gross;
  document.getElementById('rPay').textContent='₪'+fmt(pay);
  const rf=document.getElementById('rFlow'); rf.textContent=(flow>=0?'+':'')+'₪'+fmt(flow);
  rf.style.color = flow>=0?'var(--green)':'var(--red)';
  document.getElementById('rCoc').textContent=coc.toFixed(1)+'%';
  document.getElementById('rGross').textContent=gross.toFixed(1)+'%';
  document.getElementById('rCash').textContent='₪'+fmt(equity);
  document.getElementById('rLoan').textContent='₪'+fmt(loan);

  // ── תחזית וערך ──
  const tax=price*0.08;                 // מדרגת משקיע (דירה שנייה) — 8%
  const buyCosts=price*0.015;           // עו"ד + נלוות משוער ~1.5%
  const entryCash=equity+tax+buyCosts;  // המזומן שצריך בפועל בכניסה
  const net=gross*0.72;                 // אחרי ~28% עלויות: ארנונה/ניהול/ריקות/תחזוקה
  const g=(d.cagr||0)/100;
  const v5 = g? price*Math.pow(1+g,5):null;
  const v10= g? price*Math.pow(1+g,10):null;
  let tot=null;
  if(v10){
    const capGain=v10-price;
    const netRent10=annual*0.72*10 - pay*12*Math.min(term,10);
    tot=((capGain+netRent10)/(entryCash||1))*100;
  }
  const SET=(id,v)=>{const e=document.getElementById(id); if(e) e.textContent=v;};
  SET('pTax','₪'+fmt(tax));
  SET('pEntry','₪'+fmt(entryCash));
  SET('pNet',net.toFixed(1)+'%');
  SET('pV5', v5?('₪'+fmt(v5)):'—');
  SET('pV10', v10?('₪'+fmt(v10)):'—');
  SET('pTot', tot!=null?((tot>=0?'+':'')+tot.toFixed(0)+'%'):'—');
  const noteEl=document.getElementById('pNote');
  if(noteEl) noteEl.textContent = g
    ? `הקרנה לפי קצב היסטורי של ${(+d.cagr).toFixed(1)}% בשנה באזור — אומדן, לא הבטחה. התשואה הכוללת מחשבת רווח הון + שכ״ד נטו פחות החזרי משכנתא, על המזומן שהושקע.`
    : `אין נתוני עליית ערך לאזור זה — התחזית מבוססת על תזרים בלבד. מס רכישה לפי מדרגת משקיע (8%).`;
}

function trends(){
  const areas=(D.areas||[]).filter(a=>a.cagr!=null).sort((a,b)=>b.cagr-a.cagr).slice(0,12);
  const mx=areas.length?areas[0].cagr:10;
  const arows=areas.map(a=>`<tr><td><b>${a.name||a.city||''}</b>${a.city&&a.name!==a.city?` <span style="color:var(--muted)">· ${a.city}</span>`:''}</td>
     <td class="up">+${(+a.cagr).toFixed(1)}%</td><td><div class="bar"><i style="width:${Math.max(6,Math.min(100,a.cagr/mx*100))}%"></i></div></td></tr>`).join('');
  const cities=(D.cities||[]).filter(c=>c.ppm).sort((a,b)=>(b.change||0)-(a.change||0)).slice(0,14);
  const crows=cities.map(c=>`<tr><td><b>${c.city}</b></td><td><span class="nl">${fmt(c.ppm)}</span> ₪/מ״ר</td>
     <td class="${(c.change||0)>=0?'up':''}">${c.change!=null?((c.change>=0?'+':'')+(+c.change).toFixed(1)+'%'):'—'}</td>
     <td style="color:var(--muted)">${c.active||0} פעילות</td></tr>`).join('');
  return `<div class="sec-h"><h2 class="serif">אזורים מתחממים — קצב עליית ערך שנתי</h2></div>
    <p class="sec-sub">לפי עסקאות אמיתיות שנסגרו. סמן ארוך = עלייה חזקה יותר.</p>
    <div class="trend"><table><tbody>${arows||'<tr><td>אין נתוני עליית ערך בריצה הזו</td></tr>'}</tbody></table></div>
    <div class="sec-h" style="margin-top:26px"><h2 class="serif">מחירי הערים</h2></div>
    <p class="sec-sub">חציון ₪/מ״ר רשמי ושינוי מחיר, לפי רשות המיסים.</p>
    <div class="trend"><table><thead><tr><th>עיר</th><th>מחיר נוכחי</th><th>שינוי</th><th>היצע</th></tr></thead>
      <tbody>${crows||'<tr><td>—</td></tr>'}</tbody></table></div>`;
}

render();
</script>
</body>
</html>
"""
