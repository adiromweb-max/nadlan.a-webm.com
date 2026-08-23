# נדל"ן סקאוט — התקנה על השרת (מדריך קצר)

מערכת שסורקת עסקאות נדל"ן בדרום, מדרגת הזדמנויות, בונה אקסל ושולחת התראת מייל.
ההרצה החוזרת היא סקריפט פייתון ב-cron — זול, אמין, לא נתקע.

## מה שאתה צריך להכין פעם אחת

### 1. סיסמת אפליקציה של Gmail (App Password)
כדי שהשרת ישלח מיילים מהחשבון שלך:

1. היכנס לחשבון Google, אבטחה, ודא שיש אימות דו-שלבי מופעל.
2. עבור לכתובת: https://myaccount.google.com/apppasswords
3. צור סיסמת אפליקציה חדשה (שם: "nadlan server"). תקבל 16 תווים.
4. העתק אותה (זו לא סיסמת הגוגל הרגילה — זו סיסמה ייעודית לשרת).

### 2. הוסף את הפרטים לקובץ הסודות בשרת
בשרת, ערוך את קובץ הסודות והוסף שלוש שורות (אותה שיטה נקייה כמו קודם):

    read -r GP
    (הדבק את סיסמת האפליקציה, Enter)
    printf 'export GMAIL_ADDRESS="adiromweb@gmail.com"\nexport GMAIL_APP_PASSWORD="%s"\nexport ALERT_TO="adiromweb@gmail.com"\n' "$GP" >> ~/.config/menutakim/secrets.env
    source ~/.bashrc

## התקנה ובנייה

1. העלה את התיקייה nadlan-scout לשרת (scp, כמו שהעלינו פרויקטים קודם).
2. התחבר לשרת, היכנס לתיקייה, והפעל את האג'נט שיבנה:

       cd ~/nadlan-scout
       cp config.example.yaml config.yaml
       claude

   כשמופיע קלט, הדבק/הקלד:

       Follow CLAUDE.md and build the system end to end. Test it live against nadlan.gov.il and yad2, produce the Excel, and send one test email to adiromweb@gmail.com. Then set up a daily cron job. Work autonomously.

3. אשר פקודות (בחר "כן, אל תשאל שוב" כדי לחסוך שאלות). האג'נט יבנה, יבדוק,
   ישלח מייל בדיקה, ויתזמן. ודא שיש יתרה בחשבון Anthropic לבנייה.

## אחרי הבנייה
- תקבל מייל בדיקה עם אקסל מצורף (5 גיליונות).
- בשרת נוצרים גם `out/latest.json` (כל השדות + חציונים שנתיים לכל אזור)
  ו-`out/charts/*.png` (מגמת אזור ל-5 שנים, והתפלגות הקומפים לכל עסקה מוכתרת).
- המייל מכתיר **רק** מודעות בדירוג "לבדוק דחוף" שלא דווחו קודם; מודעה
  שדווחה כבר נספרת כ"דווחו כבר: N" ולא נשלחת שוב.
- מכאן, cron יריץ יומית לבד. אחרי שבוע, נעביר לשבועי (שינוי שורה אחת ב-crontab).
- לשנות ערים/סף מחיר/סף התראה — ערוך את config.yaml.

## הרצה ידנית מתי שתרצה
    cd ~/nadlan-scout && ./run.sh
