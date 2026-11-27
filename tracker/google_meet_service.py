
import time
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from googleapiclient.errors import HttpError

from .google_calendar_auth import get_google_calendar_service
from .models import Enquiry, StudentMeeting


CONFERENCE_POLL_ATTEMPTS = 10
CONFERENCE_POLL_INTERVAL = 2


class MeetingGenerationError(Exception):
    """Raised when a Google Meet conference cannot be generated."""


class MeetingGenerationPending(MeetingGenerationError):
    """Raised when Google is still processing a conference request."""


def _get_or_create_meeting(enquiry):
    """
    Get the one meeting record belonging to an enquiry.

    A conditional database update assigns the Google event ID once.
    This also protects against concurrent requests on SQLite, where
    select_for_update() does not provide effective row locking.
    """

    try:
        meeting, _ = StudentMeeting.objects.get_or_create(
            enquiry=enquiry,
        )
    except IntegrityError:
        # Another request may have created the OneToOne record first.
        meeting = StudentMeeting.objects.get(enquiry=enquiry)

    if not meeting.google_calendar_event_id:
        candidate_event_id = uuid.uuid4().hex

        StudentMeeting.objects.filter(
            pk=meeting.pk,
            google_calendar_event_id="",
        ).update(
            google_calendar_event_id=candidate_event_id,
        )

        meeting.refresh_from_db()

    if not meeting.google_calendar_event_id:
        raise MeetingGenerationError(
            "Could not assign a Google Calendar event ID."
        )

    return meeting


def _build_calendar_event(enquiry, event_id):
    """
    Build one private, transparent monthly recurring event.

    The event has no attendees and does not use the calendar's
    default reminders.
    """

    event_date = timezone.localdate() + timedelta(days=1)

    student_reference = (
        enquiry.reference or f"ENQUIRY-{enquiry.pk}"
    )

    return {
        "id": event_id,
        "summary": f"Giant Strides Student Meeting - {student_reference}",
        "description": (
            "Private recurring meeting record maintained by "
            "Giant Strides Online School. "
            "This event is not a lesson booking."
        ),
        "start": {
            "date": event_date.isoformat(),
        },
        "end": {
            "date": (event_date + timedelta(days=1)).isoformat(),
        },
        "recurrence": [
            "RRULE:FREQ=MONTHLY",
        ],
        "visibility": "private",
        "transparency": "transparent",
        "reminders": {
            "useDefault": False,
        },
        "conferenceData": {
            "createRequest": {
                "requestId": f"gs-{event_id}",
                "conferenceSolutionKey": {
                    "type": "hangoutsMeet",
                },
            },
        },
    }


def _get_or_create_calendar_event(service, enquiry, meeting):
    """
    Retrieve the existing Google event or create it using its
    previously persisted event ID.
    """

    calendar_id = "primary"
    event_id = meeting.google_calendar_event_id

    try:
        return (
            service.events()
            .get(
                calendarId=calendar_id,
                eventId=event_id,
            )
            .execute()
        )

    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 404:
            raise

    event_body = _build_calendar_event(
        enquiry=enquiry,
        event_id=event_id,
    )

    try:
        return (
            service.events()
            .insert(
                calendarId=calendar_id,
                body=event_body,
                conferenceDataVersion=1,
            )
            .execute()
        )

    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 409:
            raise

        # A previous request may have created the event even if
        # its response was interrupted. Retrieve that same event.
        return (
            service.events()
            .get(
                calendarId=calendar_id,
                eventId=event_id,
            )
            .execute()
        )


def _extract_meet_url(event):
    """
    Extract the Google Meet video URL from conference data.
    """

    conference_data = event.get("conferenceData", {})

    for entry_point in conference_data.get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            uri = entry_point.get("uri", "")

            if uri.startswith("https://meet.google.com/"):
                return uri

    hangout_link = event.get("hangoutLink", "")

    if hangout_link.startswith("https://meet.google.com/"):
        return hangout_link

    return None


def _wait_for_meet_url(service, event_id):
    """
    Google generates conference details asynchronously.

    Poll the same Calendar event until the Meet URL is available.
    """

    for attempt in range(CONFERENCE_POLL_ATTEMPTS):
        event = (
            service.events()
            .get(
                calendarId="primary",
                eventId=event_id,
            )
            .execute()
        )

        conference_data = event.get("conferenceData", {})

        request_status = (
            conference_data
            .get("createRequest", {})
            .get("status", {})
            .get("statusCode")
        )

        meet_url = _extract_meet_url(event)

        if meet_url:
            return meet_url

        if request_status == "failure":
            raise MeetingGenerationError(
                "Google could not generate the Meet conference."
            )

        if attempt < CONFERENCE_POLL_ATTEMPTS - 1:
            time.sleep(CONFERENCE_POLL_INTERVAL)

    raise MeetingGenerationPending(
        "Google is still processing the Meet conference. "
        "Retry the request shortly. The existing Calendar event "
        "will be reused."
    )


def get_or_create_student_meeting(enquiry):
    """
    Return the one Google Meet meeting associated with an Enquiry.

    If no meeting exists, create one recurring Calendar event
    and generate its Google Meet conference.

    Repeated calls reuse the same StudentMeeting record and
    Google Calendar event.
    """

    if not isinstance(enquiry, Enquiry):
        raise TypeError(
            "enquiry must be an Enquiry model instance."
        )

    if not enquiry.pk:
        raise ValueError(
            "The enquiry must be saved before creating a meeting."
        )

    try:
        enquiry = Enquiry.objects.get(pk=enquiry.pk)
    except Enquiry.DoesNotExist as exc:
        raise ValueError(
            "The supplied enquiry does not exist."
        ) from exc

    meeting = _get_or_create_meeting(enquiry)

    # A ready meeting must never generate another conference.
    if meeting.status == "ready" and meeting.meet_url:
        return meeting

    try:
        service = get_google_calendar_service()

        event = _get_or_create_calendar_event(
            service=service,
            enquiry=enquiry,
            meeting=meeting,
        )

        if event.get("status") == "cancelled":
            raise MeetingGenerationError(
                "The student's existing Google Calendar event "
                "has been cancelled. Manual review is required."
            )

        meet_url = _extract_meet_url(event)

        if not meet_url:
            meet_url = _wait_for_meet_url(
                service=service,
                event_id=meeting.google_calendar_event_id,
            )

        meeting.meet_url = meet_url
        meeting.status = "ready"

        meeting.save(
            update_fields=[
                "meet_url",
                "status",
                "updated_at",
            ]
        )

        return meeting

    except MeetingGenerationError:
        raise

    except HttpError as exc:
        status_code = getattr(exc.resp, "status", "unknown")

        raise MeetingGenerationError(
            "Google Calendar API request failed "
            f"(HTTP {status_code})."
        ) from exc

    except Exception as exc:
        raise MeetingGenerationError(
            "An unexpected error occurred while generating "
            "the student's Google Meet link."
        ) from exc
