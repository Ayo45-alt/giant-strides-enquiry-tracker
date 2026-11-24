import re

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


def normalize_phone_number(phone):
    return re.sub(r"[\s\-()\[\]{}]", "", phone or "")


class Subject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.name


class Family(models.Model):
    parent_name = models.CharField(max_length=150)
    parent_phone = models.CharField(max_length=30, db_index=True)
    phone_normalized = models.CharField(max_length=30, blank=True, db_index=True)
    parent_email = models.EmailField(blank=True)
    country = models.CharField(max_length=150, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.phone_normalized = normalize_phone_number(self.parent_phone)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "parent_phone" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"phone_normalized"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.parent_name} ({self.parent_phone})"


class Enquiry(models.Model):
    STATUS = [
        ("new", "New"),
        ("assessment_booked", "Assessment booked"),
        ("assessed", "Assessed"),
        ("started", "Started"),
        ("not_proceeding", "Not proceeding"),
    ]
    family = models.ForeignKey(
        Family,
        on_delete=models.PROTECT,
        related_name="children",
    )
    child_name = models.CharField(max_length=150)
    submission_ref = models.CharField(max_length=36, blank=True, db_index=True)
    subjects = models.ManyToManyField(Subject, blank=True)
    school_year = models.CharField(max_length=50)
    assessment_day = models.CharField(max_length=100, blank=True)
    proposed_classes = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=20, blank=True, db_index=True)
    preferred_assessment_date = models.DateField(null=True, blank=True)
    class_schedule = models.JSONField(default=dict, blank=True)
    time_zone = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="new")
    status_changed_at = models.DateTimeField()
    assessment_date = models.DateField(null=True, blank=True)
    assessment_time = models.TimeField(null=True, blank=True)
    tutor = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    not_proceeding_reason = models.CharField(max_length=200, blank=True)
    last_contacted = models.DateField(null=True, blank=True)
    next_followup = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Enquiries"

    def save(self, *args, **kwargs):
        changed_by = kwargs.pop("changed_by", None)
        update_fields = kwargs.get("update_fields")
        update_field_names = set(update_fields) if update_fields is not None else None
        is_new = self._state.adding
        previous_status = None

        if not is_new and (
            update_field_names is None or "status" in update_field_names
        ):
            previous_status = (
                type(self).objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        status_changed = is_new or (
            previous_status is not None and previous_status != self.status
        )
        if status_changed:
            self.status_changed_at = timezone.now()
            if update_field_names is not None:
                kwargs["update_fields"] = update_field_names | {"status_changed_at"}

        with transaction.atomic():
            super().save(*args, **kwargs)
            if not self.reference and self.pk:
                self.reference = f"GS-{self.pk:04d}"
                type(self).objects.filter(pk=self.pk).update(reference=self.reference)
            if status_changed:
                StageChange.objects.create(
                    enquiry=self,
                    from_status=previous_status or "",
                    to_status=self.status,
                    changed_by=changed_by,
                )

    def change_status(self, new_status, user=None):
        if new_status not in dict(self.STATUS):
            raise ValueError(f"Unknown enquiry status: {new_status}")

        with transaction.atomic():
            if self.pk:
                current_status = (
                    type(self).objects.select_for_update()
                    .filter(pk=self.pk)
                    .values_list("status", flat=True)
                    .first()
                )
                if current_status is None:
                    raise type(self).DoesNotExist
                if current_status == new_status:
                    self.status = current_status
                    return False

            self.status = new_status
            self.save(changed_by=user)
            return True

    def __str__(self):
        return f"{self.child_name} ({self.get_status_display()})"


class StageChange(models.Model):
    enquiry = models.ForeignKey(
        Enquiry,
        on_delete=models.CASCADE,
        related_name="history",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=Enquiry.STATUS)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ("changed_at", "pk")

    def __str__(self):
        return f"{self.enquiry}: {self.from_status or 'Created'} to {self.to_status}"
