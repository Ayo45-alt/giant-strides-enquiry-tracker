from datetime import timedelta
from django.db.models import Count, Q
from django.utils import timezone

from .models import Enquiry


def staff_navigation_counts():
    active = Enquiry.objects.filter(is_archived=False)
    today = timezone.localdate()
    five_days_ago = today - timedelta(days=5)
    due_filter = Q(
        next_followup__lte=today,
        status__in=("new", "assessment_booked", "assessed"),
    ) | Q(
        status="new",
        status_changed_at__date__lte=five_days_ago,
    )
    return active.aggregate(
        new=Count("pk", filter=Q(status="new")),
        assessment_booked=Count("pk", filter=Q(status="assessment_booked")),
        assessed=Count("pk", filter=Q(status="assessed")),
        started=Count("pk", filter=Q(status="started")),
        not_proceeding=Count("pk", filter=Q(status="not_proceeding")),
        followups_due=Count("pk", filter=due_filter),
    )


def staff_navigation(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return {}
    return {"staff_nav_counts": staff_navigation_counts()}
