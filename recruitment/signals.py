
# recruitment/signals.py

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from django_q.tasks import schedule

from tracker.models import StageChange

from .models import JobAd
from .services import dispatch_ad_to_groups


logger = logging.getLogger(__name__)

REMINDER_DELAY_HOURS = 12


def dispatch_job_ad_after_commit(job_ad_id):
    """
    Dispatch a newly created JobAd after its transaction commits.

    WhatsApp OR Telegram accepted:
        JobAd becomes sent.

    Both fail:
        JobAd remains pending for a later retry.

    Reversed advertisements are never automatically reactivated.
    """

    try:
        job_ad = JobAd.objects.select_related(
            "enquiry",
            "enquiry__family",
        ).get(pk=job_ad_id)

    except JobAd.DoesNotExist:
        logger.error(
            "Recruitment dispatch cancelled: JobAd %s does not exist.",
            job_ad_id,
        )
        return

    if job_ad.status != "pending":
        logger.info(
            "Automatic dispatch skipped for JobAd %s. Current status: %s",
            job_ad_id,
            job_ad.status,
        )
        return

    logger.info(
        "Starting recruitment announcement for JobAd %s (%s).",
        job_ad.pk,
        job_ad.ref_code,
    )

    try:
        results = dispatch_ad_to_groups(job_ad)

    except Exception:
        logger.exception(
            "Unexpected dispatch failure for JobAd %s. "
            "The advertisement will remain pending.",
            job_ad_id,
        )
        return

    summary = (
        results.get("summary", {})
        if isinstance(results, dict)
        else {}
    )

    if not summary.get("any_success", False):
        logger.warning(
            "Initial dispatch failed for JobAd %s. "
            "The advertisement remains pending. Results: %s",
            job_ad_id,
            results,
        )
        return

    job_ad.refresh_from_db()

    if job_ad.status != "sent":
        logger.error(
            "Dispatch reported success for JobAd %s, "
            "but its database status is %s. "
            "No reminder will be scheduled.",
            job_ad_id,
            job_ad.status,
        )
        return

    logger.info(
        "JobAd %s successfully dispatched. Accepted channels: %s",
        job_ad_id,
        summary.get("required_successful_channels", []),
    )

    # Schedule the 12-hour reminder only after confirmed dispatch.
    try:
        schedule(
            "recruitment.services.check_and_resend_unfilled_ad",
            job_ad_id,
            schedule_type="O",
            next_run=timezone.now() + timedelta(
                hours=REMINDER_DELAY_HOURS
            ),
        )

        logger.info(
            "Scheduled recruitment reminder for JobAd %s in %s hours.",
            job_ad_id,
            REMINDER_DELAY_HOURS,
        )

    except Exception:
        logger.exception(
            "Could not schedule reminder for JobAd %s.",
            job_ad_id,
        )


@receiver(
    post_save,
    sender=StageChange,
    dispatch_uid="recruitment.trigger_ad_on_assessment_booked",
)
def trigger_ad_on_assessment_booked(
    sender,
    instance,
    created,
    **kwargs,
):
    """
    Create a JobAd when an enquiry transitions from NEW
    to ASSESSMENT_BOOKED.

    Only newly created JobAds trigger automatic dispatch.
    Existing advertisements are never automatically broadcast again.
    """

    if not created:
        return

    if instance.from_status != "new":
        return

    if instance.to_status != "assessment_booked":
        return

    enquiry = instance.enquiry

    logger.info(
        "Assessment booked for enquiry %s (%s).",
        enquiry.pk,
        enquiry.reference,
    )

    try:
        job_ad, ad_created = JobAd.objects.get_or_create(
            enquiry=enquiry,
        )

    except Exception:
        logger.exception(
            "Failed to create or retrieve JobAd for enquiry %s.",
            enquiry.pk,
        )
        raise

    if not ad_created:
        logger.info(
            "JobAd %s already exists for enquiry %s. "
            "Automatic broadcast skipped to prevent duplicates.",
            job_ad.pk,
            enquiry.pk,
        )
        return

    logger.info(
        "Created JobAd %s for enquiry %s.",
        job_ad.pk,
        enquiry.pk,
    )

    transaction.on_commit(
        lambda job_ad_id=job_ad.pk: dispatch_job_ad_after_commit(
            job_ad_id
        )
    )
