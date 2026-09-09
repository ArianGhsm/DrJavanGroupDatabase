# DrJavanBot — Dental Intelligence Assistant با دسترسی ویژه به آرشیو گروه دکتر مهدی جوان

> این پروژه توسط **آریان قاسم‌پور** ساخته شده است.

این ریپازیتوری هم آرشیو Telegram HTML Export گروه دکتر مهدی جوان را نگه می‌دارد و هم **Dental Intelligence Assistant** چندمنبعی را اجرا می‌کند. آرشیو برای سؤال «گروه چه گفته؟» منبع privileged است؛ facts علمی از Scientific/Official evidence و اطلاعات زمان‌حساس از Current/Official evidence می‌آیند. DeepSeek/AvalAI می‌تواند سؤال، routing و synthesis را انجام دهد، اما **حافظه عمومی مدل factual authority نیست** و هیچ خلأ شواهدی را حق ندارد با حدس پر کند.

> **حفظ سیاست قبلی:** نسخه کامل و بدون تغییر README/راهنمای تحلیل پیش از تبدیل پروژه به ربات، عیناً در [`docs/original-analysis-policy.md`](docs/original-analysis-policy.md) نگه‌داری شده است. نسخه فشرده اجرایی در [`docs/answer-policy.md`](docs/answer-policy.md) قرار دارد. بنابراین هیچ‌یک از قواعد تحلیلی قبلی حذف نشده‌اند.

## وضعیت داده و معماری

در ممیزی مرحله ۱، **۲۴۷ فایل `messages*.html`** پیوسته شناسایی شد: `messages.html` و `messages2.html` تا `messages247.html`.

معماری هدف:

```text
Question + bounded conversation context
  → Question Intelligence (intent/domain/entity/facet/freshness)
  → Source Router (Archive / Scientific / Current / Official)
  → source-specific Query Generation
  → parallel required-source Retrieval
  → Evidence Fusion + RequestedFactCoverage
  → Compact Grounded Synthesis (support IDs only)
  → Application-owned verbatim support + Multi-Source Claim Validation
  → route-aware Telegram Answer + Citations
```

مسیر داده:

```text
Telegram HTML Export
  → Parser
  → Normalizer
  → Structured Message Store
  → SQLite FTS5
  → Full / Incremental Reindex
```

هدف طراحی، **۰ AI call برای فهم سؤال‌های ساده و مسیرهای archive ساده و معمولاً ۱ call برای synthesis علمی/current/hybrid مستند** است. فقط ambiguity/complexity یا حداکثر یک repair محدود می‌تواند call اضافه ایجاد کند. retrievalهای مستقل چندمنبعی parallel می‌شوند و هیچ raw archive کامل یا full copyrighted article برای هر سؤال به مدل ارسال نمی‌شود.

جزئیات تصمیم‌های معماری در [`docs/architecture/ADR-001-core-architecture.md`](docs/architecture/ADR-001-core-architecture.md) و ممیزی داده در [`docs/data-audit.md`](docs/data-audit.md) ثبت شده است.

## امنیت و secrets

هیچ Bot Token، AvalAI/DeepSeek API Key یا credential سرور نباید داخل Git commit شود.

`.env.example` فقط متغیرهای غیرحساس/placeholder را تعریف می‌کند. مالک ربات با **Telegram numeric user ID** (`TELEGRAM_OWNER_ID`) احراز می‌شود، نه username. AvalAI API Key بعد از بالا آمدن ربات توسط مالک و فقط در private chat از `/settings` تنظیم می‌شود؛ پیام کلید best-effort حذف، کلید قبل از ذخیره validate و سپس خارج از Git در `runtime/secrets/` با دسترسی محدود نگه‌داری می‌شود.

## راه‌اندازی در سطح کلی

جریان production پس از Stage 5/6:

1. dependencyها نصب شوند.
2. `.env` خارج از Git فقط با `TELEGRAM_BOT_TOKEN`، `TELEGRAM_OWNER_ID` و تنظیمات غیرحساس ساخته شود.
3. archive با `python -m drjavanbot index` یا `reindex` آماده شود.
4. health/integrity بررسی شود.
5. process ربات با `python -m drjavanbot.telegram` یا `drjavanbot-bot` اجرا شود.
6. مالک در private chat از `/settings` کلید AvalAI را تنظیم و تست کند؛ restart لازم نیست.

