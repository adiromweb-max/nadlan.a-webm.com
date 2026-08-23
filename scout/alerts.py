"""
דדופ התראות — מה נשלח במייל ומה כבר דווח.

הבעיה: מודעה טובה נשארת טובה. בלי דדופ, אותה דירה מופיעה בכל מייל יומי עד
שהיא יורדת מהאוויר, והמייל מאבד את מהות ה"חדש".

הכלל: מודעה נשלחת אם — ורק אם — היא **חצתה את הסף בפעם הראשונה**, או
שמאז ההתראה הקודמת עליה **הציון עלה ב-alert_score_rise נקודות ומעלה**, או
ש**המחיר ירד**. כל השאר נספרות כ"דווחו כבר" ומוצגות כמונה במייל.

המצב נשמר ב-listings: first_alerted_at, last_alerted_score, last_alerted_price.
"""
import logging

log = logging.getLogger(__name__)

DEFAULT_SCORE_RISE = 10.0


def decide(candidates, state, score_rise=DEFAULT_SCORE_RISE):
    """
    candidates — מודעות שעברו את סף ההתראה בריצה הזו (כבר מסוננות).
    state      — db.alert_state(): {id: {first_alerted_at, last_alerted_score, ...}}

    מחזיר (to_send, suppressed, stats). כל מודעה מקבלת גם שדות:
      alert_status  — 'new' / 'score_up' / 'price_drop' / 'repeat'
      alert_reason  — הסבר קצר בעברית
    """
    to_send, suppressed = [], []
    stats = {"new": 0, "score_up": 0, "price_drop": 0, "repeat": 0}

    for c in candidates:
        prev = (state or {}).get(c.get("id")) or {}
        first_at = prev.get("first_alerted_at")
        prev_score = prev.get("last_alerted_score")
        prev_price = prev.get("last_alerted_price")

        score = c.get("score")
        price = c.get("price")

        if not first_at:
            status, reason = "new", "חצתה את הסף לראשונה"
        else:
            rise = None
            if score is not None and prev_score is not None:
                rise = float(score) - float(prev_score)
            dropped = (price is not None and prev_price is not None
                       and float(price) < float(prev_price))

            if rise is not None and rise >= float(score_rise):
                status = "score_up"
                reason = f"הציון עלה ב-{rise:.0f} נקודות מאז הדיווח הקודם"
            elif dropped:
                delta = float(prev_price) - float(price)
                status = "price_drop"
                reason = f"המחיר ירד ב-{delta:,.0f} ₪ מאז הדיווח הקודם"
            else:
                status = "repeat"
                reason = f"דווחה כבר ב-{first_at}"

        c["alert_status"] = status
        c["alert_reason"] = reason
        c["first_alerted_at"] = first_at
        c["last_alerted_score"] = prev_score
        stats[status] += 1
        (suppressed if status == "repeat" else to_send).append(c)

    log.info("דדופ התראות: %d חדשות, %d עם ציון שעלה, %d עם ירידת מחיר, "
             "%d דווחו כבר (מדוכאות)",
             stats["new"], stats["score_up"], stats["price_drop"], stats["repeat"])
    return to_send, suppressed, stats


def summary_he(stats):
    """שורת סיכום לדו"ח ולמייל."""
    if not stats:
        return "לא רץ"
    sent = stats.get("new", 0) + stats.get("score_up", 0) + stats.get("price_drop", 0)
    return (f"{sent} נשלחו ({stats.get('new', 0)} חדשות, "
            f"{stats.get('score_up', 0)} ציון עלה, "
            f"{stats.get('price_drop', 0)} ירידת מחיר), "
            f"{stats.get('repeat', 0)} דווחו כבר")
