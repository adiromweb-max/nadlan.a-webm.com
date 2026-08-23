"""
עוזר HTTP: User-Agent אמיתי, timeout, retry מוגבל, השהיות בין בקשות.

משמש לבקשות ל-data.nadlan.gov.il (ציבורי, ישיר, בלי ScraperAPI).
בקשות ליד2 עוברות דרך scout/scraperapi.py.

בטוח לתהליכונים: session נפרד לכל תהליכון, ונעילה על חישוב ההשהיה.
"""
import logging
import random
import threading
import time

import requests

log = logging.getLogger(__name__)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class Fetcher:
    """קליינט HTTP מנומס: השהיה בין בקשות, retry מוגבל, לא זורק חריגות."""

    def __init__(self, delay=3.0, timeout=20, retries=2, extra_headers=None,
                 proxy_key=None):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.extra_headers = extra_headers or {}
        # אם proxy_key מוגדר, כל בקשה מנותבת דרך ScraperAPI (מצב רגיל, קרדיט
        # אחד) — נדרש כשה-IP של הרץ (למשל GitHub Actions) חסום ע"י nadlan.
        self.proxy_key = proxy_key
        self._local = threading.local()
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _wrap(self, url):
        """עוטף URL דרך ScraperAPI אם מוגדר proxy_key, אחרת מחזיר כמו שהוא."""
        if not self.proxy_key:
            return url
        import urllib.parse
        return "https://api.scraperapi.com/?" + urllib.parse.urlencode(
            {"api_key": self.proxy_key, "url": url}, safe="")

    @property
    def session(self):
        """session לכל תהליכון — requests.Session אינו בטוח לשיתוף."""
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": BROWSER_UA,
                "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "application/json, text/plain, */*",
            })
            s.headers.update(self.extra_headers)
            self._local.session = s
        return s

    def _throttle(self):
        """שומר על מרווח מינימלי בין בקשות (עם ג'יטר) כדי לא להעמיס."""
        if self.delay <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            wait = self.delay + random.uniform(0, self.delay * 0.6) - elapsed
            # מעדכנים מיד כדי ששני תהליכונים לא יחשבו את אותו חלון
            self._last_request = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def get(self, url, **kwargs):
        """GET עם retry. מחזיר Response או None בכשל."""
        last_err = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = self.session.get(self._wrap(url), timeout=self.timeout, **kwargs)
                if resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    log.debug("שגיאת שרת %s ב-%s (ניסיון %d)", resp.status_code, url, attempt + 1)
                    time.sleep(2 ** attempt)
                    continue
                return resp
            except requests.RequestException as e:
                last_err = str(e)
                log.debug("כשל בקשה ל-%s: %s (ניסיון %d)", url, e, attempt + 1)
                time.sleep(2 ** attempt)
        log.warning("נכשלה בקשה ל-%s: %s", url, last_err)
        return None

    def get_json(self, url, **kwargs):
        """GET שמחזיר JSON או None. עומד גם ב-BOM (utf-8-sig) של קבצי nadlan."""
        resp = self.get(url, **kwargs)
        if resp is None or resp.status_code != 200:
            if resp is not None:
                log.warning("תשובה לא תקינה מ-%s: HTTP %s", url, resp.status_code)
            return None
        try:
            # קבצי ה-S3 של nadlan מוגשים עם BOM — json.loads הרגיל נכשל עליהם
            return resp.json()
        except ValueError:
            try:
                import json
                return json.loads(resp.content.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError) as e:
                log.warning("JSON לא תקין מ-%s: %s", url, e)
                return None
