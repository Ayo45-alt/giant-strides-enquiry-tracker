# recruitment/services.py

import logging
from datetime import timedelta

import requests

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .utils import (
    generate_salted_apply_url,
    generate_salted_report_url,
)


logger = logging.getLogger(__name__)


# =============================================================
# CONFIGURATION
# =============================================================

WHATSAPP_TIMEOUT = 20
TELEGRAM_TIMEOUT = 20

CONSOLE_EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
)

REQUIRED_AD_CHANNELS = ("whatsapp", "telegram")


# =============================================================
# GENERAL HELPERS
# =============================================================

def _get_setting(name, default=None):
    return getattr(settings, name, default)


def _safe_error(exc, provider):
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{provider} request timed out."

    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        status = (
            response.status_code
            if response is not None
            else "unknown"
        )
        return f"{provider} returned HTTP {status}."

    if isinstance(exc, requests.exceptions.RequestException):
        return f"{provider} network request failed."

    return f"{provider} encountered an unexpected error."


def _channel_result(provider, attempted=False):
    return {
        "provider": provider,
        "attempted": attempted,
        "success": False,
        "status": "not_attempted",
        "message_id": None,
        "error": None,
    }


def _summarize_dispatch(results, job_ad):
    """
    WhatsApp OR Telegram determines recruitment dispatch success.

    Email is supplementary and is deliberately excluded from
    summary.any_success.
    """

    all_channels = ("telegram", "whatsapp", "email")

    successful_channels = [
        channel
        for channel in all_channels
        if results[channel]["success"]
    ]

    attempted_channels = [
        channel
        for channel in all_channels
        if results[channel]["attempted"]
    ]

    required_successful = [
        channel
        for channel in REQUIRED_AD_CHANNELS
        if results[channel]["success"]
    ]

    required_failed = [
        channel
        for channel in REQUIRED_AD_CHANNELS
        if not results[channel]["success"]
    ]

    results["summary"] = {
        "job_ad_id": job_ad.pk,
        "job_ad_ref": job_ad.ref_code,

        "required_channels": list(REQUIRED_AD_CHANNELS),

        "successful_channels": successful_channels,
        "attempted_channels": attempted_channels,

        "successful_count": len(successful_channels),
        "attempted_count": len(attempted_channels),

        "required_successful_channels": required_successful,
        "required_failed_channels": required_failed,

        "failed_channels": [
            channel
            for channel in attempted_channels
            if not results[channel]["success"]
        ],

        # This is the important field used by signals.py.
        # Email success alone cannot make this True.
        "any_success": bool(required_successful),

        "all_required_successful": (
            len(required_successful) == len(REQUIRED_AD_CHANNELS)
        ),

        "email_success": results["email"]["success"],
    }

    return results


def _build_reversal_reason(results):
    """Build an audit-friendly explanation for a reversed JobAd."""

    reasons = []

    for channel in REQUIRED_AD_CHANNELS:
        result = results[channel]

        error = (
            result.get("error")
            or result.get("status")
            or "No successful provider acceptance"
        )

        reasons.append(f"{channel.title()}: {error}")

    return (
        "Initial recruitment announcement failed. "
        "Neither WhatsApp nor Telegram accepted the announcement. "
        + " | ".join(reasons)
    )



def _update_job_ad_lifecycle(job_ad, results):
    """
    Update the initial dispatch lifecycle.

    WhatsApp OR Telegram accepted:
        pending -> sent

    Both fail:
        pending remains pending.

    Reversed advertisements cannot be automatically reactivated.

    A reminder dispatch must use update_lifecycle=False.
    """

    from .models import JobAd

    summary = results["summary"]

    if job_ad.status != "pending":
        logger.info(
            "Lifecycle update skipped for JobAd %s. Current status: %s",
            job_ad.pk,
            job_ad.status,
        )
        return

    if not summary.get("any_success", False):
        logger.warning(
            "No required channel accepted JobAd %s. "
            "It remains pending.",
            job_ad.pk,
        )
        return

    updated = JobAd.objects.filter(
        pk=job_ad.pk,
        status="pending",
    ).update(
        status="sent",
        last_sent_at=timezone.now(),
        reminder_sent=False,
    )

    job_ad.refresh_from_db(
        fields=[
            "status",
            "last_sent_at",
            "reminder_sent",
            "reversed_at",
            "reversal_reason",
        ]
    )

    if updated:
        logger.info(
            "JobAd %s marked SENT. Successful required channels: %s",
            job_ad.pk,
            summary.get("required_successful_channels", []),
        )
    else:
        logger.warning(
            "JobAd %s was not updated. Its status may have changed "
            "during dispatch.",
            job_ad.pk,
        )


