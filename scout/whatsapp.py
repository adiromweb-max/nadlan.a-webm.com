"""
שליחת עסקת בוננזה לוואטסאפ דרך Green-API (שער מתארח).

הרעיון: שולחים **אליך** (הודעה לעצמך / מספר שלך), ואתה מעביר בלחיצה לקבוצה.
Green-API מחזיק את חיבור הוואטסאפ החי, ולכן זה עובד גם מ-GitHub Actions הארעי —
אנחנו רק קוראים ל-HTTP API שלהם.

סודות מהסביבה:
  GREENAPI_ID        - idInstance (מזהה ה-instance)
  GREENAPI_TOKEN     - apiTokenInstance (הטוקן)
  GREENAPI_TO        - מספר היעד עם קידומת מדינה, בלי + (למשל 972501234567)
  GREENAPI_URL       - בסיס API (ברירת מחדל https://api.green-api.com)
  GREENAPI_MEDIA_URL - בסיס העלאת מדיה (ברירת מחדל https://media.green-api.com)

כשל בשליחה לעולם לא מפיל את הריצה — נרשם ללוג בלבד.
"""
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TIMEOUT = 60


def _cfg():
    return {
        "id": os.environ.get("GREENAPI_ID"),
        "token": os.environ.get("GREENAPI_TOKEN"),
        "to": os.environ.get("GREENAPI_TO"),
        "url": (os.environ.get("GREENAPI_URL") or "https://api.green-api.com").rstrip("/"),
        "media": (os.environ.get("GREENAPI_MEDIA_URL") or "https://media.green-api.com").rstrip("/"),
    }


def configured(c=None):
    c = c or _cfg()
    return bool(c["id"] and c["token"] and c["to"])


def _chat_id(to):
    digits = re.sub(r"\D", "", to or "")
    return f"{digits}@c.us"


def send_text(text, c=None):
    c = c or _cfg()
    url = f"{c['url']}/waInstance{c['id']}/sendMessage/{c['token']}"
    r = requests.post(url, json={"chatId": _chat_id(c["to"]), "message": text}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def send_file(path, caption="", c=None):
    c = c or _cfg()
    url = f"{c['media']}/waInstance{c['id']}/sendFileByUpload/{c['token']}"
    with open(path, "rb") as f:
        files = {"file": (Path(path).name, f)}
        data = {"chatId": _chat_id(c["to"])}
        if caption:
            data["caption"] = caption
        r = requests.post(url, files=files, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def send_bonanza(results):
    """
    לכל עסקה: שולח הודעה אחת ראשית = **הכרטיס הממותג + הטקסט ככיתוב** (כך שאפשר
    להעביר אותה כמו שהיא לקבוצה), ואז את תמונות הנכס האמיתיות כהודעות נוספות.
    """
    c = _cfg()
    if not configured(c):
        log.info("Green-API לא מוגדר (חסר GREENAPI_ID/TOKEN/TO) — מדלג על וואטסאפ")
        return False

    sent_any = False
    for r in results:
        try:
            text = Path(r["text_file"]).read_text(encoding="utf-8")
        except Exception:
            text = "עסקת בוננזה חדשה — נדל\"ן סקאוט"

        card = r.get("card_png")
        photos = [p for p in (r.get("images") or []) if p and Path(p).exists()]
        primary = card if (card and Path(card).exists()) else (photos[0] if photos else None)

        try:
            if primary:
                send_file(primary, caption=text, c=c)
            else:
                send_text(text, c=c)
            sent_any = True
            # תמונות הנכס הנוספות (לא הראשית ששלחנו)
            extras = [p for p in photos if p != primary][:3]
            for img in extras:
                try:
                    send_file(img, c=c)
                except Exception as e:
                    log.debug("whatsapp extra photo failed: %s", e)
            log.info("whatsapp: נשלחה עסקה %s", r.get("id"))
        except Exception as e:
            log.warning("whatsapp: שליחת %s נכשלה: %s", r.get("id"), e)

    return sent_any
