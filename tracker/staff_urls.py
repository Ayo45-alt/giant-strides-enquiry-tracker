from django.urls import path

from . import staff_views

app_name = "staff"

urlpatterns = [
    path("login/", staff_views.StaffLoginView.as_view(), name="staff_login"),
    path("logout/", staff_views.staff_logout, name="staff_logout"),
    path("report/", staff_views.staff_report, name="staff_report"),
    path("download/", staff_views.staff_download, name="staff_download"),
    path(
        "download/everything/",
        staff_views.staff_download_everything,
        name="staff_download_everything",
    ),
    path(
        "<int:pk>/panel/",
        staff_views.staff_enquiry_panel,
        name="staff_enquiry_panel",
    ),
    path("", staff_views.staff_list, name="staff_list"),
    path("<int:pk>/", staff_views.staff_detail, name="staff_detail"),
]
