#!/usr/bin/env bash
# נדל"ן סקאוט — wrapper להרצה (ידנית או מ-cron).
# מפעיל את ה-venv, טוען סודות, ומפנה פלט ללוג יומי.
set -uo pipefail

cd "$(dirname "$0")" || exit 1

LOG_DIR="logs"
mkdir -p "$LOG_DIR" data out
LOG_FILE="$LOG_DIR/run-$(date +%Y%m%d).log"

# טעינת סודות (GMAIL_ADDRESS / GMAIL_APP_PASSWORD / ALERT_TO).
# ב-cron אין ~/.bashrc, לכן טוענים במפורש.
SECRETS="$HOME/.config/menutakim/secrets.env"
if [ -f "$SECRETS" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$SECRETS"
    set +a
fi

PY="./venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "$(date '+%F %T') שגיאה: לא נמצא venv ב-./venv — הרץ: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" \
        | tee -a "$LOG_FILE"
    exit 1
fi

echo "$(date '+%F %T') === הרצת נדל\"ן סקאוט ===" >> "$LOG_FILE"
"$PY" -m scout.main "$@" >> "$LOG_FILE" 2>&1
STATUS=$?
echo "$(date '+%F %T') === סיום, קוד יציאה $STATUS ===" >> "$LOG_FILE"

# ניקוי לוגים מעל 60 יום כדי שהתיקייה לא תתפח
find "$LOG_DIR" -name 'run-*.log' -type f -mtime +60 -delete 2>/dev/null

exit $STATUS
