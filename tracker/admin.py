from django.contrib import admin

from .models import Enquiry, Family, StageChange, Subject


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
    inlines = (StageChangeInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("family")

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
    list_display = ("parent_name", "parent_phone", "parent_email", "country", "created")
    search_fields = ("parent_name", "parent_phone", "phone_normalized", "parent_email")
    readonly_fields = ("phone_normalized", "created")
    inlines = (EnquiryInline,)


@admin.register(StageChange)
class StageChangeAdmin(admin.ModelAdmin):
    list_display = ("enquiry", "from_status", "to_status", "changed_at", "changed_by")
    list_filter = ("to_status", "changed_at")
    search_fields = ("enquiry__child_name", "enquiry__family__parent_name")
    readonly_fields = ("enquiry", "from_status", "to_status", "changed_at", "changed_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")