جزئیات Telegram/Owner در [`docs/telegram-bot.md`](docs/telegram-bot.md) ثبت شده است. **API Key AvalAI به Codex داده نمی‌شود و در `.env` قرار نمی‌گیرد.**

## پنل مالک و دسترسی

Stage 4 این قابلیت‌ها را پیاده‌سازی کرده است:

- `/settings`, `/health`, `/stats`, `/reindex` فقط برای مالک و private chat؛
- تنظیم/تعویض/حذف/تست AvalAI API Key؛
- انتخاب مدل فقط از allowlist `deepseek-v4-flash` / `deepseek-v4-pro`؛
- آمار سؤال‌ها، cache، AI token/cost، index و آخرین reindex؛
- `owner_only` (پیش‌فرض)، `allowlist` و `public`؛
- `/allow NUMERIC_ID` و `/deny NUMERIC_ID`؛
- rate limit per-user، duplicate-update protection، long-message chunking؛
- منابع paginated و user-bound بدون ارسال raw archive؛
- reindex قفل‌دار با حفظ last-known-good index و cache invalidation پس از موفقیت.

---

# سیاست اجرایی منابع و پاسخ

این بخش قواعد archive را در معماری جدید خلاصه می‌کند. contract نهایی چندمنبعی در [`docs/intelligence-v2/SOURCE_POLICY.md`](docs/intelligence-v2/SOURCE_POLICY.md) و [`docs/intelligence-v2/ANSWER_POLICY.md`](docs/intelligence-v2/ANSWER_POLICY.md) مرجع اجرایی است؛ [`docs/original-analysis-policy.md`](docs/original-analysis-policy.md) فقط سیاست تاریخی تحلیل آرشیو را حفظ می‌کند.

## 1. اصل بنیادین

Source authority تابع intent است. سؤال صریح درباره گروه باید پس از جست‌وجوی همه بخش‌های مرتبط آرشیو پاسخ داده شود؛ سؤال علمی factual باید scientific evidence داشته باشد و سؤال current باید evidence تاریخ‌دار/current داشته باشد. مدل به‌تنهایی منبع factual نیست.

هر پرسش درباره محتوای این آرشیو باید پس از جست‌وجوی همه بخش‌های مرتبط پاسخ داده شود. دیدگاه‌های موافق، مخالف، خنثی، تجربی و اصلاحی باید دیده شوند و محدودیت و میزان اطمینان پاسخ روشن باشد. اگر حجم داده زیاد است، نمونه‌گیری سطحی کافی نیست؛ باید از جست‌وجوی چندمرحله‌ای، کلیدواژه‌ها، مترادف‌ها، شکل‌های فارسی/انگلیسی/فینگلیش، غلط‌های املایی محتمل، نام افراد/برندها و زمینه زمانی استفاده شود.

## 2. قانون جست‌وجوی کامل

برای هر سؤال، چند query/variant ساخته و کل index جست‌وجو شود؛ replyها، نقل‌قول‌ها، ادامه بحث، پیام‌های مشابه، متناقض و اصلاحی نیز بررسی شوند. اولین نتیجه یا پرتکرارترین جمله به‌تنهایی ملاک نتیجه نیست.

## 3. پوشش و زمینه

حتی پیام کم‌تعداد اما دقیق ممکن است نتیجه را تغییر دهد. پیام بدون زمینه باید با reply target و پیام‌های قبل/بعد خوانده شود. اگر بخشی از archive قابل بررسی نبود، ربات باید آن محدودیت را صریح اعلام کند.

## 4. ارزیابی ادعاها

برای ادعاهای مهم بررسی شود آیا توسط افراد مستقل تأیید یا رد شده‌اند، آیا بعداً اصلاح شده‌اند، و آیا حاصل تجربه، توضیح فنی، شنیده، تبلیغ، شوخی یا عصبانیت‌اند. این ارزیابی کیفیت evidence است، نه اثبات علمی ادعا.

## 5. ارزیابی نویسندگان بدون تشخیص شخصیتی

فقط برای سنجش کیفیت یک ادعا می‌توان الگوی مشارکت همان نویسنده در موضوع مشابه را بررسی کرد. تشخیص روان‌شناختی، برچسب شخصیتی، قضاوت اخلاقی یا نسبت‌هایی مانند «دروغگو» ممنوع است.

## 6. سوگیری و تعارض منافع

احتمال تبلیغ، رقابت، تجربه شخصی محدود، تعمیم بیش از حد، فضای احساسی، داده قدیمی یا منفعت مستقیم نویسنده بررسی شود و فقط با شواهد و زبان محتاطانه گزارش شود.

