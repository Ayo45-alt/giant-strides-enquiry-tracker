from datetime import datetime, time

from django import forms
from django.utils import timezone
from django.forms import formset_factory

from .models import Subject


DAYS = [
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]

TIME_ZONES = [
    ("Africa/Lagos", "Nigeria (WAT)"),
    ("Europe/London", "United Kingdom (GMT/BST)"),
    ("Europe/Berlin", "Central Europe (CET)"),
    ("America/New_York", "US / Canada Eastern"),
    ("America/Los_Angeles", "US / Canada Pacific"),
    ("Asia/Dubai", "Gulf (GST)"),
]
TIME_ZONE_LABELS = dict(TIME_ZONES)


def _time_label(value):
    return datetime.combine(datetime.today(), value).strftime("%I:%M %p").lstrip("0")


TIME_CHOICES = [("", "Select time")] + [
    (f"{hour:02d}:{minute:02d}", _time_label(time(hour, minute)))
    for hour in range(6, 22)
    for minute in (0, 30)
    if not (hour == 21 and minute == 30)
]
TIME_LABELS = dict(TIME_CHOICES)


class ParentEnquiryForm(forms.Form):
    parent_name = forms.CharField(
        max_length=150,
        label="Your full name (parent or guardian)",
        widget=forms.TextInput(attrs={"placeholder": "Enter your full name"}),
    )
    parent_phone = forms.CharField(
        max_length=30,
        label="Your phone number (WhatsApp)",
        widget=forms.TextInput(attrs={"placeholder": "With country code, e.g. +234"}),
    )
    parent_email = forms.EmailField(
        required=False,
        max_length=254,
        label="Your email address",
        widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}),
    )
    location = forms.CharField(
        max_length=150,
        label="Country",
        widget=forms.TextInput(attrs={"placeholder": "Country you live in"}),
    )
    time_zone = forms.ChoiceField(
        choices=TIME_ZONES,
        initial="Africa/Lagos",
        label="Your time zone (for class times)",
    )
    child_count = forms.ChoiceField(
        choices=[(str(count), str(count)) for count in range(1, 6)],
        initial="1",
        label="Number of children",
        widget=forms.Select(attrs={"class": "form-input child-count-select"}),
    )
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "class": "honeypot",
                "tabindex": "-1",
                "autocomplete": "off",
                "aria-hidden": "true",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in {"child_count", "website"}:
                field.widget.attrs["class"] = "form-input"


class ChildEnquiryForm(forms.Form):
    child_name = forms.CharField(
        max_length=150,
        label="Child's full name",
        widget=forms.TextInput(attrs={"placeholder": "First and last name"}),
    )
    school_year = forms.CharField(
        max_length=50,
        label="Child's current school year",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Year 5"}),
    )
    preferred_assessment_date = forms.DateField(
        required=False,
        label="Pick a date for the assessment",
        help_text="Our team will confirm the exact time with you.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    class_days = forms.MultipleChoiceField(
        required=False,
        choices=DAYS,
        label="Preferred class days",
        help_text="Tick every day that suits you.",
        widget=forms.CheckboxSelectMultiple,
    )
    class_time = forms.ChoiceField(
        required=False,
        choices=TIME_CHOICES,
        label="Preferred time for class",
        help_text="Each class is 1 hour. This time applies to every day you ticked.",
    )
    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=True,
        label="Subjects your child needs help with",
        error_messages={"required": "Please choose at least one subject."},
        widget=forms.CheckboxSelectMultiple,
    )

    field_order = [
        "child_name",
        "school_year",
        "subjects",
        "preferred_assessment_date",
        "class_days",
        "class_time",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in {"subjects", "class_days"}:
                field.widget.attrs["class"] = "form-input"
        self.fields["preferred_assessment_date"].widget.attrs["min"] = (
            timezone.localdate().isoformat()
        )

    def clean_preferred_assessment_date(self):
        value = self.cleaned_data.get("preferred_assessment_date")
        if value and value < timezone.localdate():
            raise forms.ValidationError("Please pick a date that is not in the past.")
        return value

    def clean(self):
        cleaned = super().clean()
        days = cleaned.get("class_days") or []
        chosen = cleaned.get("class_time")
        if days and not chosen:
            self.add_error("class_time", "Pick a start time for your class days.")
        schedule = {}
        if days and chosen:
            schedule = {key: chosen for key, _label in DAYS if key in days}
        cleaned["class_schedule"] = schedule
        return cleaned


def schedule_summary(schedule, time_zone=""):
    if not schedule:
        return ""
    day_labels = dict(DAYS)
    days = [day_labels[key] for key, _l in DAYS if key in schedule]
    value = next(iter(schedule.values()))
    text = f"{', '.join(days)} at {TIME_LABELS.get(value, value)}"
    zone = TIME_ZONE_LABELS.get(time_zone, time_zone)
    return f"{text} ({zone})" if zone else text


ChildEnquiryFormSet = formset_factory(
    ChildEnquiryForm,
    extra=5,
    max_num=5,
    validate_max=True,
)


def child_formset_data(post_data, selected_count, prefix="children"):
    data = post_data.copy()
    data[f"{prefix}-TOTAL_FORMS"] = str(selected_count)
    data[f"{prefix}-INITIAL_FORMS"] = "0"

    for index in range(selected_count, 5):
        for field_name in ChildEnquiryForm.base_fields:
            data.pop(f"{prefix}-{index}-{field_name}", None)

    return data
