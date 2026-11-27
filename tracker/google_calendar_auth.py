
import json
import os
import tempfile

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


def get_google_client_config():
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

    if not client_id or not client_secret:
        raise ImproperlyConfigured(
            "Google OAuth client credentials are missing. "
            "Check your .env configuration."
        )

    if not redirect_uri:
        raise ImproperlyConfigured(
            "GOOGLE_OAUTH_REDIRECT_URI is not configured."
        )

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def create_google_oauth_flow(
    state=None,
    code_verifier=None,
    autogenerate_code_verifier=False,
):
    """
    Create a Google OAuth flow.

    During authorization:
        autogenerate_code_verifier=True

    During callback:
        Supply the original code_verifier and keep
        autogenerate_code_verifier=False.
    """

    flow = Flow.from_client_config(
        get_google_client_config(),
        scopes=settings.GOOGLE_OAUTH_SCOPE.split(),
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=autogenerate_code_verifier,
    )

    flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

    return flow


def get_google_token_path():
    return Path(settings.GOOGLE_CALENDAR_TOKEN_FILE)


def save_google_credentials(credentials):
    """
    Save credentials atomically.

    Preserve an existing refresh token if Google does not
    return a new one during a subsequent authorization.
    """

    token_path = get_google_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve the existing refresh token when necessary.
    if not credentials.refresh_token and token_path.exists():
        try:
            existing_data = json.loads(
                token_path.read_text(encoding="utf-8")
            )
            credentials.refresh_token = existing_data.get(
                "refresh_token"
            )
        except (OSError, json.JSONDecodeError):
            pass

    if not credentials.refresh_token:
        raise ImproperlyConfigured(
            "Google did not provide a refresh token. "
            "Reconnect the Google account with offline access."
        )

    token_json = credentials.to_json()

    # Write to a temporary file in the same directory,
    # then replace the destination atomically.
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=token_path.parent,
            prefix=".google-token-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(token_json)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            # Windows may not support POSIX permission semantics.
            pass

        os.replace(temp_path, token_path)

        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass

    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def load_google_credentials():
    token_path = get_google_token_path()

    if not token_path.exists():
        raise ImproperlyConfigured(
            "Google Calendar has not been connected. "
            "Authorize the Google account first."
        )

    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            scopes=settings.GOOGLE_OAUTH_SCOPE.split(),
        )
    except (ValueError, OSError) as exc:
        raise ImproperlyConfigured(
            "The Google Calendar token file is invalid. "
            "Reconnect the Google account."
        ) from exc

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_google_credentials(credentials)
        except GoogleAuthError as exc:
            raise ImproperlyConfigured(
                "Google Calendar credentials could not be refreshed. "
                "Please reconnect the Google account."
            ) from exc

    if not credentials.valid:
        raise ImproperlyConfigured(
            "Google Calendar credentials are invalid. "
            "Please authorize the account again."
        )

    return credentials


def get_google_calendar_service():
    credentials = load_google_credentials()

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
