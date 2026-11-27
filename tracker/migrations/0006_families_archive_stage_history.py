import re

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def normalize_phone(phone):
    return re.sub(r"[\s\-()\[\]{}]", "", phone or "")


def link_families_and_seed_history(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Enquiry = apps.get_model("tracker", "Enquiry")
    Family = apps.get_model("tracker", "Family")
    StageChange = apps.get_model("tracker", "StageChange")

    for enquiry in Enquiry.objects.using(db_alias).order_by("pk").iterator():
        normalized = normalize_phone(enquiry.parent_phone)
        family = None
        if normalized:
            family = (
                Family.objects.using(db_alias)
                .filter(phone_normalized=normalized)
                .order_by("pk")
                .first()
            )

        if family is None:
            family = Family.objects.using(db_alias).create(
                parent_name=enquiry.parent_name,
                parent_phone=enquiry.parent_phone,
                phone_normalized=normalized,
                parent_email=enquiry.parent_email,
                country=enquiry.location,
            )
        else:
            updates = {}
            if not family.parent_email and enquiry.parent_email:
                updates["parent_email"] = enquiry.parent_email
            if not family.country and enquiry.location:
                updates["country"] = enquiry.location
            if updates:
                Family.objects.using(db_alias).filter(pk=family.pk).update(**updates)

        Enquiry.objects.using(db_alias).filter(pk=enquiry.pk).update(
            family_id=family.pk
        )
        stage_change = StageChange.objects.using(db_alias).create(
            enquiry_id=enquiry.pk,
            from_status="",
            to_status=enquiry.status,
        )
        StageChange.objects.using(db_alias).filter(pk=stage_change.pk).update(
            changed_at=enquiry.created
        )


def restore_parent_fields(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Enquiry = apps.get_model("tracker", "Enquiry")
    Family = apps.get_model("tracker", "Family")

    for enquiry in Enquiry.objects.using(db_alias).select_related("family").iterator():
        family = enquiry.family
        Enquiry.objects.using(db_alias).filter(pk=enquiry.pk).update(
            parent_name=family.parent_name,
            parent_phone=family.parent_phone,
            parent_email=family.parent_email,
            location=family.country,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0005_enquiry_stage_tracking"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Family",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("parent_name", models.CharField(max_length=150)),
                ("parent_phone", models.CharField(db_index=True, max_length=30)),
                (
                    "phone_normalized",
                    models.CharField(blank=True, db_index=True, max_length=30),
                ),
                ("parent_email", models.EmailField(blank=True, max_length=254)),
                ("country", models.CharField(blank=True, max_length=150)),
                ("created", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="enquiry",
            name="family",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="tracker.family",
            ),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="enquiry",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="StageChange",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("from_status", models.CharField(blank=True, max_length=20)),
                (
                    "to_status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("assessed", "Assessed"),
                            ("started", "Started"),
                            ("not_proceeding", "Not proceeding"),
                        ],
                        max_length=20,
                    ),
                ),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "enquiry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="history",
                        to="tracker.enquiry",
                    ),
                ),
            ],
            options={"ordering": ("changed_at", "pk")},
        ),
        migrations.AlterField(
            model_name="enquiry",
            name="parent_name",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AlterField(
            model_name="enquiry",
            name="parent_phone",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AlterField(
            model_name="enquiry",
            name="parent_email",
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.AlterField(
            model_name="enquiry",
            name="location",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.RunPython(link_families_and_seed_history, restore_parent_fields),
        migrations.AlterField(
            model_name="enquiry",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="tracker.family",
            ),
        ),
        migrations.RemoveField(model_name="enquiry", name="parent_name"),
        migrations.RemoveField(model_name="enquiry", name="parent_phone"),
        migrations.RemoveField(model_name="enquiry", name="parent_email"),
        migrations.RemoveField(model_name="enquiry", name="location"),
    ]