## 7. فرایند پاسخ

فهم سؤال → intent/facet/freshness → انتخاب source → query مخصوص هر source → retrieval → fusion/rerank → requested-fact coverage → grounded synthesis → claim validation → پاسخ source-aware. در مسیر Archive، context/reply expansion و discussion graph حفظ می‌شود.

## 8. قالب پاسخ adaptive

سؤال ساده باید جواب کوتاه بگیرد. سؤال تحلیلی، در صورت نیاز، می‌تواند شامل جمع‌بندی کوتاه، شواهد اصلی، دیدگاه‌های مخالف/متفاوت، ارزیابی اعتبار و سوگیری، نتیجه عملی، confidence، محدودیت‌ها و منابع باشد. تیتر خالی یا بخش بی‌فایده صرفاً برای قالب‌سازی ایجاد نشود.

## 9. ارجاع‌دهی

ادعاهای مهم باید تا حد ممکن به message id، source file/locator، تاریخ و نویسنده قابل ردیابی باشند. در صورت نیاز حریم خصوصی، شناسه ناشناس جای نام واقعی استفاده شود.

## 10. حریم خصوصی و انصاف

اطلاعات شخصی غیرضروری، شماره تماس، آدرس، اطلاعات بیمار و داده حساس بازنشر نشود. تحلیل کیفیت پیام برای سنجش ادعاست نه قضاوت شخص. داده سلامت با احتیاط مضاعف پردازش شود.

## 11. محدودیت پزشکی

این archive منبع گفت‌وگویی/تجربی است، نه guideline. چیزی که در گروه زیاد گفته شده نباید به‌عنوان حقیقت علمی قطعی نمایش داده شود. تصمیم‌های درمانی، دارویی و تشخیصی باید از تجربه گروه و منابع علمی/قضاوت بالینی جدا نگه داشته شوند.

## 12. داده بزرگ

جست‌وجو باید مرحله‌ای و index-based باشد؛ نتایج تکراری collapse و پیام‌های مهم با context خوانده شوند. محدودیت context مدل مجوز تحلیل حدسی یک زیرمجموعه تصادفی نیست.

## 13. چک‌لیست پیش از پاسخ

قبل از پاسخ نهایی بررسی شود که query variants کافی استفاده شده‌اند؛ context/reply پیام‌های مهم دیده شده؛ اختلاف‌ها و اصلاحات جست‌وجو شده؛ احتمال سوگیری و duplicate بررسی شده؛ confidence و محدودیت‌ها قابل دفاع‌اند؛ و هیچ برچسب شخصیتی/روانی تولید نشده است.

## 14. دستور اجرایی مدل

مدل فقط evidence بازیابی‌شده از sourceهای route‌شده را تحلیل می‌کند. اگر evidence لازم کافی نیست، سیستم باید ناکافی بودن شواهد را اعلام کند و حق ندارد پاسخ textbook، عدد بازار یا guideline را از حافظه خودش جایگزین کند.

## 15. شواهد ناکافی

عبارت‌هایی مانند «در پیام‌های بررسی‌شده شواهد کافی برای نتیجه قطعی پیدا نشد» پاسخ معتبر محسوب می‌شوند. کمبود evidence نباید با hallucination پوشانده شود.

## 16. اولویت‌بندی evidence

وزن بیشتر معمولاً برای تجربه عملی دقیق/توضیح فنی، تأیید چند فرد مستقل، ادعایی که بعداً رد نشده، اصلاحات جدیدتر و مشارکت قابل‌اتکاتر در همان حوزه است. پیام منفرد، احساسی، تبلیغاتی یا بدون پشتوانه وزن کمتری دارد. Frequency تنها یک signal است.

## 17. سؤال درباره افراد

می‌توان گفت ادعای یک فرد در archive چند بار تأیید یا نقد شده یا با پیام‌های بعدی ناسازگار بوده است. نمی‌توان از این داده‌ها درباره شخصیت واقعی، سلامت روان یا قابل‌اعتمادبودن ذاتی فرد حکم داد.

## 18. جمع‌بندی سیاست

خروجی ربات باید **مستند، چندجانبه، قابل ردیابی و محدود به evidence مجاز route** باشد. Archive opinion، Scientific evidence، Guideline/Official و Current market info با label جدا نمایش داده می‌شوند. Confidence فقط `High / Medium / Low` با دلیل واقعی است؛ درصد ساختگی ممنوع است.
