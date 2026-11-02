"""Seed the Epic Games password change guide (/guides/epic-games-change-password)."""

from django.db import migrations

EPIC_GAMES_CHANGE_PASSWORD_GUIDE = (
    "epic-games-change-password",
    "account",
    "fortnite",
    "تغییر پسوورد اپیک گیمز",
    "آموزش تصویری و گام‌به‌گام تغییر پسوورد (رمز عبور) اکانت اپیک گیمز در ۳ مرحله ساده از بخش Password and security برای حفظ امنیت اکانت فورتنایت.",
    """تغییر دوره‌ای پسوورد (رمز عبور) اکانت اپیک گیمز، به‌خصوص پس از دریافت سفارش‌های [فروشگاه فورتنایت تایتان](/store/fortnite) یا برای افزایش امنیت حساب، یکی از بهترین راه‌ها برای محافظت کامل از اسکین‌ها و وی‌باکس‌های شماست. در این آموزش تصویری، نحوه تغییر پسوورد اکانت در [سایت رسمی اپیک گیمز (Epic Games)](https://www.epicgames.com/) را در **۳ مرحله ساده** یاد می‌گیرید.

## مرحله اول: ورود به اکانت اپیک گیمز و باز کردن بخش Account

ابتدا وارد [سایت رسمی اپیک گیمز به آدرس epicgames.com](https://www.epicgames.com/) شوید و با ایمیل و رمز عبور فعلی خود وارد حساب کاربری شوید.

1. همان‌طور که در تصویر زیر با فلش قرمز مشخص شده است، در گوشه بالا سمت راست سایت روی **نام کاربری یا آیکون پروفایل خود** کلیک کنید.
2. از منوی بازشده، گزینه **Account** را انتخاب کنید تا وارد صفحه تنظیمات حساب شوید.

![مرحله اول تغییر پسوورد اپیک گیمز: ورود به اکانت در سایت Epic Games، کلیک روی پروفایل و انتخاب گزینه Account](/images/guides/epic-games-change-password/1.webp)

## مرحله دوم: ورود به بخش Password and security و کلیک روی Change password

پس از ورود به تنظیمات حساب، از منوی سمت چپ صفحه وارد قسمت **Password and security** شوید (یا مستقیماً صفحه [تنظیمات رمز عبور و امنیت اپیک گیمز](https://www.epicgames.com/account/password) را باز کنید) و باقی مراحل را طبق تصویر زیر پیش بروید:

1. از منوی سمت چپ وارد بخش **Password and security** شوید.
2. صفحه را به پایین اسکرول کنید تا به قسمت **Change your password** برسید.
3. طبق کادر قرمز در تصویر زیر، روی دکمه آبی‌رنگ **Change password** کلیک کنید.

![مرحله دوم تغییر پسوورد اپیک گیمز: ورود به بخش Password and security و کلیک روی دکمه Change password](/images/guides/epic-games-change-password/2.webp)

## مرحله سوم: وارد کردن رمز عبور فعلی و جدید و کلیک روی Confirm

با کلیک روی دکمه **Change password**، پنجره تغییر رمز عبور برای شما باز می‌شود. مطابق کادرهای راهنمای فارسی در تصویر زیر عمل کنید:

1. در کادر اول (**Current password**)، **رمز عبور فعلی خود** را وارد کنید.
2. در کادر دوم (**New password**)، **رمز عبور جدید خود** را وارد کنید (رمز جدید نباید یکی از ۵ رمز قبلی شما باشد، نباید فاصله یا Space داشته باشد و باید حداقل شامل ۱۰ کاراکتر، ۱ حرف انگلیسی و ۱ عدد باشد).
3. در پایان، روی دکمه آبی‌رنگ **Confirm** کلیک کنید تا پسوورد اکانت اپیک گیمز شما با موفقیت تغییر کند.

![مرحله سوم تغییر پسوورد اپیک گیمز: وارد کردن رمز عبور فعلی در Current password، رمز عبور جدید در New password و کلیک روی دکمه Confirm](/images/guides/epic-games-change-password/3.webp)

## نکات مهم پس از تغییر پسوورد اپیک گیمز

- **به‌روزرسانی رمز در پنل تایتان:** اگر اطلاعات اکانت فورتنایت خود را برای خریدهای سریع در [بخش اکانت‌های من در پنل تایتان](/dashboard/accounts) ذخیره کرده‌اید، پس از تغییر پسوورد، رمز جدید را در پنل تایتان نیز ویرایش کنید تا در سفارش‌های بعدی [فروشگاه فورتنایت](/store/fortnite) تاخیری ایجاد نشود.
- **قوانین رمز عبور جدید:** رمز عبور جدید باید حداقل ۱۰ کاراکتر بوده و شامل حداقل یک حرف و یک عدد بدون فاصله (Space) باشد.""",
    [
        (
            "تغییر پسوورد اکانت اپیک گیمز از کدام بخش انجام می‌شود؟",
            "وارد سایت epicgames.com شوید، از منوی پروفایل بالا سمت راست گزینه Account را بزنید، سپس از منوی تنظیمات وارد بخش Password and security شوید و در قسمت Change your password روی دکمه Change password کلیک کنید.",
        ),
        (
            "رمز عبور جدید اپیک گیمز (New password) باید چه شرایطی داشته باشد؟",
            "رمز عبور جدید باید حداقل ۱۰ کاراکتر باشد، حداقل شامل ۱ حرف انگلیسی و ۱ عدد باشد، هیچ فاصله‌ای (Space) نداشته باشد و تکراری (جزو ۵ رمز عبور قبلی شما) نباشد.",
        ),
        (
            "آیا بعد از تغییر پسوورد باید اطلاعات اکانت را در سایت تایتان هم ویرایش کنم؟",
            "بله، اگر قصد ثبت سفارش جدید برای شارژ وی‌باکس یا کروپک دارید، وارد بخش «اکانت‌های من» در داشبورد تایتان شوید و رمز عبور جدید اکانت اپیک گیمز خود را جایگزین کنید.",
        ),
    ],
)


def create_epic_games_change_password_guide(apps, schema_editor):
    Guide = apps.get_model("content", "Guide")
    GuideFaq = apps.get_model("content", "GuideFaq")
    Game = apps.get_model("catalog", "Game")

    slug, category, game_slug, title, summary, body, faqs = EPIC_GAMES_CHANGE_PASSWORD_GUIDE
    game = Game.objects.filter(slug=game_slug).first() if game_slug else None

    guide, _ = Guide.objects.update_or_create(
        slug=slug,
        defaults={
            "title": title,
            "category": category,
            "game": game,
            "summary": summary,
            "body": body,
            "meta_title": "آموزش تصویری تغییر پسوورد اپیک گیمز (Epic Games) | تایتان",
            "meta_description": "آموزش گام‌به‌گام و تصویری تغییر پسوورد (رمز عبور) اکانت اپیک گیمز برای فورتنایت در ۳ مرحله ساده از طریق بخش Password and security.",
            "is_published": True,
            "sort_order": 13,
        },
    )
    guide.faqs.all().delete()
    GuideFaq.objects.bulk_create(
        GuideFaq(guide=guide, question=question, answer=answer, sort_order=index)
        for index, (question, answer) in enumerate(faqs)
    )


def remove_epic_games_change_password_guide(apps, schema_editor):
    Guide = apps.get_model("content", "Guide")
    Guide.objects.filter(slug="epic-games-change-password").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0009_epic_games_change_region_guide"),
    ]

    operations = [
        migrations.RunPython(
            create_epic_games_change_password_guide,
            remove_epic_games_change_password_guide,
        )
    ]
