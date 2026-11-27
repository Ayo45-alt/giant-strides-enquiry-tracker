
import hmac
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect

from google.auth.exceptions import GoogleAuthError
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from .google_calendar_auth import (
    create_google_oauth_flow,
    save_google_credentials,
)


logger = logging.getLogger(__name__)

OAUTH_STATE_SESSION_KEY = "google_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "google_oauth_code_verifier"


@staff_member_required
def google_oauth_authorize(request):
    flow = create_google_oauth_flow(
        autogenerate_code_verifier=True,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        login_hint="giantstridesonlineschool1@gmail.com",
    )

    # Preserve both values for the callback.
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_VERIFIER_SESSION_KEY] = (
        flow.code_verifier
    )

    request.session.modified = True

    return redirect(authorization_url)


@staff_member_required
def google_oauth_callback(request):
    if request.GET.get("error"):
        request.session.pop(OAUTH_STATE_SESSION_KEY, None)
        request.session.pop(OAUTH_VERIFIER_SESSION_KEY, None)

        messages.error(
            request,
            "Google Calendar authorization was not completed.",
        )
        return redirect("tracker:enquiry_create")

    expected_state = request.session.pop(
        OAUTH_STATE_SESSION_KEY,
        None,
    )

    code_verifier = request.session.pop(
        OAUTH_VERIFIER_SESSION_KEY,
        None,
    )

    received_state = request.GET.get("state")

    if (
        not expected_state
        or not received_state
        or not code_verifier
        or not hmac.compare_digest(
            expected_state,
            received_state,
        )
    ):
        request.session.modified = True

        messages.error(
            request,
            "Google authorization could not be verified. "
            "Please start the connection process again.",
        )
        return redirect("tracker:enquiry_create")

    request.session.modified = True

    try:
        # Restore the exact verifier generated during authorization.
        flow = create_google_oauth_flow(
            state=expected_state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )

        flow.fetch_token(
            authorization_response=request.build_absolute_uri()
        )

        credentials = flow.credentials

        save_google_credentials(credentials)

    except (GoogleAuthError, OAuth2Error, RequestException):
        logger.exception(
            "Google Calendar OAuth token exchange failed."
        )

        messages.error(
            request,
            "Google Calendar could not complete the connection. "
            "Please try authorizing again.",
        )
        return redirect("tracker:enquiry_create")

    except Exception:
        logger.exception(
            "Unexpected error while connecting Google Calendar."
        )

        messages.error(
            request,
            "An unexpected error occurred while connecting "
            "Google Calendar. Check the server logs."
        )
        return redirect("tracker:enquiry_create")

    messages.success(
        request,
        "Google Calendar connected successfully.",
    )

    return redirect("tracker:enquiry_create")
