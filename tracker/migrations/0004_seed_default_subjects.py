from django.db import migrations


def seed_subjects(apps, schema_editor):
    Subject = apps.get_model("tracker", "Subject")
    subjects = ("English", "Maths", "Sciences", "GCSE", "Coding")
    for sort_order, name in enumerate(subjects):
        Subject.objects.using(schema_editor.connection.alias).get_or_create(
            name=name,
            defaults={"is_active": True, "sort_order": sort_order},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0003_subject_enquiry_subjects"),
    ]

    operations = [
        migrations.RunPython(seed_subjects, migrations.RunPython.noop),
    ]
