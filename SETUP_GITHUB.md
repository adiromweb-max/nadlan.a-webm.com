# העלאת נדל״ן סקאוט ל‑GitHub Actions (בלי שרת משלך)

המנוע ירוץ כל בוקר בענן של GitHub (חינם), יסרוק, ייצר את הדשבורד,
וישמור אותו. אתה לא מתחזק שום שרת. זמן הקמה: ~10 דקות.

איך זה עובד בקצרה:
**GitHub Actions מריץ פייתון כל בוקר → מייצר `app.html` → מפרסם אותו.**
בשלב 1 מפרסמים ל‑GitHub Pages (כתובת חיה חינמית) כדי לראות שהלולאה עובדת.
בשלב 2 מפנים את זה לוורדפרס שלך.

---

## שלב 1 — יצירת ה‑repo והעלאת הקוד

1. היכנס ל‑github.com → New repository. תן שם, למשל `nadlan-scout`.
   בחר **Public** (כדי ש‑GitHub Pages יהיה חינמי).
2. העלה את כל תוכן החבילה הזו ל‑repo. הכי פשוט מהמחשב:
   ```bash
   cd nadlan-scout
   git init
   git add -A
   git commit -m "nadlan-scout initial"
   git branch -M main
   git remote add origin https://github.com/<המשתמש-שלך>/nadlan-scout.git
   git push -u origin main
   ```
   (או פשוט "Add file → Upload files" בממשק של GitHub וגרור את התיקייה.)

## שלב 2 — הוספת מפתח ScraperAPI כ‑Secret

ב‑repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `SCRAPERAPI_KEY`
- Secret: המפתח שלך (המחרוזת מ‑ScraperAPI)

זה נשמר מוצפן ולא נחשף בקוד.

## שלב 3 — הפעלת GitHub Pages

**Settings → Pages**:
- Source: **Deploy from a branch**
- Branch: **main**, Folder: **/docs**
- Save.

תוך דקה תקבל כתובת: `https://<המשתמש>.github.io/nadlan-scout/`

## שלב 4 — הרצה ראשונה (ידנית) לבדיקה

**Actions → "nadlan-scout daily scan" → Run workflow**.
זה יסרוק חי (כמה דקות), ייצר את הדשבורד, וישמור אותו ל‑`docs/index.html`.
בסיום — פתח את כתובת ה‑Pages ותראה את הדשבורד עם הנתונים הטריים.
מכאן זה ירוץ **אוטומטית כל בוקר** ב‑08:00 (שעון ישראל).

---

## שלב 5 — לחבר לוורדפרס (a‑webm.com)

שתי דרכים, בחר אחת:

### דרך א' — הטמעה (הכי מהיר, בלי סיסמאות)
צור עמוד חדש בוורדפרס, הוסף בלוק **HTML מותאם אישית** והדבק:
```html
<iframe src="https://<המשתמש>.github.io/nadlan-scout/"
        style="width:100%;height:100vh;border:0"></iframe>
```
המשתמש רואה את הדשבורד בתוך a‑webm.com. מתעדכן לבד כל יום.

### דרך ב' — דומיין משלך על ה‑Pages (נקי יותר)
מפנים תת‑דומיין, למשל `nadlan.a‑webm.com`, ל‑GitHub Pages:
1. אצל ספק ה‑DNS: רשומת **CNAME** מ‑`nadlan` אל `<המשתמש>.github.io`.
2. ב‑repo, קובץ `docs/CNAME` עם השורה: `nadlan.a-webm.com`.
3. Settings → Pages → Custom domain → `nadlan.a-webm.com` → Save (וסמן HTTPS).

### דרך ג' — דחיפה ישירה לוורדפרס (SFTP)
אם תרצה שהקובץ יֵשב פיזית בוורדפרס (ולא ב‑Pages), פתח את
`.github/workflows/scan.yml`, בטל את ההערה על שלב ה‑SFTP, והוסף ב‑Secrets:
`WP_HOST`, `WP_USER`, `WP_PASS`, `WP_PATH`. אז אפשר להפוך את ה‑repo לפרטי.

---

## נקודות חשובות

- **היסטוריית מחירים** נשמרת: ה‑workflow שומר את `data/nadlan.db` חזרה
  ל‑repo בכל ריצה, כך שירידות המחיר נצברות מיום ליום.
- **תקציב קרדיטים:** ריצה יומית = ~2,500 קרדיטים (עמוד לעיר, 32 ערים) ≈
  65k/חודש מתוך 100k. לשינוי — ערוך `yad2_max_pages_per_city` ב‑config.yaml.
- **תדירות:** לשינוי השעה/יום ערוך את שורת ה‑`cron` ב‑scan.yml
  (הזמן ב‑UTC; 05:00 UTC = 08:00 בישראל בקיץ).
- **פרטיות קוד:** ל‑Pages חינמי ה‑repo צריך להיות Public. אם חשוב שהקוד
  יהיה פרטי — לך על דרך ג' (SFTP) והפוך את ה‑repo לפרטי.
