import re

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone


COUNTRY_CODES = {
    "nigeria": "NG",
    "united kingdom": "UK",
    "uk": "UK",
    "great britain": "UK",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "canada": "CA",
    "ghana": "GH",
    "south africa": "ZA",
    "kenya": "KE",
    "ireland": "IE",
    "australia": "AU",
    "new zealand": "NZ",
    "india": "IN",
    "united arab emirates": "AE",
    "uae": "AE",
    "france": "FR",
    "germany": "DE",
    "italy": "IT",
    "spain": "ES",
    "netherlands": "NL",
    "saudi arabia": "SA",
    "qatar": "QA",
    "singapore": "SG",
    "malaysia": "MY",
    "china": "CN",
    "japan": "JP",
    "cameroon": "CM",
    "uganda": "UG",
    "tanzania": "TZ",
    "zambia": "ZM",
    "zimbabwe": "ZW",
    "rwanda": "RW",
    "ethiopia": "ET",
    "sierra leone": "SL",
    "liberia": "LR",
}


def get_country_code(country):
    """
    Convert the family's country value into a country code.
    """
    country = (country or "").strip()

    if not country:
        return "XX"

    normalized = country.casefold()

    if normalized in COUNTRY_CODES:
        return COUNTRY_CODES[normalized]

    # Accept an already-supplied two-letter country code.
    if re.fullmatch(r"[A-Za-z]{2}", country):
        return country.upper()

    raise ValueError(
        f"Country '{country}' has no configured country code. "
        "Add it to COUNTRY_CODES in tracker/models.py."
    )


def get_class_code(enquiry):
    """
    Convert school_year into a compact class code.

    Examples:
        Year 1     -> Y1
        Y2         -> Y2
        Primary 3  -> P3
        JSS 1      -> JSS1
        SS 2       -> SS2
        Grade 4    -> G4
    """
    class_value = (
        enquiry.school_year
        or enquiry.proposed_classes
        or ""
    ).strip().upper()

    if not class_value:
        raise ValueError(
            "Cannot generate an enrollment reference because "
            "the student's class is missing."
        )

    patterns = [
        (r"^(?:YEAR|Y)\s*(\d+)$", "Y"),
        (r"^(?:PRIMARY|PRI|P)\s*(\d+)$", "P"),
        (r"^(?:JSS|JHS)\s*(\d+)$", "JSS"),
        (r"^(?:SS|SSS|SHS)\s*(\d+)$", "SS"),
        (r"^(?:GRADE|GR)\s*(\d+)$", "G"),
        (r"^(?:NURSERY|N)\s*(\d+)$", "N"),
        (r"^(?:KG|KINDERGARTEN)\s*(\d+)$", "KG"),
    ]

    for pattern, prefix in patterns:
        match = re.fullmatch(pattern, class_value)

        if match:
            return f"{prefix}{match.group(1)}"

    # Accept already-standardized codes such as Y1 or P2.
    if re.fullmatch(r"[A-Z]{1,4}\d{1,2}", class_value):
        return class_value

    raise ValueError(
        f"Cannot convert class '{class_value}' into a reference code. "
        "Please standardize the school_year value."
    )


def get_next_monthly_enrollment_number(year, month):
    """
    Get the next portal-wide enrollment number for a given month.
    """
    with transaction.atomic():
        counter, created = MonthlyEnrollmentSequence.objects.get_or_create(
            year=year,
            month=month,
            defaults={"last_number": 0},
        )

        # Lock the counter row while allocating the next number.
        counter = (
            MonthlyEnrollmentSequence.objects
            .select_for_update()
            .get(pk=counter.pk)
        )

        counter.last_number += 1
        counter.save(update_fields=["last_number"])

        return counter.last_number

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

class MonthlyEnrollmentSequence(models.Model):
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"],
                name="unique_monthly_enrollment_sequence",
            )
        ]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.year}-{self.month:02d}: {self.last_number}"

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
        update_field_names = (
            set(update_fields) if update_fields is not None else None
        )

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
            previous_status is not None
            and previous_status != self.status
        )

        if status_changed:
            self.status_changed_at = timezone.now()

            if update_field_names is not None:
                kwargs["update_fields"] = (
                    update_field_names | {"status_changed_at"}
                )

        with transaction.atomic():
            # Save first so that a new enquiry receives its database ID.
            super().save(*args, **kwargs)

            # Generate a reference only when one does not already exist.
            if not self.reference:
                enrollment_date = timezone.localdate()

                country_code = get_country_code(self.family.country)
                class_code = get_class_code(self)

                sequence = get_next_monthly_enrollment_number(
                    year=enrollment_date.year,
                    month=enrollment_date.month,
                )

                self.reference = (
                    f"GS-{country_code}-{class_code}-"
                    f"{enrollment_date.strftime('%y%m')}"
                    f"{sequence:02d}"
                )

                # Update the reference without triggering save() again.
                type(self).objects.filter(pk=self.pk).update(
                    reference=self.reference
                )

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


class StudentMeeting(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ready", "Ready"),
    ]

    enquiry = models.OneToOneField(
        Enquiry,
        on_delete=models.CASCADE,
        related_name="meeting",
    )

    meet_url = models.URLField(
        max_length=500,
        unique=True,
        blank=True,
        null=True,
    )

    google_calendar_event_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Google Meet - {self.enquiry.child_name}"
