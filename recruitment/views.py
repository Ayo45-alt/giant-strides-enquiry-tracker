
# recruitment/views.py

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core import signing
from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.db import IntegrityError, transaction
from django.conf import settings
from django.utils.dateparse import parse_date, parse_time

from tracker.models import Enquiry
from tracker.google_meet_service import (
    get_or_create_student_meeting,
    MeetingGenerationError,
)

from .models import JobAd, TutorApplication, AssessmentReport
from .utils import decode_apply_token, decode_report_token
from .services import send_approval_notifications
from .doc_generator import (
    generate_student_report_docx,
    generate_student_report_pdf,
    build_report_filename,
    get_report_date,
)

# ---------------------------------------------------------
# 1. DASHBOARD: SUCCESSFULLY SENT ADS
# ---------------------------------------------------------

@login_required
def ads_sent_view(request):
    ads = (
        JobAd.objects
        .filter(status="sent")
        .select_related("enquiry", "enquiry__family")
        .prefetch_related(
            "enquiry__subjects",
            "applications",
        )
        .order_by("-last_sent_at", "-created_at")
    )

    return render(
        request,
        "recruitment/ads_sent.html",
        {"ads": ads},
    )


# ---------------------------------------------------------
# 2. DASHBOARD: ADS WITH TUTOR APPLICATIONS
# ---------------------------------------------------------