# =============================================================
# SCHEDULE FORMATTING
# =============================================================

def format_schedule_info(class_schedule):
    if isinstance(class_schedule, dict) and class_schedule:
        formatted = ", ".join(
            f"{day}: {time}"
            for day, time in class_schedule.items()
        )
        return len(class_schedule), formatted

    if isinstance(class_schedule, list) and class_schedule:
        return (
            len(class_schedule),
            ", ".join(str(item) for item in class_schedule),
        )

    if isinstance(class_schedule, str) and class_schedule.strip():
        return 1, class_schedule.strip()

    if class_schedule:
        return 1, str(class_schedule)

    return 0, "To be confirmed"


# =============================================================
# RECRUITMENT MESSAGE FORMATTING
# =============================================================

def format_job_ad_message(job_ad, subjects_queryset=None):
    enquiry = job_ad.enquiry

    subjects = (
        subjects_queryset
        if subjects_queryset is not None
        else enquiry.subjects.all()
    )

    subject_names = ", ".join(
        subject.name for subject in subjects
    ) or "General"

    days_count, schedule_text = format_schedule_info(
        enquiry.class_schedule
    )

    family = enquiry.family
    country = getattr(family, "country", None) or "International"

    apply_link = generate_salted_apply_url(job_ad.id)

    return (
        "NEW JOB ALERT\n"
        f"Reference: {job_ad.ref_code}\n\n"
        f"Online Tutor Opportunity - {country}\n"
        f"Student: {enquiry.child_name}\n"
        f"Lessons: {days_count} day(s) per week\n"
        f"Schedule: {schedule_text}\n"
        f"Subjects: {subject_names}\n\n"
        "Interested tutors can apply using the link below:\n"
        f"{apply_link}"
    )


# =============================================================
# WHATSAPP - GREEN API
# =============================================================

def _normalize_whatsapp_recipient(chat_id):
    if not isinstance(chat_id, str) or not chat_id.strip():
        return None

    chat_id = chat_id.strip()

    if chat_id.endswith("@g.us"):
        return chat_id

    if chat_id.endswith("@c.us"):
        return chat_id

    clean_number = "".join(
        character
        for character in chat_id
        if character.isdigit()
    )

    if not clean_number:
        return None

    return f"{clean_number}@c.us"


def send_whatsapp_message(chat_id, text):
    instance_id = _get_setting("GREEN_API_ID_INSTANCE")
    api_token = _get_setting("GREEN_API_TOKEN")
    api_base = _get_setting("GREEN_API_URL")

    if not instance_id or not api_token or not api_base:
        return {
            "success": False,
            "provider": "green_api",
            "status": "configuration_error",
            "error": "Green API credentials or base URL are missing.",
        }

    recipient = _normalize_whatsapp_recipient(chat_id)

    if not recipient:
        return {
            "success": False,
            "provider": "green_api",
            "status": "invalid_recipient",
            "error": "Invalid or missing WhatsApp recipient.",
        }

    if not isinstance(text, str) or not text.strip():
        return {
            "success": False,
            "provider": "green_api",
            "status": "invalid_message",
            "error": "WhatsApp message cannot be empty.",
        }

    # Keep the corrected Green API URL structure.
    url = (
        f"{api_base.rstrip('/')}/"
        f"waInstance{instance_id}/"
        f"sendMessage/{api_token}"
    )

    payload = {
        "chatId": recipient,
        "message": text,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=WHATSAPP_TIMEOUT,
        )

        response.raise_for_status()

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        message_id = response_data.get("idMessage")

        if not message_id:
            return {
                "success": False,
                "provider": "green_api",
                "status": "unconfirmed",
                "error": (
                    "Green API did not confirm message acceptance."
                ),
                "http_status": response.status_code,
            }

        logger.info(
            "Green API accepted a WhatsApp message. Recipient type: %s.",
            "group" if recipient.endswith("@g.us") else "individual",
        )

        return {
            "success": True,
            "provider": "green_api",
            "status": "accepted",
            "message_id": message_id,
            "http_status": response.status_code,
        }

    except requests.exceptions.RequestException as exc:
        error = _safe_error(exc, "Green API")
        logger.error("%s", error)

        return {
            "success": False,
            "provider": "green_api",
            "status": "failed",
            "error": error,
        }

    except Exception:
        logger.exception("Unexpected Green API integration failure.")

        return {
            "success": False,
            "provider": "green_api",
            "status": "failed",
            "error": "Unexpected WhatsApp integration failure.",
        }


