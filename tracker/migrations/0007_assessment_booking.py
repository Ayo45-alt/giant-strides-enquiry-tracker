from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0006_families_archive_stage_history"),
    ]

    operations = [
        migrations.AlterField(
            model_name="enquiry",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("assessment_booked", "Assessment booked"),
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
            name="assessment_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="tutor",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AlterField(
            model_name="stagechange",
            name="to_status",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("assessment_booked", "Assessment booked"),
                    ("assessed", "Assessed"),
                    ("started", "Started"),
                    ("not_proceeding", "Not proceeding"),
                ],
                max_length=20,
            ),
        ),
    ]
