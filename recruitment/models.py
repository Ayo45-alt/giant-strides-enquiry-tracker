
# recruitment/models.py

from django.db import models
from django.utils import timezone

from tracker.models import Enquiry, Subject


class JobAd(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending Dispatch"),
        ("sent", "Successfully Dispatched"),
        ("reversed", "Dispatch Reversed"),
    ]

    enquiry = models.OneToOneField(
        Enquiry,
        on_delete=models.CASCADE,
        related_name="job_ad",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # Time of the last successful dispatch.
    last_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reminder_sent = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    reversed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reversal_reason = models.TextField(blank=True)

    @property
    def ref_code(self):
        return self.enquiry.reference or f"GS-{self.enquiry.pk:04d}"

    def get_unassigned_subjects(self):
        approved_subject_ids = self.applications.filter(
            is_approved=True
        ).values_list("subject_id", flat=True)

        return self.enquiry.subjects.exclude(
            id__in=approved_subject_ids
        )

    @property
    def is_active(self):
        return self.status == "sent"

    def mark_as_sent(self):
        self.status = "sent"
        self.last_sent_at = timezone.now()
        self.reversed_at = None
        self.reversal_reason = ""

        self.save(
            update_fields=[
                "status",
                "last_sent_at",
                "reversed_at",
                "reversal_reason",
            ]
        )

    def mark_as_reversed(self, reason):
        self.status = "reversed"
        self.reversed_at = timezone.now()
        self.reversal_reason = reason

        self.save(
            update_fields=[
                "status",
                "reversed_at",
                "reversal_reason",
            ]
        )

    def __str__(self):
        return f"{self.ref_code} - {self.enquiry.child_name}"


class TutorApplication(models.Model):
    job_ad = models.ForeignKey(
        JobAd,
        on_delete=models.CASCADE,
        related_name="applications",
    )

    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
    )

    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    whatsapp_number = models.CharField(max_length=30)
    years_of_experience = models.PositiveIntegerField()

    demo_video = models.URLField(
        help_text="Link to Google Drive / YouTube demo video"
    )

    cv = models.FileField(upload_to="tutor_cvs/")

    is_approved = models.BooleanField(default=False)

    assessment_date = models.DateField(
        null=True,
        blank=True,
    )

    assessment_time = models.TimeField(
        null=True,
        blank=True,
    )

    assessment_link = models.URLField(blank=True)

    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-applied_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["job_ad", "subject"],
                condition=models.Q(is_approved=True),
                name="unique_approved_tutor_per_subject",
            )
        ]

    def __str__(self):
        return (
            f"{self.full_name} -> {self.subject.name} "
            f"({self.job_ad.ref_code})"
        )


class AssessmentReport(models.Model):
    application = models.OneToOneField(
        TutorApplication,
        on_delete=models.CASCADE,
        related_name="report",
    )

    current_level = models.TextField()
    academic_strengths = models.TextField()
    areas_for_improvement = models.TextField()
    recommendations = models.TextField()

    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"Report: {self.application.job_ad.enquiry.child_name} "
            f"- {self.application.subject.name}"
        )
