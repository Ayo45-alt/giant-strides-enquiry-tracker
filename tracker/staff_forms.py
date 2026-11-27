from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Enquiry


class StaffAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise ValidationError(
                "This account does not have staff access.",
                code="not_staff",
            )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": "form-input", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"class": "form-input", "autocomplete": "current-password"}
        )


class EnquiryTrackingForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = (
            "status",
            "assessment_date",
            "assessment_time",
            "tutor",
            "last_contacted",
            "next_followup",
            "notes",
        )
        widgets = {
            "assessment_date": forms.DateInput(attrs={"type": "date"}),
            "assessment_time": forms.TimeInput(attrs={"type": "time"}),
            "tutor": forms.TextInput(attrs={"placeholder": "Tutor name"}),
            "last_contacted": forms.DateInput(attrs={"type": "date"}),
            "next_followup": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"rows": 6, "placeholder": "Add notes about this enquiry"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"


class StageTransitionForm(forms.Form):
    stage_action = forms.ChoiceField(
        choices=(
            ("book_assessment", "Book assessment"),
            ("mark_assessed", "Mark assessed"),
            ("mark_started", "Mark started"),
            ("not_proceeding", "Not proceeding"),
            ("move_back", "Move back one stage"),
            ("reopen", "Reopen"),
        ),
        widget=forms.HiddenInput(),
    )
    assessment_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-input"}),
    )
    assessment_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-input"}),
    )
    tutor = forms.CharField(
        required=False,
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": "form-input", "placeholder": "Tutor name (optional)"}
        ),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-input"}),
    )
    reason = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "Optional reason",
                "maxlength": "200",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("assessment_date", timezone.localdate())
        initial.setdefault("start_date", timezone.localdate())
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get("stage_action")
        if action == "book_assessment" and not cleaned_data.get("assessment_date"):
            self.add_error("assessment_date", "Enter an assessment date.")
        elif action == "mark_started" and not cleaned_data.get("start_date"):
            self.add_error("start_date", "Enter a start date.")
        return cleaned_data
