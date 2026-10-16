from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    try:
        call_command("createcachetable", "django_cache_table")
    except Exception:
        pass


def remove_cache_table(apps, schema_editor):
    table_name = "django_cache_table"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(create_cache_table, reverse_code=remove_cache_table),
    ]