# =============================================================
# TELEGRAM
# =============================================================

def send_telegram_message(chat_id, text):
    bot_token = _get_setting("TELEGRAM_BOT_TOKEN")

    if not bot_token or not chat_id:
        return {
            "success": False,
            "provider": "telegram",
            "status": "configuration_error",
            "error": "Telegram bot token or group ID is missing.",
        }

    if not isinstance(text, str) or not text.strip():
        return {
            "success": False,
            "provider": "telegram",
            "status": "invalid_message",
            "error": "Telegram message cannot be empty.",
        }

    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=TELEGRAM_TIMEOUT,
        )

        response.raise_for_status()

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        if response_data.get("ok") is not True:
            description = response_data.get(
                "description",
                "Telegram did not accept the message.",
            )

            logger.warning(
                "Telegram rejected a recruitment announcement: %s",
                description,
            )

            return {
                "success": False,
                "provider": "telegram",
                "status": "rejected",
                "error": description,
            }

        message_id = (
            response_data.get("result", {}).get("message_id")
        )

        logger.info("Telegram accepted recruitment announcement.")

        return {
            "success": True,
            "provider": "telegram",
            "status": "accepted",
            "message_id": message_id,
            "http_status": response.status_code,
        }

    except requests.exceptions.RequestException as exc:
        error = _safe_error(exc, "Telegram")
        logger.error("%s", error)

        return {
            "success": False,
            "provider": "telegram",
            "status": "failed",
            "error": error,
        }

    except Exception:
        logger.exception("Unexpected Telegram integration failure.")

        return {
            "success": False,
            "provider": "telegram",
            "status": "failed",
            "error": "Unexpected Telegram integration failure.",
        }


# =============================================================
# EMAIL
# =============================================================

def send_tutors_email(subject, message, recipient=None):
    """
    Send email to an explicitly supplied recipient.

    If recipient is omitted, use TUTORS_GROUP_EMAIL.

    Console backend output is not counted as actual delivery.
    """

    recipient = recipient or _get_setting("TUTORS_GROUP_EMAIL")
    from_email = _get_setting("DEFAULT_FROM_EMAIL")

    if not recipient or not from_email:
        return {
            "success": False,
            "provider": "django_email",
            "status": "configuration_error",
            "error": "Email recipient or sender address is missing.",
        }

    if recipient.lower().endswith(
        ("@example.com", "@example.org", "@example.net")
    ):
        return {
            "success": False,
            "provider": "django_email",
            "status": "placeholder_recipient",
            "error": "The configured email address is a placeholder.",
        }

    email_backend = _get_setting("EMAIL_BACKEND", "")

    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )

        if email_backend == CONSOLE_EMAIL_BACKEND:
            logger.info(
                "Email written to console backend only; "
                "no external email was delivered."
            )

            return {
                "success": False,
                "provider": "django_email",
                "status": "console_only",
                "error": (
                    "Console backend is active. No external email "
                    "was delivered."
                ),
            }

        if sent_count == 1:
            logger.info("Email backend accepted an email.")

            return {
                "success": True,
                "provider": "django_email",
                "status": "accepted_by_backend",
            }

        return {
            "success": False,
            "provider": "django_email",
            "status": "not_accepted",
            "error": "Email backend did not confirm submission.",
        }

    except Exception:
        logger.exception("Email dispatch failed.")

        return {
            "success": False,
            "provider": "django_email",
            "status": "failed",
            "error": "Email dispatch failed.",
        }


