from django.db import migrations, models


def backfill_authors(apps, schema_editor):
    """Existing reviews take their author's public name and email from the user profile."""
    Review = apps.get_model("catalog", "Review")
    for review in Review.objects.select_related("user"):
        user = review.user
        review.author_name = (user.username or user.full_name or user.phone)[:80]
        review.author_email = user.email or f"{user.phone}@users.titan.local"
        review.save(update_fields=["author_name", "author_email"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_product_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="author_name",
            field=models.CharField(
                default="", help_text="Shown publicly with the review.", max_length=80, verbose_name="name"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="review",
            name="author_email",
            field=models.EmailField(
                default="", help_text="Private; never shown publicly.", max_length=254, verbose_name="email"
            ),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_authors, migrations.RunPython.noop),
    ]
