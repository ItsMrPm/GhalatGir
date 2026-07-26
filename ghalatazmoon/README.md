# غلط‌آزمون — بک‌اند + دیتابیس (نسخه‌ی قابل‌اجرا)

## این چیه؟
تکمیل‌شده‌ی پروتوتایپ `prototype.html` طبق سندِ `architecture.md`: یک بک‌اند واقعی
و کامل (auth، ثبت غلط، تولید آزمون هوشمند، موتور SRS، آنالیتیکس) + دیتابیس،
و فرانت‌اندی که به‌جای داده‌ی هاردکد، از همین بک‌اند داده می‌گیرد.

## چرا Flask/SQLite به‌جای Django/DRF/PostgreSQL/Celery؟
سندِ معماری دقیقاً Django+DRF+PostgreSQL+Redis+Celery را پیشنهاد داده. محیطی که
من (Claude) این پروژه رو توش ساختم دسترسی به اینترنت نداره، پس نصب و اجرای
هیچ‌کدوم از این پکیج‌ها ممکن نبود — فقط چیزهایی که از قبل نصب بودن رو داشتم:
Python استاندارد (شامل `sqlite3`)، Flask، PyJWT، Werkzeug.

**تصمیم:** schema و منطق (الگوریتم تولید آزمون، امتیاز اولویت، زمان‌بندی مرور
فاصله‌دار) را دقیقاً طبق سند پیاده کردم، فقط framework عوض شد. جدول‌به‌جدول و
اندپوینت‌به‌اندپوینت با `architecture.md` قابل تطبیقه. کد رو همین‌جا اجرا و
تست کردم (register → login → ثبت غلط → تولید آزمون → پاسخ‌دهی → بروزرسانی SRS →
آنالیتیکس) و همه چیز کار می‌کنه.

اگر بعداً خواستی روی Django/PostgreSQL/Celery واقعی مهاجرت کنی، schema.sql و
منطق سرویس‌ها (`services/*.py`) تقریباً بدون تغییر قابل پورت هستن — فقط لایه‌ی
ORM و routing عوض می‌شه.

### چیزهایی که ساده‌سازی شدن (و چرا)
| بخش سند | در این نسخه |
|---|---|
| Auth با OTP پیامکی | phone_number + password (نیاز به گیت‌وی SMS واقعی داشت) |
| Celery Beat شبانه برای priority_score | همون تابع، ولی sync و بلافاصله بعد از هر تغییر اجرا می‌شه (در این مقیاس داده فرقی حس نمی‌شه) |
| Cloudinary/S3 برای عکس سؤال | فیلد `image_url` متنی (لینک) — آپلود واقعی فایل نداره |
| PostgreSQL | SQLite (فایل `backend/ghalatazmoon.db`) |
| Redis / Celery broker | حذف شده — کاری که این‌ها انجام می‌دادن (صف async) این‌جا sync اجرا می‌شه |

## اجرا
```bash
cd backend
pip install -r requirements.txt
python app.py
```
اولین اجرا، دیتابیس رو می‌سازه و برنامه‌ی درسی (شیمی/فیزیک/ریاضی/زیست پایه‌ی
دوازدهم تجربی) رو seed می‌کنه. بعد برو به: **http://127.0.0.1:5001**

از همون صفحه می‌تونی ثبت‌نام کنی، غلط ثبت کنی، آزمون بسازی و جواب بدی —
همه‌چیز روی دیتابیس واقعی ذخیره می‌شه.

## ساختار
```
backend/
  app.py              نقطه‌ی ورود Flask، ثبت route‌ها، سرو کردن فرانت‌اند
  schema.sql           DDL کامل (پورت‌شده از بخش ۳ سند)
  db.py                اتصال sqlite + init
  seed_data.py         برنامه‌ی درسی نمونه
  auth.py              هش پسورد + صدور/اعتبارسنجی JWT
  utils.py             pagination و کمکی‌ها
  services/
    priority.py         فرمول priority_score (بخش ۵ سند)
    srs.py               زمان‌بندی مرور فاصله‌دار (بخش ۶ سند)
    exam_generator.py    الگوریتم تولید آزمون هوشمند (بخش ۵ سند)
    weakness.py          تشخیص الگوی ضعف تکراری (بخش ۷ سند)
  routes/
    auth_routes.py، curriculum.py، mistakes.py، exams.py، analytics.py
frontend/
  index.html            SPA (تک‌فایل) — همون طراحی پروتوتایپ + login + بانک غلط‌ها
                         + آنالیتیکس + جریان کامل ساخت/اجرای آزمون، متصل به API بالا
```

## اندپوینت‌های اصلی (`/api/v1/...`)
همون فهرست بخش ۴ سند: `auth/register|login|refresh|me`،
`lessons/`, `lessons/{id}/chapters/`, `chapters/{id}/topics/`,
`mistakes/` (CRUD + `due-today/` + `stats-summary/`),
`exams/generate|{id}|{id}/start|{id}/questions/{qid}/answer|.../flag|{id}/finish|{id}/result`,
`analytics/overview|by-lesson|by-chapter|trend|reason-breakdown|weak-topics`.

## چیزهایی که تست شدن (end-to-end، با curl)
ثبت‌نام → ورود → گرفتن لیست درس‌ها/فصل‌ها/مباحث → ثبت غلط (و محاسبه‌ی
priority_score اولیه) → `stats-summary` و `due-today` → تولید آزمون هوشمند →
شروع آزمون → پاسخ غلط به یک سؤال (که `repeat_count`، `priority_score` و
`next_review_date` رو طبق فرمول SRS بروزرسانی کرد) → پایان آزمون → آنالیتیکس
(overview، reason-breakdown، weak-topics). رمز اشتباه هم درست ۴۰۱ برمی‌گردونه.