# =============================================================
# UNIFIED RECRUITMENT DISPATCH
# =============================================================

def dispatch_ad_to_groups(
    job_ad,
    subjects_queryset=None,
    update_lifecycle=True,
):
    """
    Dispatch through Telegram, WhatsApp and supplementary email.

    Option A:
        WhatsApp OR Telegram accepted -> JobAd sent.
        Both failed -> JobAd reversed.

    Email never determines the JobAd lifecycle.

    Set update_lifecycle=False for reminder dispatches so a failed
    reminder cannot reverse an already-sent JobAd.
    """

    results = {
        "telegram": _channel_result("telegram"),
        "whatsapp": _channel_result("green_api"),
        "email": _channel_result("django_email"),
    }

    try:
        message = format_job_ad_message(
            job_ad,
            subjects_queryset=subjects_queryset,
        )

    except Exception:
        logger.exception(
            "Could not format recruitment advertisement %s.",
            job_ad.pk,
        )

        for channel in results.values():
            channel["status"] = "message_format_error"
            channel["error"] = (
                "Recruitment message could not be generated."
            )

        results = _summarize_dispatch(results, job_ad)

        if update_lifecycle:
            _update_job_ad_lifecycle(job_ad, results)

        return results

    # ---------------------------------------------------------
    # TELEGRAM
    # ---------------------------------------------------------

    telegram_group_id = _get_setting(
        "TELEGRAM_TUTORS_GROUP_ID"
    )

    telegram_configured = bool(
        _get_setting("TELEGRAM_BOT_TOKEN")
        and telegram_group_id
    )

    results["telegram"]["attempted"] = telegram_configured

    if telegram_configured:
        telegram_result = send_telegram_message(
            telegram_group_id,
            message,
        )

        results["telegram"].update(telegram_result)

    else:
        results["telegram"].update({
            "status": "configuration_error",
            "error": "Telegram bot token or group ID is missing.",
        })

        logger.warning(
            "Telegram announcement skipped: configuration incomplete."
        )

    # ---------------------------------------------------------
    # WHATSAPP
    # ---------------------------------------------------------

    whatsapp_group_id = _get_setting(
        "WHATSAPP_TUTORS_GROUP_ID"
    )

    whatsapp_configured = bool(
        _get_setting("GREEN_API_ID_INSTANCE")
        and _get_setting("GREEN_API_TOKEN")
        and _get_setting("GREEN_API_URL")
        and whatsapp_group_id
    )

    results["whatsapp"]["attempted"] = whatsapp_configured

    if whatsapp_configured:
        whatsapp_result = send_whatsapp_message(
            whatsapp_group_id,
            message,
        )

        results["whatsapp"].update(whatsapp_result)

    else:
        results["whatsapp"].update({
            "status": "configuration_error",
            "error": (
                "WhatsApp group ID or Green API configuration is missing."
            ),
        })

        logger.warning(
            "WhatsApp announcement skipped: configuration incomplete."
        )

    # ---------------------------------------------------------
    # SUPPLEMENTARY EMAIL
    # ---------------------------------------------------------

    tutors_email = _get_setting("TUTORS_GROUP_EMAIL")

    results["email"]["attempted"] = bool(
        tutors_email and _get_setting("DEFAULT_FROM_EMAIL")
    )

    if results["email"]["attempted"]:
        email_result = send_tutors_email(
            subject=f"New Job Alert - Ref: {job_ad.ref_code}",
            message=message,
            recipient=tutors_email,
        )

        results["email"].update(email_result)

    else:
        results["email"].update({
            "status": "configuration_error",
            "error": "Tutors email or sender address is missing.",
        })

    # ---------------------------------------------------------
    # SUMMARY AND LIFECYCLE
    # ---------------------------------------------------------

    results = _summarize_dispatch(results, job_ad)

    if update_lifecycle:
        _update_job_ad_lifecycle(job_ad, results)

    logger.info(
        "Recruitment dispatch completed. Ref: %s | "
        "Required successes: %s | Attempted: %s | JobAd status: %s",
        job_ad.ref_code,
        results["summary"]["required_successful_channels"],
        results["summary"]["attempted_channels"],
        getattr(job_ad, "status", "unknown"),
    )

    return results


