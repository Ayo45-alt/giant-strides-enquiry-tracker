
from django.urls import path

from . import google_calendar_views

urlpatterns = [
    path(
        "authorize/",
        google_calendar_views.google_oauth_authorize,
        name="google_oauth_authorize",
    ),
    path(
        "callback/",
        google_calendar_views.google_oauth_callback,
        name="google_oauth_callback",
    ),
]
