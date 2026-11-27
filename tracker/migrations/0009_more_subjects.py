from django.db import migrations


def add_subjects(apps, schema_editor):
    Subject = apps.get_model("tracker", "Subject")
    alias = schema_editor.connection.alias
    for sort_order, name in enumerate(
        ("Literature", "Non-verbal Reasoning", "Verbal Reasoning"), start=5
    ):
        Subject.objects.using(alias).get_or_create(
            name=name,
            defaults={"is_active": True, "sort_order": sort_order},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0008_reference_schedule"),
    ]

    operations = [
        migrations.RunPython(add_subjects, migrations.RunPython.noop),
    ]