# =============================================================
# 12-HOUR UNFILLED AD REMINDER
# =============================================================


def check_and_resend_unfilled_ad(job_ad_id):
    """
    Send one 12-hour reminder for remaining unassigned subjects.

    Only sent advertisements qualify.

    A successful reminder updates last_sent_at and reminder_sent.
    A failed reminder does not reverse the original advertisement.
    """

    from .models import JobAd

    try:
        job_ad = JobAd.objects.select_related(
            "enquiry",
            "enquiry__family",
        ).get(pk=job_ad_id)

    except JobAd.DoesNotExist:
        logger.warning(
            "Reminder skipped: JobAd %s does not exist.",
            job_ad_id,
        )

        return {
            "success": False,
            "status": "job_ad_not_found",
        }

    if job_ad.status != "sent":
        logger.info(
            "Reminder skipped for JobAd %s because its status is %s.",
            job_ad_id,
            job_ad.status,
        )

        return {
            "success": False,
            "status": "job_ad_not_active",
            "job_ad_id": job_ad_id,
        }

    # Prevent duplicate reminders if the task was scheduled more than once.
    if job_ad.reminder_sent:
        logger.info(
            "Reminder skipped for JobAd %s because its reminder "
            "has already been sent.",
            job_ad_id,
        )

        return {
            "success": True,
            "status": "reminder_already_sent",
            "job_ad_id": job_ad_id,
        }

    unassigned_subjects = job_ad.get_unassigned_subjects()

    if not unassigned_subjects.exists():
        logger.info(
            "No unassigned subjects remain for JobAd %s.",
            job_ad_id,
        )

        return {
            "success": True,
            "status": "fully_assigned",
            "job_ad_id": job_ad_id,
        }

    logger.info(
        "Unassigned subjects remain for JobAd %s. "
        "Starting reminder dispatch.",
        job_ad_id,
    )

    results = dispatch_ad_to_groups(
        job_ad,
        subjects_queryset=unassigned_subjects,
        update_lifecycle=False,
    )

    summary = results.get("summary", {})

    if summary.get("any_success", False):
        updated = JobAd.objects.filter(
            pk=job_ad_id,
            status="sent",
            reminder_sent=False,
        ).update(
            last_sent_at=timezone.now(),
            reminder_sent=True,
        )

        if updated:
            logger.info(
                "Reminder accepted by at least one required channel "
                "for JobAd %s.",
                job_ad_id,
            )

            return {
                "success": True,
                "status": "reminder_accepted",
                "job_ad_id": job_ad_id,
                "dispatch": results,
            }

        logger.warning(
            "Reminder was accepted, but JobAd %s was not updated. "
            "Its status may have changed during dispatch.",
            job_ad_id,
        )

        return {
            "success": False,
            "status": "reminder_not_recorded",
            "job_ad_id": job_ad_id,
            "dispatch": results,
        }

    logger.warning(
        "Reminder failed for JobAd %s. "
        "The original advertisement remains sent.",
        job_ad_id,
    )

    return {
        "success": False,
        "status": "reminder_failed",
        "job_ad_id": job_ad_id,
        "dispatch": results,
    }


# =============================================================
# TUTOR APPROVAL AND ASSESSMENT NOTIFICATIONS
# =============================================================

