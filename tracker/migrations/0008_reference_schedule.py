from django.db import migrations, models


def backfill_references(apps, schema_editor):
    Enquiry = apps.get_model("tracker", "Enquiry")
    for enquiry in Enquiry.objects.filter(reference=""):
        enquiry.reference = f"GS-{enquiry.pk:04d}"
        enquiry.save(update_fields=["reference"])


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0007_assessment_booking"),
    ]

    operations = [
        migrations.AddField(
            model_name="enquiry",
            name="reference",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="preferred_assessment_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="class_schedule",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="time_zone",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.RunPython(backfill_references, migrations.RunPython.noop),
    ]
