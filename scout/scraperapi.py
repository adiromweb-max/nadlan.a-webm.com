"""
תעבורת ScraperAPI — כל בקשה ליד2 עוברת דרך כאן.

למה: yad2.co.il מוגן ב-Radware Bot Manager. בקשה ישירה מהשרת הזה (גרמניה)
מקבלת דף Captcha/אתגר JS. ScraperAPI עם ‎country_code=il‎ מחזיר את ה-HTML
האמיתי של דף החיפוש.

מה נבדק בפועל (אוגוסט 2026) מול החשבון הזה:
  * ‎country_code=il‎ בלבד, בלי render — עובד ומחזיר את ה-HTML המלא. עלות:
    קרדיט אחד לבקשה מוצלחת. זו הדרך שאנחנו משתמשים בה.
  * ‎render=true‎ — עבד פעם אחת ואז החל להיכשל. לא אמין, ועולה פי 10.
  * ‎premium=true‎ / ‎ultra_premium=true‎ — לא זמינים במסלול הנוכחי
    (403 "your current plan does not allow"). לכן אין הסלמה לשם.
  * נקודות הקצה של ה-API (gw.yad2.co.il) חסומות גם דרך ScraperAPI — רק
    דפי ה-HTML עוברים. לכן yad2.py מפרסר ‎__NEXT_DATA__‎ ולא JSON API.

בקשות שנכשלות אינן מחויבות בקרדיט, ולכן retry הוא זול. עדיין שומרים על
תקרת קרדיטים לריצה (max_credits) כדי שתקלה לא תשרוף את המכסה החודשית.
"""
import logging
import threading
import time
import urllib.parse

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://api.scraperapi.com/"

# תשובות שמעידות על כשל זמני של ScraperAPI (לא מחויב, שווה לנסות שוב)
TRANSIENT_MARKERS = (
    "Request failed",
    "will not be charged",
)
# תשובות שמעידות על בעיית מסלול/מפתח — אין טעם לנסות שוב
FATAL_MARKERS = (
    "does not allow you to use our premium proxies",
    "API key",
    "exceeded the number of requests",
)


class CreditBudgetExceeded(Exception):
    """נגמרה מכסת הקרדיטים שהוקצתה לריצה הזו."""


class ScraperApiClient:
    """
    קליינט ScraperAPI בטוח-לתהליכונים.

    סופר קרדיטים (בקשה מוצלחת = קרדיט אחד במסלול הנוכחי) ועוצר כשחורגים
    מהתקרה שהוגדרה לריצה.
    """

    def __init__(self, api_key, country_code="il", timeout=90, retries=3,
                 max_credits=None, backoff=3.0, ultra_premium=False,
                 credit_cost=1):
        if not api_key:
            raise ValueError("חסר SCRAPERAPI_KEY")
        self.api_key = api_key
        self.country_code = country_code
        # ultra_premium=true עוקף את הגנת Radware של יד2 (30 קרדיטים לבקשה).
        # geotargeting (country_code=il) אינו זמין במסלול Hobby ומחזיר 403,
        # ולכן במצב ultra_premium לא שולחים country_code כלל.
        self.ultra_premium = bool(ultra_premium)
        self.credit_cost = int(credit_cost)
        self.timeout = timeout
        self.retries = retries
        self.max_credits = max_credits
        self.backoff = backoff

        self._local = threading.local()
        self._lock = threading.Lock()
        self.credits_used = 0
        self.requests_ok = 0
        self.requests_failed = 0
        self.fatal_error = None

    # ---- ניהול sessions לפי תהליכון (requests.Session אינו בטוח לשיתוף) ----
    @property
    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            self._local.session = s
        return s

    def _spend(self):
        """רושם קרדיט. זורק CreditBudgetExceeded אם עברנו את התקרה."""
        with self._lock:
            if self.max_credits is not None and self.credits_used >= self.max_credits:
                raise CreditBudgetExceeded(
                    f"נוצלו {self.credits_used} קרדיטים (תקרה {self.max_credits})")
            self.credits_used += self.credit_cost

    def budget_left(self):
        with self._lock:
            if self.max_credits is None:
                return None
            return max(0, self.max_credits - self.credits_used)

    def get(self, url, extra_params=None):
        """
        מביא URL דרך ScraperAPI. מחזיר את גוף התשובה (str) או None בכשל.
        לא זורק חריגות רשת — פרט ל-CreditBudgetExceeded שעוצר את השלב.
        """
        if self.fatal_error:
            return None

        params = {
            "api_key": self.api_key,
            "url": url,
        }
        if self.ultra_premium:
            # geotargeting חסום במסלול הזה — ultra_premium במקום country_code
            params["ultra_premium"] = "true"
        else:
            params["country_code"] = self.country_code
        if extra_params:
            params.update(extra_params)
        full = ENDPOINT + "?" + urllib.parse.urlencode(params, safe="")

        last = None
        for attempt in range(self.retries + 1):
            # התקרה נבדקת לפני כל ניסיון; בקשה שנכשלה אינה נספרת
            self._spend()
            try:
                resp = self._session.get(full, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
                self._refund()
                self._note_fail()
                log.debug("ScraperAPI: כשל רשת (%s) ניסיון %d — %s",
                          _short(url), attempt + 1, e)
                time.sleep(self.backoff * (attempt + 1))
                continue

            body = resp.text or ""
            head = body[:400]

            if any(m in head for m in FATAL_MARKERS):
                self._refund()
                self._note_fail()
                self.fatal_error = head.strip()[:200]
                log.error("ScraperAPI: שגיאה סופית — %s", self.fatal_error)
                return None

            if resp.status_code >= 400 or any(m in head for m in TRANSIENT_MARKERS):
                # ScraperAPI לא מחייב על בקשות כאלה
                self._refund()
                self._note_fail()
                last = f"HTTP {resp.status_code}: {head.strip()[:120]}"
                log.debug("ScraperAPI: כשל זמני (%s) ניסיון %d — %s",
                          _short(url), attempt + 1, last)
                time.sleep(self.backoff * (attempt + 1))
                continue

            with self._lock:
                self.requests_ok += 1
            return body

        log.warning("ScraperAPI: ויתור על %s אחרי %d ניסיונות (%s)",
                    _short(url), self.retries + 1, last)
        return None

    def _refund(self):
        with self._lock:
            self.credits_used = max(0, self.credits_used - self.credit_cost)

    def _note_fail(self):
        with self._lock:
            self.requests_failed += 1

    def summary_he(self):
        return (f"{self.requests_ok} בקשות מוצלחות, "
                f"{self.requests_failed} כשלים, ~{self.credits_used} קרדיטים")


def _short(url, n=90):
    return url if len(url) <= n else url[:n] + "…"


def account_info(api_key, timeout=30):
    """מצב החשבון (קרדיטים שנותרו). מחזיר dict או None."""
    try:
        r = requests.get("https://api.scraperapi.com/account",
                         params={"api_key": api_key}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        log.warning("ScraperAPI account: HTTP %s", r.status_code)
    except (requests.RequestException, ValueError) as e:
        log.warning("ScraperAPI account: %s", e)
    return None