def send_approval_notifications(application):
    """
    Send tutor approval notifications through the approved channels:

    1. Parent/guardian email
    2. Approved tutor email
    3. Parent/guardian WhatsApp

    The student's permanent Google Meet link is stored in
    application.assessment_link by the approval view.
    """

    enquiry = application.job_ad.enquiry
    family = enquiry.family

    report_link = generate_salted_report_url(application.id)

    assessment_date = (
        application.assessment_date.strftime("%A, %d %B %Y")
        if application.assessment_date
        else "To be confirmed"
    )

    assessment_time = (
        application.assessment_time.strftime("%I:%M %p")
        if application.assessment_time
        else "To be confirmed"
    )

    meeting_link = application.assessment_link

    # -----------------------------------------------------
    # PARENT/GUARDIAN EMAIL
    # -----------------------------------------------------

    parent_email_subject = (
        f"Tutor Approval and Assessment Details - {enquiry.child_name}"
    )

    parent_email_message = (
        f"Dear {family.parent_name},\n\n"
        f"We are pleased to inform you that a tutor has been approved "
        f"for {enquiry.child_name}.\n\n"
        f"Tutor: {application.full_name}\n"
        f"Subject: {application.subject.name}\n\n"
        f"Assessment Date: {assessment_date}\n"
        f"Assessment Time: {assessment_time}\n\n"
        f"Google Meet Link:\n{meeting_link}\n\n"
        "Please keep this meeting link for your records. "
        "It is the permanent meeting link associated with your child.\n\n"
        "Kind regards,\n"
        "Admissions & Academic Team\n"
        "Giant Strides Online School"
    )

    # -----------------------------------------------------
    # APPROVED TUTOR EMAIL
    # -----------------------------------------------------

    tutor_email_subject = (
        f"Tutor Approval - {enquiry.child_name} ({application.subject.name})"
    )

    tutor_email_message = (
        f"Dear {application.full_name},\n\n"
        "Your application has been approved.\n\n"
        f"Student: {enquiry.child_name}\n"
        f"Subject: {application.subject.name}\n\n"
        f"Assessment Date: {assessment_date}\n"
        f"Assessment Time: {assessment_time}\n\n"
        f"Google Meet Link:\n{meeting_link}\n\n"
        "Please use this link to join the student's initial teaching "
        "assessment at the scheduled time.\n\n"
        "After completing the assessment, submit your assessment report "
        f"using the following link:\n{report_link}\n\n"
        "Kind regards,\n"
        "Recruitment & Academic Team\n"
        "Giant Strides Online School"
    )

    # -----------------------------------------------------
    # PARENT/GUARDIAN WHATSAPP
    # -----------------------------------------------------

    parent_whatsapp_message = (
        f"Hello {family.parent_name},\n\n"
        f"A tutor has been approved for {enquiry.child_name}.\n\n"
        f"Tutor: {application.full_name}\n"
        f"Subject: {application.subject.name}\n\n"
        f"Assessment Date: {assessment_date}\n"
        f"Assessment Time: {assessment_time}\n\n"
        f"Google Meet Link:\n{meeting_link}\n\n"
        "Please keep this link for your child's online sessions.\n\n"
        "Regards,\n"
        "Giant Strides Online School"
    )

    # -----------------------------------------------------
    # SEND ONLY THE THREE APPROVED CHANNELS
    # -----------------------------------------------------

    results = {}

    if family.parent_email:
        results["parent_email"] = send_tutors_email(
            parent_email_subject,
            parent_email_message,
            recipient=family.parent_email,
        )
    else:
        results["parent_email"] = {
            "success": False,
            "error": "No parent email address is saved.",
        }

    if application.email:
        results["tutor_email"] = send_tutors_email(
            tutor_email_subject,
            tutor_email_message,
            recipient=application.email,
        )
    else:
        results["tutor_email"] = {
            "success": False,
            "error": "No tutor email address is saved.",
        }

    parent_whatsapp_number = family.phone_normalized

    if parent_whatsapp_number:
        results["parent_whatsapp"] = send_whatsapp_message(
            parent_whatsapp_number,
            parent_whatsapp_message,
        )
    else:
        results["parent_whatsapp"] = {
            "success": False,
            "error": "No parent WhatsApp number is saved.",
        }

    return results