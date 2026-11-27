
# recruitment/urls.py

from django.urls import path
from . import views

app_name = "recruitment"

urlpatterns = [
    path(
        "ads-sent/",
        views.ads_sent_view,
        name="ads_sent",
    ),

    path(
        "ads-responded/",
        views.ads_responded_view,
        name="ads_responded",
    ),

    path(
        "ads-reversed/",
        views.ads_reversed_view,
        name="ads_reversed",
    ),

    path(
        "approve-tutor/<int:application_id>/",
        views.approve_tutor_view,
        name="approve_tutor",
    ),

    path(
        "student-reports/",
        views.student_reports_view,
        name="student_reports",
    ),

    path(
        "student-detail/<int:enquiry_id>/",
        views.student_full_detail,
        name="student_full_detail",
    ),

    path(
        "report-download/<int:enquiry_id>/",
        views.download_report_docx,
        name="download_report_docx",
    ),

    path(
        "report-email/<int:enquiry_id>/",
        views.email_report_docx,
        name="email_report_docx",
    ),

    path(
        "apply/<str:token>/",
        views.tutor_apply,
        name="tutor_apply",
    ),

    path(
        "report/<str:token>/",
        views.submit_report,
        name="submit_report",
    ),
]
