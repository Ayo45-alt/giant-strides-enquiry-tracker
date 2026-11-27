
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    path(
        "tracker/",
        include("tracker.staff_urls"),
    ),

    path(
        "recruitment/",
        include("recruitment.urls"),
    ),

    path(
        "google/oauth/",
        include("tracker.google_calendar_urls"),
    ),

    path(
        "",
        include("tracker.urls"),
    ),
]
