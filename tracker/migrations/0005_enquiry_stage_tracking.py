from django.db import migrations, models
from django.utils import timezone


def assessment_booked_to_new(apps, schema_editor):
    Enquiry = apps.get_model("tracker", "Enquiry")
    Enquiry.objects.using(schema_editor.connection.alias).filter(
        status="assessment_booked"
    ).update(status="new")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0004_seed_default_subjects"),
    ]

    operations = [
        migrations.RunPython(
            assessment_booked_to_new,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="enquiry",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("assessed", "Assessed"),
                    ("started", "Started"),
                    ("not_proceeding", "Not proceeding"),
                ],
                default="new",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="status_changed_at",
            field=models.DateTimeField(default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="enquiry",
            name="assessment_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="not_proceeding_reason",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
