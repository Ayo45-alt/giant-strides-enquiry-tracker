import json
import uuid
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import Enquiry, Family, StageChange, Subject


class PublicEnquiryFormTests(TestCase):
    def submission(self, count=1, **overrides):
        data = {
            "parent_name": "Taylor Example",
            "parent_phone": "555-0102",
            "parent_email": "taylor@example.com",
            "location": "Westside",
            "time_zone": "Africa/Lagos",
            "child_count": str(count),
            "website": "",
            "children-TOTAL_FORMS": str(count),
            "children-INITIAL_FORMS": "0",
            "children-MIN_NUM_FORMS": "0",
            "children-MAX_NUM_FORMS": "5",
        }
        for index in range(count):
            data.update(
                {
                    f"children-{index}-child_name": f"Child {index + 1}",
                    f"children-{index}-school_year": f"Year {index + 1}",
                    f"children-{index}-preferred_assessment_date": (
                        timezone.localdate() + timedelta(days=3)
                    ).isoformat(),
                    f"children-{index}-class_days": ["mon", "wed"],
                    f"children-{index}-class_time": "16:00",
                    f"children-{index}-subjects": [
                        str(Subject.objects.get(name="Maths").pk)
                    ],
                }
            )
        data.update(overrides)
        return data

    def test_schedule_reference_and_time_zone_are_saved(self):
        response = self.client.post(reverse("tracker:enquiry_create"), self.submission())
        self.assertEqual(response.status_code, 302)
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.reference, f"GS-{enquiry.pk:04d}")
        self.assertEqual(enquiry.class_schedule, {"mon": "16:00", "wed": "16:00"})
        self.assertEqual(enquiry.time_zone, "Africa/Lagos")
        self.assertEqual(
            enquiry.proposed_classes, "Mon, Wed at 4:00 PM (Nigeria (WAT))"
        )
        self.assertEqual(
            enquiry.preferred_assessment_date, timezone.localdate() + timedelta(days=3)
        )

    def test_ticked_day_needs_a_time(self):
        data = self.submission(**{"children-0-class_time": ""})
        response = self.client.post(reverse("tracker:enquiry_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pick a start time for your class days.")
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_past_assessment_date_is_rejected(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        data = self.submission(**{"children-0-preferred_assessment_date": yesterday})
        response = self.client.post(reverse("tracker:enquiry_create"), data)
        self.assertContains(response, "not in the past")
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_schedule_is_optional(self):
        data = self.submission(**{"children-0-class_days": [], "children-0-preferred_assessment_date": ""})
        response = self.client.post(reverse("tracker:enquiry_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Enquiry.objects.get().class_schedule, {})

    def test_form_has_parent_section_child_count_and_five_child_forms(self):
        response = self.client.get(reverse("tracker:enquiry_create"))
        html = response.content.decode()

        self.assertContains(response, "Enquire About Classes")
        self.assertContains(response, "Your details")
        self.assertContains(response, "Your child's details")
        self.assertContains(response, "Number of children")
        self.assertContains(response, "Choose 1 if it is just one child.")
        self.assertContains(response, "Country")
        self.assertContains(response, "Country you live in")
        self.assertNotContains(response, "Town, city or neighbourhood")
        self.assertContains(response, "Subjects your child needs help with")
        self.assertContains(response, "English")
        self.assertContains(response, "Sciences")
        self.assertContains(response, 'name="website"')
        self.assertContains(response, "honeypot-field")
        self.assertContains(response, 'name="children-TOTAL_FORMS" value="5"')
        self.assertEqual(html.count('<section class="child-panel'), 5)
        self.assertNotContains(response, "Details for Child 1")
        self.assertNotContains(response, "Details for Child 2")
        self.assertNotContains(response, "Admin Portal")
        self.assertNotContains(response, "All Enquiries")

    def test_multiple_children_share_a_submission_reference_and_redirect_to_thank_you(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(
                count=2,
                **{
                    "children-0-child_name": "Tade",
                    "children-1-child_name": "Bisi",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tracker:thank_you"))
        enquiries = list(Enquiry.objects.order_by("pk"))
        self.assertEqual(len(enquiries), 2)
        self.assertEqual([enquiry.child_name for enquiry in enquiries], ["Tade", "Bisi"])
        self.assertEqual(
            {enquiry.family.parent_name for enquiry in enquiries},
            {"Taylor Example"},
        )
        self.assertEqual(
            {enquiry.family.parent_phone for enquiry in enquiries},
            {"555-0102"},
        )
        self.assertEqual(
            {enquiry.family.parent_email for enquiry in enquiries},
            {"taylor@example.com"},
        )
        self.assertEqual({enquiry.family.country for enquiry in enquiries}, {"Westside"})
        self.assertEqual({enquiry.status for enquiry in enquiries}, {"new"})
        self.assertEqual(
            {tuple(enquiry.subjects.values_list("name", flat=True)) for enquiry in enquiries},
            {("Maths",)},
        )
        refs = {enquiry.submission_ref for enquiry in enquiries}
        self.assertEqual(len(refs), 1)
        self.assertEqual(uuid.UUID(refs.pop()).version, 4)

        thank_you = self.client.get(reverse("tracker:thank_you"))
        self.assertContains(thank_you, "We have received details for: Tade and Bisi")
        self.assertContains(thank_you, "Submit another enquiry")
        self.assertNotContains(thank_you, "admin")

    def test_child_names_use_commas_and_and_before_the_last_name(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(
                count=3,
                **{
                    "children-0-child_name": "Tade",
                    "children-1-child_name": "Bisi",
                    "children-2-child_name": "Kemi",
                },
            ),
            follow=True,
        )

        self.assertContains(response, "We have received details for: Tade, Bisi and Kemi")
        self.assertEqual(Enquiry.objects.count(), 3)

    def test_only_selected_child_forms_are_validated_and_saved(self):
        data = self.submission(
            count=1,
            **{
                "children-0-child_name": "Only Child",
                "children-1-child_name": "",
                "children-1-school_year": "",
            },
        )
        response = self.client.post(reverse("tracker:enquiry_create"), data)

        self.assertRedirects(response, reverse("tracker:thank_you"))
        self.assertEqual(Enquiry.objects.count(), 1)
        self.assertEqual(Enquiry.objects.get().child_name, "Only Child")

    def test_invalid_selected_child_shows_error_and_preserves_count_and_values(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(
                count=2,
                **{
                    "children-0-child_name": "",
                    "children-1-child_name": "Bisi",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Enquiry.objects.count(), 0)
        self.assertContains(response, "This field is required.")
        self.assertContains(response, 'value="2" selected')
        self.assertContains(response, 'value="Bisi"')
        self.assertContains(response, "Details for Child 1")
        self.assertContains(response, "Details for Child 2")
        self.assertEqual(response.content.decode().count('<section class="child-panel'), 2)

    def test_two_children_with_second_child_blank_shows_errors_and_creates_nothing(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(
                count=2,
                **{
                    "children-0-child_name": "Tade",
                    "children-1-child_name": "",
                    "children-1-school_year": "",
                    "children-1-subjects": [],
                },
            ),
        )

        children = response.context["children"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(children.total_form_count(), 2)
        self.assertEqual(len(children.forms), 2)
        self.assertIn("This field is required.", children.forms[1].errors["child_name"])
        self.assertIn("This field is required.", children.forms[1].errors["school_year"])
        self.assertIn(
            "Please choose at least one subject.",
            children.forms[1].errors["subjects"],
        )
        self.assertContains(response, 'name="children-TOTAL_FORMS" value="2"')
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_filled_honeypot_is_discarded_and_thank_you_has_fallback_text(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(website="spam"),
        )

        self.assertRedirects(response, reverse("tracker:thank_you"))
        self.assertEqual(Enquiry.objects.count(), 0)
        self.assertContains(
            self.client.get(reverse("tracker:thank_you")),
            "We have received your details. Our team will contact you shortly to arrange your child's assessment.",
        )

    def test_thank_you_reads_and_consumes_names_from_session(self):
        session = self.client.session
        session["submitted_child_names"] = ["Previous Child"]
        session.save()
        self.client.cookies["sessionid"] = session.session_key

        response = self.client.get(reverse("tracker:thank_you"))

        self.assertContains(response, "Previous Child")
        self.assertNotIn("submitted_child_names", self.client.session)

    def test_child_requires_at_least_one_active_subject(self):
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(
                **{"children-0-subjects": []},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please choose at least one subject.")
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_inactive_subject_is_not_available_on_parent_form(self):
        Subject.objects.create(name="Archived topic", is_active=False)

        response = self.client.get(reverse("tracker:enquiry_create"))

        self.assertNotContains(response, "Archived topic")

    def test_submissions_with_normalized_matching_phone_share_a_family(self):
        first = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(count=1),
        )
        self.assertEqual(first.status_code, 302)
        first_enquiry = Enquiry.objects.get()
        second_data = self.submission(
            count=1,
            parent_phone="(555) 0102",
            parent_email="",
        )
        second_data["children-0-child_name"] = "Second Child"

        response = self.client.post(reverse("tracker:enquiry_create"), second_data)

        self.assertEqual(response.status_code, 302)
        second_enquiry = Enquiry.objects.get(child_name="Second Child")
        self.assertEqual(second_enquiry.family_id, first_enquiry.family_id)
        self.assertEqual(second_enquiry.family.parent_email, "taylor@example.com")
        self.assertEqual(second_enquiry.family.country, "Westside")

    def test_submission_fills_blank_family_email_and_country(self):
        family = Family.objects.create(
            parent_name="Taylor Example",
            parent_phone="555-0102",
            parent_email="",
            country="",
        )
        response = self.client.post(
            reverse("tracker:enquiry_create"),
            self.submission(),
        )

        self.assertEqual(response.status_code, 302)
        family.refresh_from_db()
        self.assertEqual(family.parent_email, "taylor@example.com")
        self.assertEqual(family.country, "Westside")
        self.assertEqual(Enquiry.objects.get().family_id, family.pk)


class StaffAreaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.staff = user_model.objects.create_user(
            username="staff",
            password="staff-password",
            is_staff=True,
        )
        cls.nonstaff = user_model.objects.create_user(
            username="parent",
            password="parent-password",
        )
        cls.maths = Subject.objects.get(name="Maths")

    def create_enquiry(self, child_name, **overrides):
        family = overrides.pop("family", None)
        family_values = {
            "parent_name": overrides.pop("parent_name", "Taylor Parent"),
            "parent_phone": overrides.pop("parent_phone", "555-0102"),
            "parent_email": overrides.pop("parent_email", "taylor@example.com"),
            "country": overrides.pop(
                "country",
                overrides.pop("location", "Westside"),
            ),
        }
        if family is None:
            family = Family.objects.create(**family_values)
        values = {
            "school_year": "Year 5",
        }
        values.update(overrides)
        enquiry = Enquiry.objects.create(family=family, child_name=child_name, **values)
        enquiry.subjects.add(self.maths)
        return enquiry

    def test_staff_list_requires_staff_and_login_rejects_nonstaff(self):
        response = self.client.get(reverse("staff:staff_list"))
        self.assertRedirects(
            response,
            f"{reverse('staff:staff_login')}?next={reverse('staff:staff_list')}",
        )
        self.assertRedirects(
            self.client.get(reverse("staff:staff_download")),
            f"{reverse('staff:staff_login')}?next={reverse('staff:staff_download')}",
        )
        self.assertRedirects(
            self.client.get(reverse("staff:staff_download_everything")),
            f"{reverse('staff:staff_login')}?next={reverse('staff:staff_download_everything')}",
        )

        response = self.client.post(
            reverse("staff:staff_login"),
            {"username": "parent", "password": "parent-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This account does not have staff access.")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_staff_can_sign_in_and_logout_only_with_post(self):
        response = self.client.post(
            reverse("staff:staff_login"),
            {"username": "staff", "password": "staff-password"},
        )
        self.assertRedirects(response, reverse("staff:staff_list"))

        self.assertEqual(
            self.client.get(reverse("staff:staff_logout")).status_code,
            405,
        )
        response = self.client.post(reverse("staff:staff_logout"))
        self.assertRedirects(response, reverse("staff:staff_login"))

    def test_list_filters_by_status_search_and_subject_and_highlights_due(self):
        due = self.create_enquiry(
            "Due Child",
            status="new",
            next_followup=timezone.localdate(),
        )
        self.create_enquiry(
            "Started Child",
            status="started",
            next_followup=timezone.localdate() - timedelta(days=1),
        )
        self.create_enquiry(
            "Other Parent Child",
            status="assessed",
            parent_name="Different Parent",
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("staff:staff_list"),
            {
                "status": "followups_due",
                "q": "Taylor",
                "subject": self.maths.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context["enquiries"]], [due.pk])
        self.assertContains(response, "Due Child")
        self.assertNotContains(response, "Started Child")
        self.assertEqual(response.context["tabs"][-2]["count"], 3)

    def test_list_defaults_to_new_and_renders_stage_layout_and_actions(self):
        enquiry = self.create_enquiry("New Child")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("staff:staff_list"))

        self.assertEqual(response.context["selected_status"], "new")
        self.assertEqual([item.pk for item in response.context["enquiries"]], [enquiry.pk])
        self.assertContains(response, "New")
        self.assertContains(response, "Download")
        self.assertContains(response, "Received")
        header_keys = [header["key"] for header in response.context["sort_headers"]]
        self.assertNotIn("next_followup", header_keys)
        self.assertNotIn("last_contacted", header_keys)
        self.assertContains(response, "Pending")
        self.assertContains(response, "Book assessment")
        self.assertEqual(response.context["enquiries"][0].siblings, [])
        self.assertNotContains(response, 'class="staff-sibling-line"')
        self.assertContains(response, "Not proceeding reason")
        self.assertContains(response, "1 enquiry")
        self.assertContains(response, "Assessment booked")
        self.assertNotContains(response, '<nav class="staff-status-tabs"')

    def test_list_shows_school_year_and_linked_sibling_names(self):
        family = Family.objects.create(
            parent_name="Taylor Parent",
            parent_phone="555-0102",
        )
        current = self.create_enquiry("Zara", family=family, school_year="Year 11")
        amara = self.create_enquiry("Amara", family=family)
        bayo = self.create_enquiry("Bayo", family=family)
        chidi = self.create_enquiry("Chidi", family=family)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("staff:staff_list"))

        self.assertContains(response, "Year 11")
        self.assertContains(
            response,
            f'Sibling of <a href="{reverse("staff:staff_detail", args=[amara.pk])}" '
            'data-sibling-panel',
        )
        self.assertContains(
            response,
            f'<a href="{reverse("staff:staff_detail", args=[amara.pk])}" '
            f'data-sibling-panel data-enquiry-id="{amara.pk}"',
        )
        self.assertContains(
            response,
            f'<a href="{reverse("staff:staff_detail", args=[bayo.pk])}" '
            f'data-sibling-panel data-enquiry-id="{bayo.pk}"',
        )
        self.assertContains(
            response,
            f'</a>, <a href="{reverse("staff:staff_detail", args=[bayo.pk])}"',
        )
        self.assertContains(
            response,
            f'</a> and <a href="{reverse("staff:staff_detail", args=[chidi.pk])}"',
        )
        self.assertNotContains(response, 'class="sibling-tag"')
        self.assertContains(response, "4 enquiries")
        self.assertContains(response, "What the parent submitted")
        self.assertContains(response, "tel:")
        self.assertContains(response, "mailto:")
        panel = self.client.get(reverse("staff:staff_enquiry_panel", args=[current.pk]))
        self.assertEqual(
            [sibling["id"] for sibling in panel.json()["siblings"]],
            [amara.pk, bayo.pk, chidi.pk],
        )
        self.assertTrue(panel.json()["siblings"][0]["panel_url"])
        self.assertTrue(panel.json()["siblings"][0]["detail_url"])

    def test_legacy_all_status_filter_renders_combined_rows(self):
        new_enquiry = self.create_enquiry("New Combined")
        started = self.create_enquiry(
            "Started Combined",
            status="started",
            assessment_date=date(2026, 11, 10),
            start_date=date(2026, 11, 12),
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("staff:staff_list"),
            {"status": "all"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item.pk for item in response.context["enquiries"]},
            {new_enquiry.pk, started.pk},
        )
        self.assertContains(response, "Stage")
        self.assertContains(response, "New Combined")
        self.assertContains(response, "Started Combined")

    def test_stage_lists_use_requested_columns_and_only_available_actions(self):
        assessments = self.create_enquiry(
            "Assessed Child",
            status="assessed",
            assessment_date=timezone.localdate(),
            next_followup=timezone.localdate(),
        )
        started = self.create_enquiry(
            "Started Child",
            status="started",
            assessment_date=timezone.localdate() - timedelta(days=2),
            start_date=timezone.localdate() - timedelta(days=1),
        )
        not_proceeding = self.create_enquiry(
            "Not proceeding Child",
            status="not_proceeding",
            not_proceeding_reason="Timing",
        )
        booked = self.create_enquiry(
            "Booked Child",
            status="assessment_booked",
            assessment_date=timezone.localdate(),
        )
        self.create_enquiry("Due New Child", next_followup=timezone.localdate())
        self.client.force_login(self.staff)

        expectations = {
            "new": {"child", "parent", "received", "waiting"},
            "assessment_booked": {"child", "parent", "assessment", "time", "waiting"},
            "assessed": {"child", "parent", "assessment", "next_followup", "waiting"},
            "started": {"child", "parent", "assessment", "started"},
            "not_proceeding": {"child", "parent", "moved", "reason"},
            "followups_due": {
                "child", "parent", "assessment", "next_followup", "waiting", "status"
            },
        }
        for stage, expected_keys in expectations.items():
            with self.subTest(stage=stage):
                response = self.client.get(
                    reverse("staff:staff_list"),
                    {"status": stage},
                )
                self.assertEqual(
                    {header["key"] for header in response.context["sort_headers"]},
                    expected_keys,
                )
                self.assertNotContains(response, 'data-save-field=')
                self.assertNotIn(
                    "last_contacted",
                    [header["key"] for header in response.context["sort_headers"]],
                )

        started_response = self.client.get(
            reverse("staff:staff_list"),
            {"status": "started"},
        )
        self.assertNotContains(started_response, 'data-row-action="stage"')
        not_proceeding_response = self.client.get(
            reverse("staff:staff_list"),
            {"status": "not_proceeding"},
        )
        self.assertContains(not_proceeding_response, "Reopen")
        self.assertIn(assessments.pk, {
            item.pk for item in self.client.get(
                reverse("staff:staff_list"), {"status": "assessed"}
            ).context["enquiries"]
        })
        self.assertIn(booked.pk, {
            item.pk for item in self.client.get(
                reverse("staff:staff_list"), {"status": "assessment_booked"}
            ).context["enquiries"]
        })
        booked_response = self.client.get(
            reverse("staff:staff_list"), {"status": "assessment_booked"}
        )
        self.assertContains(booked_response, "Mark assessed")
        self.assertNotContains(booked_response, "Needs tutor")
        self.assertNotContains(booked_response, "data-whatsapp")
        self.assertContains(booked_response, "data-open-edit")
        self.assertIn(started.pk, {item.pk for item in started_response.context["enquiries"]})
        self.assertIn(
            not_proceeding.pk,
            {item.pk for item in not_proceeding_response.context["enquiries"]},
        )

    def test_list_paginates_and_preserves_filters(self):
        for index in range(26):
            self.create_enquiry(f"Child {index:02}", status="new")
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("staff:staff_list"),
            {"status": "new", "q": "Taylor", "subject": self.maths.pk, "page": 2},
        )

        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(len(response.context["enquiries"]), 1)
        self.assertContains(response, "status=new")
        self.assertContains(response, "q=Taylor")
        self.assertContains(response, f"subject={self.maths.pk}")

    def test_list_ignores_legacy_received_filter_and_shows_all_stage_rows(self):
        today = timezone.localdate()
        this_month_start = date(today.year, today.month, 1)
        if today.month == 1:
            previous_month_start = date(today.year - 1, 12, 1)
        else:
            previous_month_start = date(today.year, today.month - 1, 1)
        current = self.create_enquiry("Current Child")
        previous = self.create_enquiry("Previous Child")
        Enquiry.objects.filter(pk=current.pk).update(
            created=timezone.make_aware(datetime.combine(this_month_start, time.min))
        )
        Enquiry.objects.filter(pk=previous.pk).update(
            created=timezone.make_aware(
                datetime.combine(previous_month_start, time.min)
            )
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("staff:staff_list"),
            {
                "status": "all",
                "received": "this_month",
            },
        )

        self.assertEqual(
            {item.pk for item in response.context["enquiries"]},
            {current.pk, previous.pk},
        )
        self.assertContains(response, "Current Child")
        self.assertContains(response, "Previous Child")
        self.assertNotContains(response, 'id="received-filter"')
        self.assertNotContains(response, "received=this_month")
        self.assertNotIn(
            "last_contacted",
            [header["key"] for header in response.context["sort_headers"]],
        )

    def test_enquiry_panel_updates_dates_and_moves_stage_without_reload(self):
        enquiry = self.create_enquiry("Panel Child")
        self.client.force_login(self.staff)
        panel_url = reverse("staff:staff_enquiry_panel", args=[enquiry.pk])

        detail = self.client.get(panel_url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["child_name"], "Panel Child")
        self.assertIn("family", detail.json())
        self.assertEqual(detail.json()["school_year"], "Year 5")
        self.assertEqual(detail.json()["family"]["phone"], "555-0102")

        response = self.client.post(
            panel_url,
            json.dumps(
                {
                    "action": "contact",
                    "last_contacted": "2026-11-15",
                    "next_followup": "",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.last_contacted, date(2026, 11, 15))
        self.assertIsNone(enquiry.next_followup)

        response = self.client.post(
            panel_url,
            json.dumps({"action": "stage"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "assessment_booked")
        self.assertEqual(enquiry.assessment_date, timezone.localdate())
        self.assertEqual(response.json()["navigation_counts"]["new"], 0)
        self.assertEqual(response.json()["navigation_counts"]["assessment_booked"], 1)
        self.assertEqual(
            list(enquiry.history.values_list("to_status", flat=True)),
            ["new", "assessment_booked"],
        )

        response = self.client.post(
            panel_url,
            json.dumps({"action": "stage"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "assessed")
        self.assertEqual(enquiry.next_followup, timezone.localdate() + timedelta(days=3))
        self.assertEqual(response.json()["navigation_counts"]["assessment_booked"], 0)
        self.assertEqual(response.json()["navigation_counts"]["assessed"], 1)
        self.assertEqual(
            list(enquiry.history.values_list("to_status", flat=True)),
            ["new", "assessment_booked", "assessed"],
        )

    def test_enquiry_panel_edits_tutor_and_assessment_date(self):
        enquiry = self.create_enquiry(
            "Booking Detail Child",
            status="assessment_booked",
            assessment_date=timezone.localdate(),
        )
        self.client.force_login(self.staff)
        panel_url = reverse("staff:staff_enquiry_panel", args=[enquiry.pk])

        response = self.client.post(
            panel_url,
            json.dumps(
                {
                    "action": "assessment",
                    "assessment_date": "2026-11-25",
                    "assessment_time": "14:30",
                    "tutor": "Ms Smith",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.assessment_date, date(2026, 11, 25))
        self.assertEqual(enquiry.assessment_time, time(14, 30))
        self.assertEqual(enquiry.tutor, "Ms Smith")

        get_res = self.client.get(panel_url)
        self.assertEqual(get_res.json()["tutor"], "Ms Smith")
        self.assertEqual(get_res.json()["assessment_date"], "2026-11-25")
        self.assertEqual(get_res.json()["assessment_time"], "14:30")

    def test_enquiry_panel_rejects_invalid_date(self):
        enquiry = self.create_enquiry("Invalid Date Child")
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("staff:staff_enquiry_panel", args=[enquiry.pk]),
            json.dumps(
                {
                    "action": "contact",
                    "last_contacted": "",
                    "next_followup": "not-a-date",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(enquiry.next_followup)

    def test_clean_edit_modal_updates_all_fields_and_stage(self):
        enquiry = self.create_enquiry("Editable Child", status="new")
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("staff:staff_enquiry_panel", args=[enquiry.pk]),
            json.dumps(
                {
                    "action": "edit",
                    "child_name": "Updated Child Name",
                    "school_year": "Year 6",
                    "assessment_day": "Saturday mornings",
                    "proposed_classes": "Tuesdays 4pm",
                    "parent_name": "Updated Parent",
                    "parent_phone": "07123456789",
                    "parent_email": "parent@example.com",
                    "country": "United Kingdom",
                    "status": "assessed",
                    "assessment_date": "2026-11-23",
                    "next_followup": "2026-11-26",
                    "notes": "Followed up via phone, very keen.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["saved"])
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.child_name, "Updated Child Name")
        self.assertEqual(enquiry.school_year, "Year 6")
        self.assertEqual(enquiry.assessment_day, "Saturday mornings")
        self.assertEqual(enquiry.proposed_classes, "Tuesdays 4pm")
        self.assertEqual(enquiry.family.parent_name, "Updated Parent")
        self.assertEqual(enquiry.family.parent_phone, "07123456789")
        self.assertEqual(enquiry.family.parent_email, "parent@example.com")
        self.assertEqual(enquiry.family.country, "United Kingdom")
        self.assertEqual(enquiry.status, "assessed")
        self.assertEqual(enquiry.assessment_date, date(2026, 11, 23))
        self.assertEqual(enquiry.next_followup, date(2026, 11, 26))
        self.assertEqual(enquiry.notes, "Followed up via phone, very keen.")

    def test_enquiry_in_new_for_five_days_appears_in_followups_due(self):
        five_days_ago = timezone.now() - timedelta(days=5, hours=1)
        old_new = self.create_enquiry("Five Day Old Lead", status="new")
        Enquiry.objects.filter(pk=old_new.pk).update(status_changed_at=five_days_ago)
        recent_new = self.create_enquiry("Fresh Lead", status="new")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("staff:staff_list"), {"status": "followups_due"})
        pks = [item.pk for item in response.context["enquiries"]]
        self.assertIn(old_new.pk, pks)
        self.assertNotIn(recent_new.pk, pks)
        self.assertContains(response, "Five Day Old Lead")
        self.assertContains(response, "Pending")

    def test_not_proceeding_row_can_reopen_to_new(self):
        enquiry = self.create_enquiry("Reopen Child", status="not_proceeding")
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("staff:staff_enquiry_panel", args=[enquiry.pk]),
            json.dumps({"action": "reopen"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "new")
        self.assertEqual(response.json()["message"], "Reopen Child moved to New")

    def test_detail_updates_tracking_without_previous_enquiries_box(self):
        first = self.create_enquiry("Sibling One", submission_ref="shared-ref")
        sibling = self.create_enquiry(
            "Sibling Two",
            submission_ref="shared-ref",
            family=first.family,
        )
        self.client.force_login(self.staff)
        detail_url = reverse("staff:staff_detail", args=[first.pk])
        filters = "?status=new&q=Sibling&subject=" + str(self.maths.pk)

        response = self.client.get(detail_url + filters)
        self.assertNotContains(response, "Previous enquiries")
        self.assertNotContains(response, "Sibling Two")
        self.assertContains(response, "Country")
        self.assertContains(response, "tel:555-0102")
        self.assertContains(response, "mailto:taylor@example.com")

        response = self.client.post(
            detail_url + filters,
            {
                "status": "assessed",
                "last_contacted": "2026-11-12",
                "next_followup": "2026-11-20",
                "notes": "Assessment scheduled.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("status=new", response.url)
        self.assertIn("subject=", response.url)
        first.refresh_from_db()
        self.assertEqual(first.status, "assessed")
        self.assertEqual(first.notes, "Assessment scheduled.")
        self.assertEqual(first.family.parent_phone, "555-0102")

        response = self.client.post(
            reverse("staff:staff_detail", args=[sibling.pk]),
            {
                "stage_action": "book_assessment",
                "assessment_date": "2026-11-14",
            },
        )
        self.assertRedirects(response, reverse("staff:staff_list"))
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, "assessment_booked")
        self.assertEqual(sibling.assessment_date.isoformat(), "2026-11-14")

    def post_stage_action(self, enquiry, action, **extra_data):
        data = {
            "stage_action": action,
            "enquiry_id": enquiry.pk,
        }
        data.update(extra_data)
        return self.client.post(reverse("staff:staff_list") + "?status=all", data)

    def test_new_moves_to_assessment_booked_via_book_assessment(self):
        enquiry = self.create_enquiry("New Child")
        self.client.force_login(self.staff)
        original_timestamp = enquiry.status_changed_at

        response = self.post_stage_action(
            enquiry,
            "book_assessment",
            assessment_date="2026-11-15",
            assessment_time="10:00",
            tutor="Sarah",
        )

        self.assertRedirects(response, reverse("staff:staff_list") + "?status=all")
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "assessment_booked")
        self.assertEqual(enquiry.assessment_date.isoformat(), "2026-11-15")
        self.assertEqual(enquiry.assessment_time, time(10, 0))
        self.assertEqual(enquiry.tutor, "Sarah")
        self.assertGreater(enquiry.status_changed_at, original_timestamp)

    def test_assessment_booked_moves_to_assessed_and_sets_followup_if_empty(self):
        enquiry = self.create_enquiry(
            "Booked Child",
            status="assessment_booked",
            assessment_date=timezone.localdate(),
        )
        self.client.force_login(self.staff)

        self.post_stage_action(enquiry, "mark_assessed")

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "assessed")
        self.assertEqual(enquiry.next_followup, timezone.localdate() + timedelta(days=3))

    def test_assessed_moves_to_started_and_records_start_date(self):
        enquiry = self.create_enquiry("Assessed Child", status="assessed")
        self.client.force_login(self.staff)

        self.post_stage_action(
            enquiry,
            "mark_started",
            start_date="2026-11-16",
        )

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "started")
        self.assertEqual(enquiry.start_date.isoformat(), "2026-11-16")

    def test_new_assessed_and_started_can_move_to_not_proceeding(self):
        self.client.force_login(self.staff)

        for stage in ("new", "assessment_booked", "assessed", "started"):
            with self.subTest(stage=stage):
                enquiry = self.create_enquiry(f"{stage} Child", status=stage)
                self.post_stage_action(
                    enquiry,
                    "not_proceeding",
                    reason="Family schedule changed",
                )
                enquiry.refresh_from_db()
                self.assertEqual(enquiry.status, "not_proceeding")
                self.assertEqual(enquiry.not_proceeding_reason, "Family schedule changed")

    def test_move_back_returns_started_to_assessed_and_assessed_to_new(self):
        self.client.force_login(self.staff)

        for current_stage, expected_stage in (
            ("started", "assessed"),
            ("assessed", "assessment_booked"),
            ("assessment_booked", "new"),
        ):
            with self.subTest(current_stage=current_stage):
                enquiry = self.create_enquiry(
                    f"{current_stage} Child",
                    status=current_stage,
                )
                previous_timestamp = enquiry.status_changed_at
                self.post_stage_action(enquiry, "move_back")
                enquiry.refresh_from_db()
                self.assertEqual(enquiry.status, expected_stage)
                self.assertGreater(enquiry.status_changed_at, previous_timestamp)

    def test_reopen_moves_not_proceeding_to_new(self):
        enquiry = self.create_enquiry(
            "Reopened Child",
            status="not_proceeding",
            not_proceeding_reason="Changed their mind",
        )
        self.client.force_login(self.staff)

        self.post_stage_action(enquiry, "reopen")

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "new")
        self.assertEqual(enquiry.not_proceeding_reason, "Changed their mind")

    def test_stage_history_is_written_on_creation_and_each_status_change(self):
        enquiry = self.create_enquiry("History Child")
        enquiry.change_status("assessed", self.staff)
        enquiry.change_status("started", self.staff)

        history = list(enquiry.history.all())

        self.assertEqual(
            [(entry.from_status, entry.to_status) for entry in history],
            [("", "new"), ("new", "assessed"), ("assessed", "started")],
        )
        self.assertIsNone(history[0].changed_by)
        self.assertEqual([entry.changed_by_id for entry in history[1:]], [self.staff.pk] * 2)

    def test_status_timestamp_changes_only_when_status_changes(self):
        enquiry = self.create_enquiry("Timestamp Child")
        self.assertLessEqual(
            abs(timezone.now() - enquiry.status_changed_at),
            timedelta(seconds=2),
        )
        enquiry.status_changed_at = timezone.now() - timedelta(seconds=1)
        enquiry.save(update_fields=["status_changed_at"])
        original_timestamp = enquiry.status_changed_at
        enquiry.notes = "Followed up"
        enquiry.save()
        self.assertEqual(enquiry.status_changed_at, original_timestamp)

        enquiry.status = "assessed"
        enquiry.save(update_fields=["status"])
        self.assertGreater(enquiry.status_changed_at, original_timestamp)

    def test_default_list_is_new_and_started_sorts_by_newest_start_date(self):
        old_new = self.create_enquiry("Old New")
        old_new.status_changed_at = timezone.now() - timedelta(days=10)
        old_new.save(update_fields=["status_changed_at"])
        new_new = self.create_enquiry("Recent New")
        started_earlier = self.create_enquiry(
            "Started Earlier",
            status="started",
            start_date=timezone.localdate() - timedelta(days=5),
        )
        started_later = self.create_enquiry(
            "Started Later",
            status="started",
            start_date=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(self.staff)

        default_response = self.client.get(reverse("staff:staff_list"))
        self.assertEqual(
            [item.pk for item in default_response.context["enquiries"]],
            [old_new.pk, new_new.pk],
        )

        started_response = self.client.get(
            reverse("staff:staff_list"),
            {"status": "started"},
        )
        self.assertEqual(
            [item.pk for item in started_response.context["enquiries"]],
            [started_later.pk, started_earlier.pk],
        )

    def test_archive_and_custom_delete_screens_are_removed(self):
        enquiry = self.create_enquiry("No custom destructive actions")
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get("/tracker/archive/").status_code, 404)
        self.assertEqual(
            self.client.post(f"/tracker/{enquiry.pk}/archive/").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/tracker/{enquiry.pk}/restore/").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/tracker/{enquiry.pk}/delete/").status_code,
            404,
        )
        detail_url = reverse("staff:staff_detail", args=[enquiry.pk])
        detail = self.client.get(detail_url)
        self.assertNotContains(detail, "Archive")
        self.assertNotContains(detail, "Restore")
        self.assertNotContains(detail, "Delete permanently")
        self.assertNotContains(detail, "Previous enquiries")

    def test_report_counts_received_and_stage_changes_including_archived(self):
        today = timezone.localdate()
        this_month_start = date(today.year, today.month, 1)
        if today.month == 1:
            previous_month_start = date(today.year - 1, 12, 1)
        else:
            previous_month_start = date(today.year, today.month - 1, 1)
        current_received = self.create_enquiry("Current Received")
        current_started = self.create_enquiry("Current Started")
        current_started.change_status("assessed", self.staff)
        current_started.change_status("started", self.staff)
        archived_started = self.create_enquiry("Archived Started")
        archived_started.change_status("started", self.staff)
        archived_started.is_archived = True
        archived_started.archived_at = timezone.now()
        archived_started.save(update_fields=("is_archived", "archived_at"))
        last_month_started = self.create_enquiry("Last Month Started")
        last_month_started.change_status("started", self.staff)

        start_this_month = timezone.make_aware(
            datetime.combine(this_month_start, time.min)
        )
        start_last_month = timezone.make_aware(
            datetime.combine(previous_month_start, time.min)
        )
        Enquiry.objects.filter(
            pk__in=(current_received.pk, current_started.pk, archived_started.pk)
        ).update(created=start_this_month)
        Enquiry.objects.filter(pk=last_month_started.pk).update(
            created=start_last_month
        )
        StageChange.objects.filter(
            enquiry=last_month_started,
            to_status="started",
        ).update(changed_at=start_last_month)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("staff:staff_report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["this_month"]["received"], 3)
        self.assertEqual(response.context["this_month"]["started"], 2)
        self.assertEqual(response.context["last_month"]["received"], 1)
        self.assertEqual(response.context["last_month"]["started"], 1)
        this_month_row = next(
            row for row in response.context["months"]
            if row["month"] == this_month_start
        )
        self.assertEqual(this_month_row["conversion"], 66.7)

    def test_stage_download_applies_filters_and_exports_all_pages(self):
        other_subject = Subject.objects.create(name=f"Other {uuid.uuid4()}")
        shared_family = Family.objects.create(
            parent_name="Export Parent",
            parent_phone="+234 080 123 0000",
            parent_email="",
            country="",
        )
        for index in range(26):
            self.create_enquiry(
                f"Export Child {index:02}",
                family=shared_family if index < 2 else None,
                parent_name="Export Parent",
                parent_phone="+234 080 123 0000",
                parent_email="",
                country="",
                school_year="Year 11",
                status="assessed",
                assessment_date=timezone.localdate(),
                start_date=timezone.localdate() if index == 0 else None,
                next_followup=timezone.localdate() if index == 1 else None,
                last_contacted=timezone.localdate() if index == 1 else None,
                notes="=SUM(1,1)" if index == 0 else "",
            )
        wrong_subject = self.create_enquiry(
            "Export Parent Wrong Subject",
            parent_name="Export Parent",
            status="assessed",
        )
        wrong_subject.subjects.clear()
        wrong_subject.subjects.add(other_subject)
        self.create_enquiry(
            "Export Parent Wrong Stage",
            parent_name="Export Parent",
            status="new",
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("staff:staff_download"),
            {
                "status": "assessed",
                "q": "Export Parent",
                "subject": self.maths.pk,
                "page": 2,
            },
        )

        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="enquiries-assessed-{timezone.localdate().isoformat()}.xlsx"',
        )
        worksheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(
            [cell.value for cell in worksheet[1]],
            [
                "Reference", "Child", "School year", "Subjects", "Parent", "Phone", "Email",
                "Country", "Preferred assessment day", "Preferred class days and time",
                "Stage", "Received", "Assessment date", "Start date", "Next follow-up",
                "Last contacted", "Days waiting", "Siblings", "Not proceeding reason",
                "Notes",
            ],
        )
        self.assertEqual(worksheet.max_row, 27)
        self.assertEqual(worksheet.freeze_panes, "A2")
        self.assertEqual(worksheet.auto_filter.ref, "A1:T27")
        self.assertTrue(worksheet["A1"].font.bold)
        self.assertEqual(worksheet["F2"].value, "+234 080 123 0000")
        self.assertEqual(worksheet["F2"].data_type, "s")
        for cell_address in ("L2", "M2", "N2", "O3", "P3"):
            self.assertEqual(
                worksheet[cell_address].number_format,
                "DD/MM/YYYY",
            )
        self.assertTrue(isinstance(worksheet["L2"].value, (date, datetime)))
        self.assertTrue(isinstance(worksheet["O3"].value, (date, datetime)))
        self.assertTrue(isinstance(worksheet["P3"].value, (date, datetime)))
        self.assertIsNone(worksheet["O2"].value)
        self.assertEqual(worksheet["T2"].value, "=SUM(1,1)")
        self.assertEqual(worksheet["T2"].data_type, "s")
        self.assertTrue(worksheet["T2"].alignment.wrap_text)
        self.assertEqual(worksheet["R2"].value, "Export Child 01")

    def test_download_everything_includes_archived_children_and_report_button(self):
        for child_name, status, is_archived in (
            ("All New", "new", False),
            ("All Assessed", "assessed", False),
            ("All Started", "started", False),
            ("All Not proceeding", "not_proceeding", False),
            ("All Archived", "started", True),
        ):
            self.create_enquiry(
                child_name,
                status=status,
                is_archived=is_archived,
            )
        self.client.force_login(self.staff)

        report = self.client.get(reverse("staff:staff_report"))
        self.assertContains(report, "Download everything")
        response = self.client.get(reverse("staff:staff_download_everything"))

        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="enquiries-all-{timezone.localdate().isoformat()}.xlsx"',
        )
        worksheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(worksheet.max_row, 6)
        self.assertEqual(
            {worksheet.cell(row=row, column=11).value for row in range(2, 7)},
            {"New", "Assessed", "Started", "Not proceeding"},
        )
        self.assertIn(
            "All Archived",
            {worksheet.cell(row=row, column=2).value for row in range(2, 7)},
        )


class SeedTestDataCommandTests(TestCase):
    def test_seed_creates_consistent_marked_data_and_clear_removes_only_it(self):
        call_command("seed_test_data")

        seeded = Enquiry.objects.filter(submission_ref__startswith="TEST-")
        self.assertEqual(seeded.count(), 50)
        self.assertEqual(
            Family.objects.filter(children__submission_ref__startswith="TEST-")
            .distinct()
            .count(),
            35,
        )
        self.assertEqual(
            {
                status: seeded.filter(status=status, is_archived=False).count()
                for status, _ in Enquiry.STATUS
            },
            {"new": 14, "assessment_booked": 0, "assessed": 10, "started": 14, "not_proceeding": 6},
        )
        self.assertEqual(seeded.filter(is_archived=True).count(), 6)

        for enquiry in seeded.select_related("family").prefetch_related("history"):
            statuses = list(enquiry.history.values_list("to_status", flat=True))
            if enquiry.status == "new":
                self.assertEqual(statuses, ["new"])
            elif enquiry.status == "assessed":
                self.assertEqual(statuses, ["new", "assessed"])
                self.assertGreater(enquiry.assessment_date, enquiry.created.date())
            elif enquiry.status == "started":
                self.assertEqual(statuses, ["new", "assessed", "started"])
                self.assertGreater(enquiry.assessment_date, enquiry.created.date())
                self.assertGreater(enquiry.start_date, enquiry.assessment_date)
            else:
                self.assertEqual(statuses, ["new", "assessed", "not_proceeding"])
                self.assertGreater(enquiry.assessment_date, enquiry.created.date())
            self.assertEqual(
                enquiry.status_changed_at,
                enquiry.history.latest("changed_at", "pk").changed_at,
            )
            self.assertLessEqual(
                enquiry.status_changed_at.date(),
                timezone.localdate(),
            )
            self.assertTrue(enquiry.submission_ref.startswith("TEST-"))

        outside_family = Family.objects.create(
            parent_name="Regular Parent",
            parent_phone="+234 803 555 0000",
        )
        regular_enquiry = Enquiry.objects.create(
            family=outside_family,
            child_name="Regular Child",
            submission_ref="regular-ref",
            school_year="Year 5",
        )
        call_command("seed_test_data", "--clear")

        self.assertFalse(Enquiry.objects.filter(submission_ref__startswith="TEST-").exists())
        self.assertTrue(Enquiry.objects.filter(pk=regular_enquiry.pk).exists())
        self.assertTrue(Family.objects.filter(pk=outside_family.pk).exists())


class StageTrackingMigrationTests(TransactionTestCase):
    migrate_from = ("tracker", "0004_seed_default_subjects")
    migrate_to = ("tracker", "0005_enquiry_stage_tracking")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])

        old_apps = executor.loader.project_state([self.migrate_from]).apps
        OldEnquiry = old_apps.get_model("tracker", "Enquiry")
        enquiry = OldEnquiry.objects.create(
            child_name="Legacy Child",
            parent_name="Legacy Parent",
            parent_phone="555-0199",
            location="Legacy Country",
            school_year="Year 4",
            status="assessment_booked",
        )
        self.enquiry_pk = enquiry.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_assessment_booked_migrates_to_new_and_stage_timestamp_is_populated(self):
        executor = MigrationExecutor(connection)
        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        MigratedEnquiry = migrated_apps.get_model("tracker", "Enquiry")
        enquiry = MigratedEnquiry.objects.get(pk=self.enquiry_pk)

        self.assertEqual(enquiry.status, "new")
        self.assertIsNotNone(enquiry.status_changed_at)


class FamilyDataMigrationTests(TransactionTestCase):
    migrate_from = ("tracker", "0005_enquiry_stage_tracking")
    migrate_to = ("tracker", "0006_families_archive_stage_history")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])

        old_apps = executor.loader.project_state([self.migrate_from]).apps
        OldEnquiry = old_apps.get_model("tracker", "Enquiry")
        first = OldEnquiry.objects.create(
            child_name="Legacy One",
            parent_name="Legacy Parent",
            parent_phone="555-0199",
            parent_email="",
            location="Legacy Country",
            school_year="Year 4",
            status="new",
            status_changed_at=timezone.now(),
        )
        second = OldEnquiry.objects.create(
            child_name="Legacy Two",
            parent_name="Legacy Parent",
            parent_phone="(555) 0199",
            parent_email="legacy@example.com",
            location="",
            school_year="Year 5",
            status="assessed",
            status_changed_at=timezone.now(),
        )
        self.enquiry_ids = (first.pk, second.pk)
        self.created_values = (first.created, second.created)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_enquiries_are_linked_and_initial_history_is_seeded(self):
        executor = MigrationExecutor(connection)
        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        MigratedEnquiry = migrated_apps.get_model("tracker", "Enquiry")
        enquiries = list(
            MigratedEnquiry.objects.filter(pk__in=self.enquiry_ids)
            .select_related("family")
            .order_by("pk")
        )
        self.assertEqual(len(enquiries), 2)
        self.assertEqual(enquiries[0].family_id, enquiries[1].family_id)
        self.assertEqual(enquiries[0].family.phone_normalized, "5550199")
        self.assertEqual(enquiries[0].family.parent_email, "legacy@example.com")
        self.assertEqual(enquiries[0].family.country, "Legacy Country")

        for enquiry, created in zip(enquiries, self.created_values):
            change = enquiry.history.get()
            self.assertEqual(change.from_status, "")
            self.assertEqual(change.to_status, enquiry.status)
            self.assertEqual(change.changed_at, created)

    def test_family_migration_can_be_reversed_without_losing_parent_fields(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])

        old_apps = executor.loader.project_state([self.migrate_from]).apps
        OldEnquiry = old_apps.get_model("tracker", "Enquiry")
        restored = list(
            OldEnquiry.objects.filter(pk__in=self.enquiry_ids).order_by("pk")
        )

        self.assertEqual([item.parent_name for item in restored], ["Legacy Parent"] * 2)
        self.assertEqual(
            [item.parent_phone for item in restored],
            ["555-0199", "555-0199"],
        )
        self.assertEqual(restored[0].location, "Legacy Country")
        self.assertEqual(
            [item.parent_email for item in restored],
            ["legacy@example.com", "legacy@example.com"],
        )
