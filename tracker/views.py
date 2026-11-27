from uuid import uuid4

from django.db import transaction
from django.shortcuts import redirect, render

from .forms import (
    ChildEnquiryFormSet,
    ParentEnquiryForm,
    child_formset_data,
    schedule_summary,
)
from .models import Enquiry, Family, normalize_phone_number


WHATSAPP_NUMBER = "2349073141035"


def enquiry_create(request):
    if request.method == "POST":
        if request.POST.get("website", "").strip():
            request.session.pop("submitted_child_names", None)
            return redirect("tracker:thank_you")

        parent_form = ParentEnquiryForm(request.POST)
        try:
            selected_count = int(request.POST.get("child_count", "1"))
        except (TypeError, ValueError):
            selected_count = 1
        if selected_count not in range(1, 6):
            selected_count = 1

        children = ChildEnquiryFormSet(
            child_formset_data(request.POST, selected_count),
            prefix="children",
        )
        for child_form in children.forms:
            child_form.empty_permitted = False

        parent_valid = parent_form.is_valid()
        children_valid = children.is_valid()
        if parent_valid and children_valid:
            parent_data = parent_form.cleaned_data
            child_data = []
            for form in children.forms[:selected_count]:
                cleaned_data = form.cleaned_data
                if not cleaned_data:
                    continue
                child_name = cleaned_data.get("child_name")
                school_year = cleaned_data.get("school_year")
                if not child_name or not school_year:
                    continue
                child_data.append(
                    {
                        "child_name": child_name,
                        "school_year": school_year,
                        "preferred_assessment_date": cleaned_data.get(
                            "preferred_assessment_date"
                        ),
                        "class_schedule": cleaned_data.get("class_schedule", {}),
                        "subjects": cleaned_data.get("subjects", []),
                    }
                )
            submission_ref = str(uuid4())

            with transaction.atomic():
                normalized_phone = normalize_phone_number(
                    parent_data.get("parent_phone", "")
                )
                family = None
                if normalized_phone:
                    family = (
                        Family.objects.filter(phone_normalized=normalized_phone)
                        .order_by("pk")
                        .first()
                    )

                if family is None:
                    family = Family.objects.create(
                        parent_name=parent_data.get("parent_name", ""),
                        parent_phone=parent_data.get("parent_phone", ""),
                        parent_email=parent_data.get("parent_email", ""),
                        country=parent_data.get("location", ""),
                    )
                else:
                    changed_fields = []
                    if not family.parent_email and parent_data.get("parent_email"):
                        family.parent_email = parent_data["parent_email"]
                        changed_fields.append("parent_email")
                    if not family.country and parent_data.get("location"):
                        family.country = parent_data["location"]
                        changed_fields.append("country")
                    if changed_fields:
                        family.save(update_fields=changed_fields)

                for child in child_data:
                    enquiry = Enquiry(
                        family=family,
                        child_name=child.get("child_name", ""),
                        school_year=child.get("school_year", ""),
                        preferred_assessment_date=child.get("preferred_assessment_date"),
                        assessment_day=(
                            f"{child['preferred_assessment_date']:%a} "
                            f"{child['preferred_assessment_date'].day} "
                            f"{child['preferred_assessment_date']:%b %Y}"
                            if child.get("preferred_assessment_date")
                            else ""
                        ),
                        class_schedule=child.get("class_schedule", {}),
                        time_zone=parent_data.get("time_zone", ""),
                        proposed_classes=schedule_summary(
                            child.get("class_schedule", {}),
                            parent_data.get("time_zone", ""),
                        ),
                        submission_ref=submission_ref,
                    )
                    enquiry.change_status("new")
                    enquiry.subjects.set(child.get("subjects", []))

            request.session["submitted_child_names"] = [
                child.get("child_name", "") for child in child_data
            ]
            return redirect("tracker:thank_you")
    else:
        parent_form = ParentEnquiryForm()
        children = ChildEnquiryFormSet(prefix="children")
        selected_count = 1
        for child_form in children.forms[selected_count:]:
            child_form.empty_permitted = True

    return render(
        request,
        "tracker/enquiry_form.html",
        {
            "parent_form": parent_form,
            "parent_fields": [
                parent_form[name]
                for name in (
                    "parent_name",
                    "parent_phone",
                    "parent_email",
                    "location",
                    "time_zone",
                )
            ],
            "child_count_field": parent_form["child_count"],
            "honeypot_field": parent_form["website"],
            "children": children,
            "selected_count": selected_count,
            "whatsapp_number": WHATSAPP_NUMBER,
        },
    )


def thank_you(request):
    child_names = request.session.pop("submitted_child_names", [])
    if len(child_names) == 1:
        names_display = child_names[0]
    elif child_names:
        names_display = f"{', '.join(child_names[:-1])} and {child_names[-1]}"
    else:
        names_display = ""

    return render(
        request,
        "tracker/thank_you.html",
        {"names_display": names_display},
    )
