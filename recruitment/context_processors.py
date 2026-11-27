
"""
Giant Strides Recruitment Context Processors

Provides centralized recruitment statistics and navigation
counters for the staff management interface.
"""

from django.db.models import Count, Q

from .models import JobAd, TutorApplication


def recruitment_counts(request):
    """
    Provides recruitment statistics to authenticated staff.

    Available template variables:

        ads_sent_count
        pending_approvals_count
        tutor_applications_count
        completed_reports_count
        active_ads_count
        total_recruitment_count
    """

    # --------------------------------------------------
    # 1. Authentication and authorization
    # --------------------------------------------------

    user = getattr(request, "user", None)

    if not user or not user.is_authenticated or not user.is_staff:
        return {}


    # --------------------------------------------------
    # 2. Job advertisement statistics
    # --------------------------------------------------

    job_ad_stats = JobAd.objects.aggregate(
        total_ads=Count("id"),

        completed_reports=Count(
            "id",
            filter=Q(applications__report__isnull=False),
            distinct=True,
        ),
    )


    # --------------------------------------------------
    # 3. Tutor application statistics
    # --------------------------------------------------

    tutor_stats = TutorApplication.objects.aggregate(
        total_applications=Count("id"),

        pending_approvals=Count(
            "id",
            filter=Q(is_approved=False),
        ),
    )


    # --------------------------------------------------
    # 4. Dashboard counters
    # --------------------------------------------------

    ads_sent_count = job_ad_stats["total_ads"] or 0

    pending_approvals_count = (
        tutor_stats["pending_approvals"] or 0
    )

    tutor_applications_count = (
        tutor_stats["total_applications"] or 0
    )

    completed_reports_count = (
        job_ad_stats["completed_reports"] or 0
    )


    # --------------------------------------------------
    # 5. Return template context
    # --------------------------------------------------

    return {

        # Advertisement statistics
        "ads_sent_count": ads_sent_count,

        # Tutor statistics
        "pending_approvals_count": pending_approvals_count,

        "tutor_applications_count": tutor_applications_count,

        # Student reporting statistics
        "completed_reports_count": completed_reports_count,

        # Overall recruitment statistics
        "total_recruitment_count": (
            ads_sent_count + tutor_applications_count
        ),

    }
