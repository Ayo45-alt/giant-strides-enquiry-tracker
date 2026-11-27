
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import Enquiry, Family, StageChange, StudentMeeting, Subject
from .google_meet_service import (
    MeetingGenerationError,
    MeetingGenerationPending,
    get_or_create_student_meeting,
)


class EnquiryInline(admin.TabularInline):
    model = Enquiry
    fields = ("child_name", "school_year", "status", "created", "is_archived")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class StageChangeInline(admin.TabularInline):
    model = StageChange
    fields = ("from_status", "to_status", "changed_at", "changed_by")
    readonly_fields = fields
    extra = 0
    can_delete = False
    ordering = ("changed_at",)

    def has_add_permission(self, request, obj=None):
        return False


class StudentMeetingInline(admin.StackedInline):
    model = StudentMeeting
    fields = (
        "status",
        "meet_url_link",
        "google_calendar_event_id",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields
    extra = 0
    max_num = 1
    can_delete = False
    verbose_name = "Student Google Meet"
    verbose_name_plural = "Student Google Meet"

    @admin.display(description="Google Meet link")
    def meet_url_link(self, obj):
        if not obj or not obj.meet_url:
            return "No meeting link generated yet."

        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">'
            'Open Google Meet</a>',
            obj.meet_url,
        )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = (
        "child_name",
        "family_parent_name",
        "family_parent_phone",
        "school_year",
        "submission_ref",
        "status",
        "status_changed_at",
        "assessment_date",
        "start_date",
        "not_proceeding_reason",
        "last_contacted",
        "next_followup",
        "is_archived",
    )
    list_filter = ("status", "school_year", "is_archived")
    search_fields = (
        "child_name",
        "family__parent_name",
        "family__parent_phone",
        "family__phone_normalized",
        "submission_ref",
    )
    list_editable = ("last_contacted", "next_followup")
    filter_horizontal = ("subjects",)
    ordering = ("next_followup",)
    inlines = (
        StageChangeInline,
        StudentMeetingInline,
    )
    change_form_template = (
        "admin/tracker/enquiry/change_form.html"
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("family")

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/generate-meet/",
                self.admin_site.admin_view(
                    self.generate_meet_view
                ),
                name="tracker_enquiry_generate_meet",
            ),
        ]

        return custom_urls + super().get_urls()

    def generate_meet_view(self, request, object_id):
        enquiry = get_object_or_404(
            Enquiry,
            pk=object_id,
        )

        if not self.has_change_permission(request, enquiry):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        change_url = reverse(
            "admin:tracker_enquiry_change",
            args=[enquiry.pk],
        )

        if request.method == "POST":
            if request.POST.get("confirm") != "yes":
                messages.info(
                    request,
                    "Google Meet generation was not confirmed.",
                )
                return HttpResponseRedirect(change_url)

            try:
                meeting = get_or_create_student_meeting(enquiry)

                if meeting.status == "ready" and meeting.meet_url:
                    messages.success(
                        request,
                        "The student's Google Meet link is ready. "
                        "Existing links are reused.",
                    )
                else:
                    messages.warning(
                        request,
                        "The meeting request did not return a ready link.",
                    )

            except MeetingGenerationPending as exc:
                messages.warning(request, str(exc))

            except MeetingGenerationError as exc:
                messages.error(
                    request,
                    f"Google Meet generation failed: {exc}",
                )

            return HttpResponseRedirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Generate Google Meet link",
            "enquiry": enquiry,
            "change_url": change_url,
        }

        return render(
            request,
            "admin/tracker/enquiry/generate_meet_confirmation.html",
            context,
        )

    @admin.display(description="Parent")
    def family_parent_name(self, obj):
        return obj.family.parent_name

    @admin.display(description="Phone")
    def family_parent_phone(self, obj):
        return obj.family.parent_phone

    def save_model(self, request, obj, form, change):
        if change:
            previous_status = (
                Enquiry.objects.filter(pk=obj.pk)
                .values_list("status", flat=True)
                .first()
            )
            if previous_status != obj.status:
                obj.change_status(obj.status, request.user)
                return
            obj.save(changed_by=request.user)
        else:
            obj.change_status(obj.status, request.user)


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = (
        "parent_name",
        "parent_phone",
        "parent_email",
        "country",
        "created",
    )
    search_fields = (
        "parent_name",
        "parent_phone",
        "phone_normalized",
        "parent_email",
    )
    readonly_fields = ("phone_normalized", "created")
    inlines = (EnquiryInline,)


@admin.register(StageChange)
class StageChangeAdmin(admin.ModelAdmin):
    list_display = (
        "enquiry",
        "from_status",
        "to_status",
        "changed_at",
        "changed_by",
    )
    list_filter = ("to_status", "changed_at")
    search_fields = (
        "enquiry__child_name",
        "enquiry__family__parent_name",
    )
    readonly_fields = (
        "enquiry",
        "from_status",
        "to_status",
        "changed_at",
        "changed_by",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
