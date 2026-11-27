import json
from datetime import date, datetime, time, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import TruncMonth
from django.http import (
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponse,
    JsonResponse,
    QueryDict,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .context_processors import staff_navigation_counts
from .models import Enquiry, StageChange, Subject
from .staff_forms import (
    EnquiryTrackingForm,
    StageTransitionForm,
    StaffAuthenticationForm,
)


class StaffLoginView(LoginView):
    template_name = "tracker/staff/login.html"
    authentication_form = StaffAuthenticationForm


def _staff_list_url(query_string):
    url = reverse("staff:staff_list")
    return f"{url}?{query_string}" if query_string else url


def _filtered_query_string(query_data):
    params = QueryDict("", mutable=True)
    for key in (
        "status",
        "q",
        "subject",
        "sort",
        "direction",
        "page",
    ):
        value = query_data.get(key)
        if value:
            params[key] = value
    return params.urlencode()


def _month_start(year, month):
    return date(year, month, 1)


def _add_months(month_start, offset):
    month_number = month_start.year * 12 + month_start.month - 1 + offset
    return date(month_number // 12, month_number % 12 + 1, 1)


def _aware_month_start(month_start):
    return timezone.make_aware(
        datetime.combine(month_start, time.min),
        timezone.get_current_timezone(),
    )


def _selected_subject(subject_value):
    try:
        subject_id = int(subject_value)
    except (TypeError, ValueError):
        return None
    return Subject.objects.filter(pk=subject_id).first()


def _filtered_staff_enquiries(
    status,
    search,
    selected_subject,
    today,
    include_archived=False,
):
    enquiries = Enquiry.objects.all()
    if not include_archived:
        enquiries = enquiries.filter(is_archived=False)
    if status == "followups_due":
        five_days_ago = today - timedelta(days=5)
        enquiries = enquiries.filter(
            Q(
                next_followup__lte=today,
                status__in=("new", "assessment_booked", "assessed"),
            )
            | Q(status="new", status_changed_at__date__lte=five_days_ago)
        )
    elif status != "all":
        enquiries = enquiries.filter(status=status)
    if search:
        enquiries = enquiries.filter(
            Q(child_name__icontains=search)
            | Q(family__parent_name__icontains=search)
            | Q(family__parent_phone__icontains=search)
        )
    if selected_subject:
        enquiries = enquiries.filter(subjects=selected_subject)
    return (
        enquiries.select_related("family")
        .prefetch_related("subjects")
        .distinct()
    )


def _siblings_by_family(family_ids):
    siblings = {}
    if not family_ids:
        return siblings
    children = Enquiry.objects.filter(family_id__in=family_ids).only(
        "pk",
        "family_id",
        "child_name",
    ).order_by("child_name", "pk")
    for child in children:
        siblings.setdefault(child.family_id, []).append(child)
    return siblings


def _enquiry_workbook_response(enquiries, file_scope):
    headers = (
        "Reference",
        "Child",
        "School year",
        "Subjects",
        "Parent",
        "Phone",
        "Email",
        "Country",
        "Preferred assessment day",
        "Preferred class days and time",
        "Stage",
        "Received",
        "Assessment date",
        "Start date",
        "Next follow-up",
        "Last contacted",
        "Days waiting",
        "Siblings",
        "Not proceeding reason",
        "Notes",
    )
    column_widths = (
        12, 26, 16, 28, 26, 22, 32, 20, 28, 36, 20, 16, 18, 16, 18, 16, 14, 32, 28, 50
    )
    today = timezone.localdate()
    enquiries = list(enquiries.order_by("child_name", "pk"))
    siblings_by_family = _siblings_by_family(
        {enquiry.family_id for enquiry in enquiries}
    )
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Enquiries"
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    date_columns = (12, 13, 14, 15, 16)
    for enquiry in enquiries:
        siblings = [
            child.child_name
            for child in siblings_by_family.get(enquiry.family_id, ())
            if child.pk != enquiry.pk
        ]
        values = (
            enquiry.reference or None,
            enquiry.child_name,
            enquiry.school_year,
            ", ".join(subject.name for subject in enquiry.subjects.all()) or None,
            enquiry.family.parent_name,
            enquiry.family.parent_phone or None,
            enquiry.family.parent_email or None,
            enquiry.family.country or None,
            enquiry.assessment_day or None,
            enquiry.proposed_classes or None,
            enquiry.get_status_display(),
            timezone.localtime(enquiry.created).date(),
            enquiry.assessment_date,
            enquiry.start_date,
            enquiry.next_followup,
            enquiry.last_contacted,
            max(
                (today - timezone.localtime(enquiry.status_changed_at).date()).days,
                0,
            ),
            ", ".join(siblings) or None,
            enquiry.not_proceeding_reason or None,
            enquiry.notes or None,
        )
        worksheet.append(values)
        for cell in worksheet[worksheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
        worksheet.cell(row=worksheet.max_row, column=6).number_format = "@"
        for column in date_columns:
            cell = worksheet.cell(row=worksheet.max_row, column=column)
            if cell.value is not None:
                cell.number_format = "DD/MM/YYYY"
        worksheet.cell(row=worksheet.max_row, column=20).alignment = Alignment(
            wrap_text=True,
            vertical="top",
        )

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column, width in enumerate(column_widths, start=1):
        worksheet.column_dimensions[get_column_letter(column)].width = width

    output = BytesIO()
    workbook.save(output)
    filename = f"enquiries-{file_scope}-{today.isoformat()}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _transition_enquiry(enquiry, form, user):
    action = form.cleaned_data["stage_action"]
    allowed = {
        "book_assessment": enquiry.status == "new",
        "mark_assessed": enquiry.status in {"new", "assessment_booked"},
        "mark_started": enquiry.status == "assessed",
        "not_proceeding": enquiry.status in {"new", "assessment_booked", "assessed", "started"},
        "move_back": enquiry.status in {"assessment_booked", "assessed", "started"},
        "reopen": enquiry.status == "not_proceeding",
    }
    if not allowed.get(action, False):
        form.add_error(None, "This stage move is no longer available.")
        return False

    if action == "book_assessment":
        enquiry.assessment_date = form.cleaned_data["assessment_date"]
        enquiry.assessment_time = form.cleaned_data.get("assessment_time")
        enquiry.tutor = form.cleaned_data.get("tutor") or ""
        new_status = "assessment_booked"
    elif action == "mark_assessed":
        today = timezone.localdate()
        if form.cleaned_data.get("assessment_date"):
            enquiry.assessment_date = form.cleaned_data["assessment_date"]
        elif not enquiry.assessment_date:
            enquiry.assessment_date = today
        if not enquiry.next_followup:
            enquiry.next_followup = today + timedelta(days=3)
        new_status = "assessed"
    elif action == "mark_started":
        enquiry.start_date = form.cleaned_data["start_date"]
        new_status = "started"
    elif action == "not_proceeding":
        enquiry.not_proceeding_reason = form.cleaned_data["reason"]
        new_status = "not_proceeding"
    elif action == "move_back":
        back_map = {
            "assessment_booked": "new",
            "assessed": "assessment_booked",
            "started": "assessed",
        }
        new_status = back_map.get(enquiry.status, "new")
    else:
        new_status = "new"

    return enquiry.change_status(new_status, user)


def _apply_transition_post(request, enquiry):
    form = StageTransitionForm(request.POST)
    if not form.is_valid():
        return form, False
    return form, _transition_enquiry(enquiry, form, request.user)


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_list(request):
    today = timezone.localdate()
    five_days_ago = today - timedelta(days=5)
    due_filter = Q(
        next_followup__lte=today,
        status__in=("new", "assessment_booked", "assessed"),
    ) | Q(
        status="new",
        status_changed_at__date__lte=five_days_ago,
    )
    status = request.GET.get("status") or "new"
    valid_statuses = {value for value, _ in Enquiry.STATUS}
    if status not in valid_statuses | {"all", "followups_due"}:
        status = "new"

    search = request.GET.get("q", "").strip()
    subject_id = ""
    selected_subject = _selected_subject(request.GET.get("subject", ""))
    if selected_subject:
        subject_id = str(selected_subject.pk)

    stage_form = StageTransitionForm(initial={"assessment_date": today, "start_date": today})
    stage_enquiry_id = ""
    stage_dialog_open = False
    if request.method == "POST":
        if request.POST.get("stage_action"):
            stage_enquiry_id = request.POST.get("enquiry_id", "")
            enquiry = get_object_or_404(
                Enquiry.objects.select_related("family").prefetch_related("subjects"),
                pk=stage_enquiry_id,
            )
            stage_form, moved = _apply_transition_post(request, enquiry)
            if moved:
                messages.success(
                    request,
                    f"{enquiry.child_name} moved to {enquiry.get_status_display()}",
                )
                return _redirect_to_list(request.GET)
            stage_dialog_open = True
        else:
            return HttpResponseNotAllowed(["GET"])
    elif request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    enquiries = _filtered_staff_enquiries(
        status,
        search,
        selected_subject,
        today,
    )
    requested_sort = request.GET.get("sort", "")
    sort_key = requested_sort
    direction = request.GET.get("direction", "")
    sort_fields = {
        "child": "child_name",
        "parent": "family__parent_name",
        "status": "status",
        "received": "created",
        "assessment": "assessment_date",
        "time": "assessment_time",
        "tutor": "tutor",
        "next_followup": "next_followup",
        "started": "start_date",
        "moved": "status_changed_at",
        "reason": "not_proceeding_reason",
        "waiting": "status_changed_at",
    }
    if sort_key not in sort_fields:
        sort_key = {
            "new": "waiting",
            "assessment_booked": "assessment",
            "assessed": "next_followup",
            "started": "started",
            "not_proceeding": "moved",
            "followups_due": "next_followup",
            "all": "received",
        }.get(status, "waiting")
        direction = "desc" if sort_key in {"received", "started", "moved"} else "asc"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    field_name = sort_fields[sort_key]
    if sort_key == "waiting":
        enquiries = enquiries.order_by(
            F("status_changed_at").asc(nulls_last=True)
            if direction == "asc"
            else F("status_changed_at").desc(nulls_last=True),
            "-pk",
        )
    else:
        order_field = F(field_name)
        if direction == "desc":
            order_field = order_field.desc(nulls_last=True)
        else:
            order_field = order_field.asc(nulls_last=True)
        if not requested_sort and sort_key == "next_followup":
            enquiries = enquiries.order_by(
                order_field,
                "status_changed_at",
                "-pk",
            )
        else:
            enquiries = enquiries.order_by(order_field, "-pk")
    page_obj = Paginator(enquiries.distinct(), 25).get_page(request.GET.get("page"))
    page_enquiries = list(page_obj.object_list)
    for enquiry in page_enquiries:
        enquiry.days_in_stage = max(
            (today - timezone.localtime(enquiry.status_changed_at).date()).days,
            0,
        )
        enquiry.followup_due = bool(
            (
                enquiry.status in {"new", "assessment_booked", "assessed"}
                and enquiry.next_followup
                and enquiry.next_followup <= today
            )
            or (enquiry.status == "new" and enquiry.days_in_stage >= 5)
        )
        enquiry.waiting_too_long = bool(
            (enquiry.status in {"assessment_booked", "assessed"} and enquiry.days_in_stage >= 7)
            or (enquiry.status == "new" and enquiry.days_in_stage >= 5)
        )
        enquiry.days_overdue = (
            (today - enquiry.next_followup).days
            if (enquiry.next_followup and enquiry.next_followup <= today)
            else (enquiry.days_in_stage - 5 if enquiry.status == "new" and enquiry.days_in_stage >= 5 else 0)
        )
        enquiry.highlight = enquiry.followup_due or enquiry.waiting_too_long
    family_ids = {enquiry.family_id for enquiry in page_enquiries}
    siblings_by_family = _siblings_by_family(family_ids)
    for enquiry in page_enquiries:
        enquiry.siblings = [
            sibling
            for sibling in siblings_by_family.get(enquiry.family_id, ())
            if sibling.pk != enquiry.pk
        ]
    non_archived = Enquiry.objects.filter(is_archived=False)
    counts = non_archived.aggregate(
        all=Count("pk"),
        new=Count("pk", filter=Q(status="new")),
        assessment_booked=Count("pk", filter=Q(status="assessment_booked")),
        assessed=Count("pk", filter=Q(status="assessed")),
        started=Count("pk", filter=Q(status="started")),
        not_proceeding=Count("pk", filter=Q(status="not_proceeding")),
        followups_due=Count("pk", filter=due_filter),
    )
    legacy_received_params = ("received", "received_month", "received_year")
    query_params = request.GET.copy()
    for key in ("page",) + legacy_received_params:
        query_params.pop(key, None)
    filter_query = query_params.urlencode()
    stage_title = {
        "new": "New",
        "assessment_booked": "Assessment booked",
        "assessed": "Assessed",
        "started": "Started",
        "not_proceeding": "Not proceeding",
        "followups_due": "Follow-ups due",
        "all": "All enquiries",
    }.get(status, "New")
    sort_headers = []
    header_specs = {
        "new": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("received", "Received"),
            ("waiting", "Pending"),
        ),
        "assessment_booked": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("assessment", "Assessment date"),
            ("time", "Time"),
            ("waiting", "Pending"),
        ),
        "assessed": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("assessment", "Assessment date"),
            ("next_followup", "Next follow-up"),
            ("waiting", "Pending"),
        ),
        "started": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("assessment", "Assessment date"),
            ("started", "Start date"),
        ),
        "not_proceeding": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("moved", "Date moved"),
            ("reason", "Reason"),
        ),
        "followups_due": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("assessment", "Assessment date"),
            ("next_followup", "Next follow-up"),
            ("waiting", "Pending"),
            ("status", "Stage"),
        ),
        "all": (
            ("child", "Child"),
            ("parent", "Parent"),
            ("status", "Stage"),
            ("received", "Received"),
            ("waiting", "Pending"),
        ),
    }
    for key, label in header_specs.get(status, header_specs["new"]):
        params = request.GET.copy()
        for param in ("page",) + legacy_received_params:
            params.pop(param, None)
        params["sort"] = key
        params["direction"] = (
            "desc" if sort_key == key and direction == "asc" else "asc"
        )
        sort_headers.append(
            {
                "key": key,
                "label": label,
                "url": f"{reverse('staff:staff_list')}?{params.urlencode()}",
                "active": sort_key == key,
                "direction": direction,
            }
        )
    tabs = []
    for value, label, count_key in (
        ("new", "New", "new"),
        ("assessment_booked", "Assessment booked", "assessment_booked"),
        ("assessed", "Assessed", "assessed"),
        ("started", "Started", "started"),
        ("not_proceeding", "Not proceeding", "not_proceeding"),
        ("all", "All", "all"),
        ("followups_due", "Follow-ups due", "followups_due"),
    ):
        params = request.GET.copy()
        for param in ("page",) + legacy_received_params:
            params.pop(param, None)
        params["status"] = value
        tabs.append(
            {
                "label": label,
                "count": counts[count_key],
                "url": _staff_list_url(params.urlencode()),
                "active": status == value,
                "value": value,
            }
        )
    return render(
        request,
        "tracker/staff/enquiry_list.html",
        {
            "page_obj": page_obj,
            "enquiries": page_enquiries,
            "statuses": Enquiry.STATUS,
            "selected_status": status,
            "stage_title": stage_title,
            "stage_count": counts.get(status, counts["all"]),
            "sort_headers": sort_headers,
            "column_count": len(sort_headers) + 1,
            "sort_key": sort_key,
            "sort_direction": direction,
            "tabs": tabs,
            "search_query": search,
            "subjects": Subject.objects.all(),
            "selected_subject_id": subject_id,
            "filter_query": filter_query,
            "today": today,
            "stage_form": stage_form,
            "stage_enquiry_id": stage_enquiry_id,
            "stage_dialog_open": stage_dialog_open,
        },
    )


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_download(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    today = timezone.localdate()
    status = request.GET.get("status") or "new"
    valid_statuses = {value for value, _ in Enquiry.STATUS}
    if status not in valid_statuses | {"all", "followups_due"}:
        status = "new"
    enquiries = _filtered_staff_enquiries(
        status,
        request.GET.get("q", "").strip(),
        _selected_subject(request.GET.get("subject", "")),
        today,
    )
    return _enquiry_workbook_response(enquiries, status)


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_download_everything(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    enquiries = _filtered_staff_enquiries(
        "all",
        "",
        None,
        timezone.localdate(),
        include_archived=True,
    )
    return _enquiry_workbook_response(enquiries, "all")


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_enquiry_panel(request, pk):
    enquiry = get_object_or_404(
        Enquiry.objects.select_related("family").prefetch_related("subjects"),
        pk=pk,
    )
    if request.method == "GET":
        family_children = (
            Enquiry.objects.filter(family_id=enquiry.family_id)
            .exclude(pk=enquiry.pk)
            .order_by("child_name", "pk")
        )
        return JsonResponse(
            {
                "id": enquiry.pk,
                "child_name": enquiry.child_name,
                "status": enquiry.status,
                "family": {
                    "name": enquiry.family.parent_name,
                    "phone": enquiry.family.parent_phone,
                    "email": enquiry.family.parent_email,
                    "country": enquiry.family.country,
                },
                "subjects": [subject.name for subject in enquiry.subjects.all()],
                "school_year": enquiry.school_year,
                "reference": enquiry.reference,
                "assessment_day": enquiry.assessment_day,
                "proposed_classes": enquiry.proposed_classes,
                "received": timezone.localtime(enquiry.created).date().isoformat(),
                "last_contacted": (
                    enquiry.last_contacted.isoformat()
                    if enquiry.last_contacted
                    else ""
                ),
                "next_followup": (
                    enquiry.next_followup.isoformat()
                    if enquiry.next_followup
                    else ""
                ),
                "assessment_date": (
                    enquiry.assessment_date.isoformat()
                    if enquiry.assessment_date
                    else ""
                ),
                "assessment_time": (
                    enquiry.assessment_time.strftime("%H:%M")
                    if enquiry.assessment_time
                    else ""
                ),
                "tutor": enquiry.tutor or "",
                "start_date": (
                    enquiry.start_date.isoformat() if enquiry.start_date else ""
                ),
                "notes": enquiry.notes,
                "reason": enquiry.not_proceeding_reason,
                "siblings": [
                    {
                        "id": child.pk,
                        "name": child.child_name,
                        "status": child.get_status_display(),
                        "received": timezone.localtime(child.created)
                        .date()
                        .isoformat(),
                        "detail_url": reverse("staff:staff_detail", args=[child.pk]),
                        "panel_url": reverse(
                            "staff:staff_enquiry_panel",
                            args=[child.pk],
                        ),
                    }
                    for child in family_children
                ],
            }
        )
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid JSON.")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("Invalid request.")

    action = payload.get("action")
    if action == "edit":
        if "child_name" in payload and isinstance(payload["child_name"], str) and payload["child_name"].strip():
            enquiry.child_name = payload["child_name"].strip()
        if "school_year" in payload and isinstance(payload["school_year"], str):
            enquiry.school_year = payload["school_year"].strip()
        if "assessment_day" in payload and isinstance(payload["assessment_day"], str):
            enquiry.assessment_day = payload["assessment_day"].strip()
        if "proposed_classes" in payload and isinstance(payload["proposed_classes"], str):
            enquiry.proposed_classes = payload["proposed_classes"].strip()
        if "notes" in payload and isinstance(payload["notes"], str):
            enquiry.notes = payload["notes"].strip()
        if "not_proceeding_reason" in payload and isinstance(payload["not_proceeding_reason"], str):
            enquiry.not_proceeding_reason = payload["not_proceeding_reason"].strip()

        for date_field in ("assessment_date", "start_date", "next_followup", "last_contacted"):
            if date_field in payload:
                val = payload.get(date_field, "")
                if val:
                    try:
                        setattr(enquiry, date_field, date.fromisoformat(val))
                    except (TypeError, ValueError):
                        return JsonResponse({"error": f"Invalid date for {date_field}."}, status=400)
                else:
                    setattr(enquiry, date_field, None)

        if "assessment_time" in payload:
            time_val = payload.get("assessment_time", "")
            if time_val:
                try:
                    enquiry.assessment_time = time.fromisoformat(time_val)
                except (TypeError, ValueError):
                    return JsonResponse({"error": "Invalid time for assessment."}, status=400)
            else:
                enquiry.assessment_time = None

        with transaction.atomic():
            family = enquiry.family
            if "parent_name" in payload and isinstance(payload["parent_name"], str) and payload["parent_name"].strip():
                family.parent_name = payload["parent_name"].strip()
            if "parent_phone" in payload and isinstance(payload["parent_phone"], str):
                family.parent_phone = payload["parent_phone"].strip()
            if "parent_email" in payload and isinstance(payload["parent_email"], str):
                family.parent_email = payload["parent_email"].strip()
            if "country" in payload and isinstance(payload["country"], str):
                family.country = payload["country"].strip()
            family.save()

            enquiry.save()

            new_status = payload.get("status")
            if new_status and new_status in dict(Enquiry.STATUS) and new_status != enquiry.status:
                enquiry.change_status(new_status, request.user)

        return JsonResponse(
            {
                "saved": True,
                "message": f"Updated {enquiry.child_name} successfully",
                "navigation_counts": staff_navigation_counts(),
            }
        )

    if action in {"contact", "assessment"}:
        update_fields = []
        if "last_contacted" in payload or "next_followup" in payload:
            for field in ("last_contacted", "next_followup"):
                if field in payload:
                    value = payload.get(field, "")
                    if value:
                        try:
                            setattr(enquiry, field, date.fromisoformat(value))
                        except (TypeError, ValueError):
                            return JsonResponse({"error": "Enter a valid date."}, status=400)
                    else:
                        setattr(enquiry, field, None)
                    update_fields.append(field)

        if "assessment_date" in payload or action == "assessment":
            date_val = payload.get("assessment_date", "")
            if date_val:
                try:
                    enquiry.assessment_date = date.fromisoformat(date_val)
                except (TypeError, ValueError):
                    return JsonResponse({"error": "Enter a valid date."}, status=400)
            else:
                enquiry.assessment_date = None
            update_fields.append("assessment_date")

        if "assessment_time" in payload or action == "assessment":
            time_val = payload.get("assessment_time", "")
            if time_val:
                try:
                    enquiry.assessment_time = time.fromisoformat(time_val)
                except (TypeError, ValueError):
                    return JsonResponse({"error": "Enter a valid time."}, status=400)
            else:
                enquiry.assessment_time = None
            update_fields.append("assessment_time")

        if "tutor" in payload or action == "assessment":
            tutor_val = payload.get("tutor", "")
            if not isinstance(tutor_val, str):
                return JsonResponse({"error": "Tutor must be text."}, status=400)
            if len(tutor_val) > 150:
                return JsonResponse({"error": "Tutor must be 150 characters or fewer."}, status=400)
            enquiry.tutor = tutor_val
            update_fields.append("tutor")

        if update_fields:
            with transaction.atomic():
                enquiry.save(update_fields=set(update_fields))
        return JsonResponse(
            {"saved": True, "navigation_counts": staff_navigation_counts()}
        )

    if action == "notes":
        value = payload.get("value", "")
        if not isinstance(value, str):
            return JsonResponse({"error": "Notes must be text."}, status=400)
        enquiry.notes = value
        enquiry.save(update_fields=("notes",))
        return JsonResponse(
            {"saved": True, "navigation_counts": staff_navigation_counts()}
        )

    if action in {"stage", "reopen"}:
        with transaction.atomic():
            if enquiry.status == "new":
                enquiry.assessment_date = timezone.localdate()
                enquiry.save(update_fields=("assessment_date",))
                new_status = "assessment_booked"
            elif enquiry.status == "assessment_booked":
                today = timezone.localdate()
                if not enquiry.next_followup:
                    enquiry.next_followup = today + timedelta(days=3)
                    enquiry.save(update_fields=("next_followup",))
                new_status = "assessed"
            elif enquiry.status == "assessed":
                enquiry.start_date = timezone.localdate()
                enquiry.save(update_fields=("start_date",))
                new_status = "started"
            elif action == "reopen" and enquiry.status == "not_proceeding":
                new_status = "new"
            else:
                return JsonResponse(
                    {"error": "This stage move is unavailable."}, status=400
                )
            enquiry.change_status(new_status, request.user)
        return JsonResponse(
            {
                "moved": True,
                "message": f"{enquiry.child_name} moved to {enquiry.get_status_display()}",
                "navigation_counts": staff_navigation_counts(),
            }
        )

    if action == "not_proceeding":
        if enquiry.status == "not_proceeding":
            return JsonResponse({"error": "This enquiry is already not proceeding."}, status=400)
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            return JsonResponse({"error": "Enter a valid reason."}, status=400)
        if len(reason) > 200:
            return JsonResponse(
                {"error": "The reason must be 200 characters or fewer."},
                status=400,
            )
        with transaction.atomic():
            enquiry.not_proceeding_reason = reason
            enquiry.save(update_fields=("not_proceeding_reason",))
            enquiry.change_status("not_proceeding", request.user)
        return JsonResponse(
            {
                "moved": True,
                "message": f"{enquiry.child_name} moved to Not proceeding",
                "navigation_counts": staff_navigation_counts(),
            }
        )

    return JsonResponse({"error": "Unknown update."}, status=400)


def _redirect_to_list(query_data):
    return redirect(_staff_list_url(_filtered_query_string(query_data)))


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_detail(request, pk):
    enquiry = get_object_or_404(
        Enquiry.objects.select_related("family").prefetch_related("subjects"),
        pk=pk,
    )
    filter_query = _filtered_query_string(request.GET)
    stage_form = StageTransitionForm(
        initial={
            "assessment_date": timezone.localdate(),
            "start_date": timezone.localdate(),
        }
    )
    stage_dialog_open = False
    if request.method == "POST":
        if request.POST.get("stage_action"):
            stage_form, moved = _apply_transition_post(request, enquiry)
            if moved:
                messages.success(
                    request,
                    f"{enquiry.child_name} moved to {enquiry.get_status_display()}",
                )
                return _redirect_to_list(request.GET)
            stage_dialog_open = True
            form = EnquiryTrackingForm(instance=enquiry)
        else:
            previous_status = enquiry.status
            form = EnquiryTrackingForm(request.POST, instance=enquiry)
            if form.is_valid():
                enquiry = form.save(commit=False)
                submitted_status = form.cleaned_data["status"]
                if submitted_status != previous_status:
                    enquiry.status = previous_status
                    enquiry.save()
                    enquiry.change_status(submitted_status, request.user)
                else:
                    form.save()
                messages.success(request, "Enquiry updated successfully.")
                return _redirect_to_list(request.GET)
    else:
        form = EnquiryTrackingForm(instance=enquiry)
    today = timezone.localdate()
    days_in_stage = max(
        (today - timezone.localtime(enquiry.status_changed_at).date()).days,
        0,
    )

    return render(
        request,
        "tracker/staff/enquiry_detail.html",
        {
            "enquiry": enquiry,
            "form": form,
            "filter_query": filter_query,
            "days_in_stage": days_in_stage,
            "stage_form": stage_form,
            "stage_enquiry_id": enquiry.pk,
            "stage_dialog_open": stage_dialog_open,
        },
    )


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_logout(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    return redirect("staff:staff_login")


@staff_member_required(login_url=settings.LOGIN_URL)
def staff_report(request):
    today = timezone.localdate()
    current_month = _month_start(today.year, today.month)
    end = _add_months(current_month, 1)
    start = _add_months(current_month, -11)
    start_at = _aware_month_start(start)
    end_at = _aware_month_start(end)
    month_rows = []
    month_lookup = {}
    cursor = start
    while cursor < end:
        row = {
            "month": cursor,
            "received": 0,
            "assessed": 0,
            "started": 0,
            "not_proceeding": 0,
            "conversion": 0,
        }
        month_rows.append(row)
        month_lookup[(cursor.year, cursor.month)] = row
        cursor = _add_months(cursor, 1)

    received_counts = (
        Enquiry.objects.filter(created__gte=start_at, created__lt=end_at)
        .annotate(month=TruncMonth("created", tzinfo=timezone.get_current_timezone()))
        .values("month")
        .annotate(total=Count("pk"))
    )
    for result in received_counts:
        month_key = (result["month"].year, result["month"].month)
        if month_key in month_lookup:
            month_lookup[month_key]["received"] = result["total"]

    stage_counts = (
        StageChange.objects.filter(changed_at__gte=start_at, changed_at__lt=end_at)
        .annotate(
            month=TruncMonth("changed_at", tzinfo=timezone.get_current_timezone())
        )
        .values("month", "to_status")
        .annotate(total=Count("pk"))
    )
    for result in stage_counts:
        month_key = (result["month"].year, result["month"].month)
        row = month_lookup.get(month_key)
        if row and result["to_status"] in {
            "assessed",
            "started",
            "not_proceeding",
        }:
            row[result["to_status"]] = result["total"]

    for row in month_rows:
        if row["received"]:
            row["conversion"] = round(row["started"] * 100 / row["received"], 1)

    this_month = month_lookup[(current_month.year, current_month.month)]
    last_month_start = _add_months(current_month, -1)
    last_month = month_lookup[(last_month_start.year, last_month_start.month)]
    return render(
        request,
        "tracker/staff/report.html",
        {
            "months": month_rows,
            "this_month": this_month,
            "last_month": last_month,
        },
    )