@login_required
def ads_responded_view(request):
    ads = (
        JobAd.objects
        .filter(applications__isnull=False)
        .distinct()
        .select_related("enquiry", "enquiry__family")
        .prefetch_related(
            "applications__subject",
            "enquiry__subjects",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "recruitment/ads_responded.html",
        {"ads": ads},
    )


# ---------------------------------------------------------
# 3. DASHBOARD: REVERSED ADS
# ---------------------------------------------------------

@login_required
def ads_reversed_view(request):
    ads = (
        JobAd.objects
        .filter(status="reversed")
        .select_related("enquiry", "enquiry__family")
        .prefetch_related(
            "enquiry__subjects",
            "applications",
        )
        .order_by("-reversed_at", "-created_at")
    )

    return render(
        request,
        "recruitment/ads_reversed.html",
        {"ads": ads},
    )


# ---------------------------------------------------------
# 4. APPROVE TUTOR AND SCHEDULE ASSESSMENT
# ---------------------------------------------------------

@login_required
@require_POST
def approve_tutor_view(request, application_id):
    application = get_object_or_404(
        TutorApplication.objects.select_related(
            "job_ad",
            "job_ad__enquiry",
            "subject",
        ),
        id=application_id,
        job_ad__status="sent",
    )

    assessment_date = parse_date(
        request.POST.get("assessment_date", "")
    )

    assessment_time = parse_time(
        request.POST.get("assessment_time", "")
    )

    if not assessment_date or not assessment_time:
        messages.error(
            request,
            "Please provide a valid assessment date and time.",
        )
        return redirect("recruitment:ads_responded")

    if application.is_approved:
        messages.warning(
            request,
            "This tutor has already been approved.",
        )
        return redirect("recruitment:ads_responded")

    enquiry = application.job_ad.enquiry

    # Generate or retrieve the student's permanent Google Meet link.
    # This happens before approval so a Meet failure cannot result
    # in an approved tutor without a valid meeting link.
    try:
        meeting = get_or_create_student_meeting(enquiry)

    except MeetingGenerationError as exc:
        messages.error(
            request,
            (
                "Tutor approval was not completed because the student's "
                f"Google Meet link could not be prepared: {exc}"
            ),
        )
        return redirect("recruitment:ads_responded")

    except Exception:
        messages.error(
            request,
            (
                "Tutor approval was not completed because an unexpected "
                "error occurred while preparing the Google Meet link. "
                "Please check the application logs."
            ),
        )
        return redirect("recruitment:ads_responded")

    assessment_link = meeting.meet_url

    if not assessment_link:
        messages.error(
            request,
            "The student's Google Meet link is not available yet. "
            "Please try again shortly.",
        )
        return redirect("recruitment:ads_responded")

    try:
        with transaction.atomic():
            application.is_approved = True
            application.assessment_date = assessment_date
            application.assessment_time = assessment_time
            application.assessment_link = assessment_link

            application.save(
                update_fields=[
                    "is_approved",
                    "assessment_date",
                    "assessment_time",
                    "assessment_link",
                ]
            )

            enquiry.assessment_date = assessment_date
            enquiry.assessment_time = assessment_time

            enquiry.save(
                update_fields=[
                    "assessment_date",
                    "assessment_time",
                ]
            )

        # Notifications happen after the database transaction.
        # A notification failure must not undo the saved approval.
        try:
            notification_results = send_approval_notifications(
                application
            )

            failed_channels = [
                channel
                for channel, result in notification_results.items()
                if not result.get("success", False)
            ]

        except Exception:
            messages.warning(
                request,
                "Tutor approval was saved, but the notification workflow "
                "encountered an unexpected error. Check the application logs.",
            )
        else:
            if failed_channels:
                messages.warning(
                    request,
                    (
                        f"{application.full_name} was approved, and the "
                        "student's Google Meet link was saved. However, "
                        "one or more notifications could not be sent. "
                        f"Check these channels: {', '.join(failed_channels)}."
                    ),
                )
            else:
                messages.success(
                    request,
                    (
                        f"{application.full_name} approved for "
                        f"{application.subject.name}. Notifications processed."
                    ),
                )

    except IntegrityError:
        messages.error(
            request,
            (
                f"A tutor has already been approved for "
                f"{application.subject.name} on this student."
            ),
        )

    return redirect("recruitment:ads_responded")

# ---------------------------------------------------------
# 5. PUBLIC: TUTOR APPLICATION FORM
# ---------------------------------------------------------

def tutor_apply(request, token):
    try:
        data = decode_apply_token(token)
        job_ad = get_object_or_404(
            JobAd,
            id=data["job_ad_id"],
        )

    except (signing.BadSignature, signing.SignatureExpired):
        return render(
            request,
            "recruitment/link_expired.html",
            {
                "reason": (
                    "This application link is invalid or has expired."
                )
            },
        )

    # Only successfully dispatched adverts accept applications.
    if job_ad.status != "sent":
        return render(
            request,
            "recruitment/link_expired.html",
            {
                "reason": (
                    "This recruitment advertisement is no longer accepting "
                    "applications."
                )
            },
        )

    available_subjects = job_ad.get_unassigned_subjects()

    if request.method == "POST":
        subject_id = request.POST.get("subject")

        subject = get_object_or_404(
            available_subjects,
            id=subject_id,
        )

        TutorApplication.objects.create(
            job_ad=job_ad,
            subject=subject,
            full_name=request.POST.get("full_name"),
            email=request.POST.get("email"),
            whatsapp_number=request.POST.get("whatsapp_number"),
            years_of_experience=request.POST.get(
                "years_of_experience"
            ),
            demo_video=request.POST.get("demo_video"),
            cv=request.FILES.get("cv"),
        )

        return render(
            request,
            "recruitment/apply_success.html",
            {
                "job_ad": job_ad,
                "subject": subject,
            },
        )

    return render(
        request,
        "recruitment/tutor_apply.html",
        {
            "job_ad": job_ad,
            "available_subjects": available_subjects,
        },
    )


# ---------------------------------------------------------
# 6. PUBLIC: POST-ASSESSMENT REPORT FORM
# ---------------------------------------------------------

def submit_report(request, token):
    try:
        data = decode_report_token(token)

        application = get_object_or_404(
            TutorApplication,
            id=data["application_id"],
            is_approved=True,
        )

    except (signing.BadSignature, signing.SignatureExpired):
        return render(
            request,
            "recruitment/link_expired.html",
            {
                "reason": (
                    "This report submission link is invalid or expired."
                )
            },
        )

    if hasattr(application, "report"):
        return render(
            request,
            "recruitment/report_success.html",
            {
                "application": application,
                "already_submitted": True,
            },
        )

    if request.method == "POST":
        AssessmentReport.objects.create(
            application=application,
            current_level=request.POST.get("current_level"),
            academic_strengths=request.POST.get(
                "academic_strengths"
            ),
            areas_for_improvement=request.POST.get(
                "areas_for_improvement"
            ),
            recommendations=request.POST.get(
                "recommendations"
            ),
        )

        return render(
            request,
            "recruitment/report_success.html",
            {"application": application},
        )

    return render(
        request,
        "recruitment/submit_report.html",
        {"application": application},
    )


# ---------------------------------------------------------
# 7. DASHBOARD: FULL STUDENT DETAIL
# ---------------------------------------------------------

@login_required
def student_full_detail(request, enquiry_id):
    enquiry = get_object_or_404(
        Enquiry.objects
        .select_related("family")
        .prefetch_related("subjects", "history"),
        id=enquiry_id,
    )

    job_ad = getattr(enquiry, "job_ad", None)

    approved_apps = (
        job_ad.applications
        .filter(is_approved=True)
        .select_related("subject", "report")
        if job_ad
        else []
    )

    return render(
        request,
        "recruitment/student_full_detail.html",
        {
            "enquiry": enquiry,
            "job_ad": job_ad,
            "approved_apps": approved_apps,
        },
    )


# ---------------------------------------------------------
# 8. DASHBOARD: STUDENT REPORTS
# ---------------------------------------------------------

@login_required
def student_reports_view(request):
    ads_with_reports = (
        JobAd.objects
        .filter(applications__report__isnull=False)
        .distinct()
        .select_related("enquiry", "enquiry__family")
        .prefetch_related(
            "applications__subject",
            "applications__report",
        )
    )

    return render(
        request,
        "recruitment/student_reports.html",
        {"ads": ads_with_reports},
    )



@user_passes_test(
    lambda user: user.is_authenticated and user.is_active and user.is_staff
)
def download_report_docx(request, enquiry_id):
    """Admin-only Word download of a student's assessment report."""
    enquiry = get_object_or_404(
        Enquiry.objects.select_related("family"),
        id=enquiry_id,
    )

    report_date = get_report_date()

    docx_buffer = generate_student_report_docx(
        enquiry,
        report_date=report_date,
    )

    filename = build_report_filename(
        enquiry,
        "docx",
        report_date=report_date,
    )

    response = HttpResponse(
        docx_buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )

    return response


@user_passes_test(
    lambda user: user.is_authenticated and user.is_active and user.is_staff
)
def email_report_docx(request, enquiry_id):
    """Email the parent/guardian a PDF copy of the assessment report."""
    enquiry = get_object_or_404(
        Enquiry.objects.select_related("family"),
        id=enquiry_id,
    )

    if not enquiry.family.parent_email:
        messages.error(
            request,
            "No parent email address is saved for this family.",
        )
        return redirect("recruitment:student_reports")

    report_date = get_report_date()

    pdf_buffer = generate_student_report_pdf(
        enquiry,
        report_date=report_date,
    )

    filename = build_report_filename(
        enquiry,
        "pdf",
        report_date=report_date,
    )

    email = EmailMessage(
        subject=(
            f"Comprehensive Academic Assessment Report - "
            f"{enquiry.child_name}"
        ),
        body=(
            f"Dear {enquiry.family.parent_name},\n\n"
            f"Please find attached the comprehensive academic "
            f"assessment report for {enquiry.child_name}.\n\n"
            f"Student Reference: {enquiry.reference or f'GS-{enquiry.pk:04d}'}\n"
            f"Report Date: {report_date.strftime('%d %B %Y')}\n\n"
            "The attached document is in PDF format for easy viewing "
            "and record keeping.\n\n"
            "Warm regards,\n"
            "Admissions & Academic Team\n"
            "Giant Strides Online School"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[enquiry.family.parent_email],
    )

    email.attach(
        filename,
        pdf_buffer.getvalue(),
        "application/pdf",
    )

    email.send(fail_silently=False)

    messages.success(
        request,
        f"PDF assessment report emailed to "
        f"{enquiry.family.parent_email}.",
    )

    return redirect("recruitment:student_reports")
