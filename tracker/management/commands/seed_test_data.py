import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tracker.models import Enquiry, Family, StageChange, Subject


PARENT_NAMES = [
    "Amina Adeyemi", "Chinedu Okafor", "Ngozi Eze", "Tunde Adebayo",
    "Folake Balogun", "Ibrahim Bello", "Zainab Musa", "Emeka Nwosu",
    "Kemi Ojo", "Olumide Lawal", "Adaobi Obi", "Babatunde Akinola",
    "Chisom Umeh", "Halima Sani", "Femi Ogunleye", "Nneka Ibe",
    "Seyi Aluko", "Fatima Abdullahi", "Obinna Anyanwu", "Tolu Bakare",
    "Amaka Iwu", "Yusuf Danjuma", "Bisi Afolayan", "Uche Nnamdi",
    "Maryam Garba", "Kunle Shonibare", "Ifeoma Ekwueme", "Segun Oladipo",
    "Nana Mensah", "Kwame Boateng", "Grace Williams", "Daniel Campbell",
    "Kofi Asare", "Ethan Martin", "Abena Owusu",
]

CHILD_NAMES = [
    "Amara", "Chika", "Dara", "Efe", "Eniola", "Fiyin", "Ife", "Ikenna",
    "Kamsi", "Kanyin", "Kelechi", "Lola", "Mide", "Nnamdi", "Obi", "Ola",
    "Onyeka", "Ore", "Sade", "Tami", "Tobi", "Ugo", "Zara", "Ayo", "Boma",
    "Chinwe", "Damilola", "Ebuka", "Folami", "Ireti", "Jide", "Kosi",
    "Lekan", "Muna", "Nneoma", "Oge", "Pele", "Ronke", "Somto", "Tayo",
    "Teni", "Yewande", "Zikora", "Abiola", "Bunmi", "Dike", "Ejiro",
    "Folarin", "Ifeanyi", "Jumoke",
]

NOT_PROCEEDING_REASONS = (
    "Too expensive",
    "Chose another school",
    "No response",
    "Timing did not work",
)

ASSESSMENT_DAYS = ("Monday", "Wednesday", "Saturday", "Flexible")
CLASS_OPTIONS = (
    "Mathematics, English",
    "Reading and Science",
    "English, Creative Writing",
    "Mathematics, Coding",
)


def _at_noon(day):
    return timezone.make_aware(
        datetime.combine(day, time(hour=12)),
        timezone.get_current_timezone(),
    )


class Command(BaseCommand):
    help = "Create 50 clearly marked test enquiries, or clear previously seeded data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove seeded enquiries and families made childless by their removal.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            self.clear_test_data()
            return

        subjects = list(Subject.objects.filter(is_active=True))
        if len(subjects) < 2:
            raise CommandError("At least two active subjects are required to seed data.")

        rng = random.Random(2026)
        today = timezone.localdate()
        family_sizes = [1] * 22 + [2] * 11 + [3] * 2
        rng.shuffle(family_sizes)
        countries = ["Nigeria"] * 31 + ["United Kingdom"] * 2 + [
            "Ghana", "Canada"
        ]
        rng.shuffle(countries)

        families = []
        for index, (parent_name, country) in enumerate(
            zip(PARENT_NAMES, countries), start=1
        ):
            if country == "Nigeria":
                phone = f"+234 803 000 {index:04d}"
            elif country == "United Kingdom":
                phone = f"+44 7700 900{index:03d}"
            elif country == "Ghana":
                phone = f"+233 24 000 {index:04d}"
            else:
                phone = f"+1 416 555 {index:04d}"
            families.append(
                Family.objects.create(
                    parent_name=parent_name,
                    parent_phone=phone,
                    parent_email=f"family{index}@example.test",
                    country=country,
                )
            )

        stage_values = (
            ["new"] * 14
            + ["assessed"] * 10
            + ["started"] * 18
            + ["not_proceeding"] * 8
        )
        archived_indices = {24, 25, 26, 27, 42, 43}
        child_index = 0
        for family, siblings_count in zip(families, family_sizes):
            for _ in range(siblings_count):
                status = stage_values[child_index]
                received = self.received_date(rng, today, child_index, status)
                assessment_date = None
                start_date = None
                moved_date = received
                age = (today - received).days
                if status != "new":
                    assessment_date = received + timedelta(
                        days=rng.randint(1, max(1, min(30, age - 1)))
                    )
                    moved_date = assessment_date
                if status == "started":
                    start_date = assessment_date + timedelta(
                        days=rng.randint(1, max(1, (today - assessment_date).days))
                    )
                    moved_date = start_date
                elif status == "not_proceeding":
                    moved_date = assessment_date + timedelta(
                        days=rng.randint(1, max(1, (today - assessment_date).days))
                    )

                enquiry = Enquiry.objects.create(
                    family=family,
                    child_name=f"{CHILD_NAMES[child_index]} {family.parent_name.split()[-1]}",
                    submission_ref=f"TEST-{child_index + 1}",
                    school_year=f"Year {rng.randint(3, 13)}",
                    assessment_day=rng.choice(ASSESSMENT_DAYS),
                    proposed_classes=rng.choice(CLASS_OPTIONS),
                    status="new",
                    status_changed_at=_at_noon(received),
                )
                enquiry.created = _at_noon(received)
                enquiry.status_changed_at = _at_noon(received)
                Enquiry.objects.filter(pk=enquiry.pk).update(
                    created=enquiry.created,
                    status_changed_at=enquiry.status_changed_at,
                )
                StageChange.objects.filter(enquiry=enquiry).update(
                    changed_at=_at_noon(received)
                )
                if status in {"assessed", "started", "not_proceeding"}:
                    enquiry.assessment_date = assessment_date
                    enquiry.save(update_fields=("assessment_date",))
                    enquiry.change_status("assessed")
                    self.set_stage_timestamp(enquiry, "assessed", assessment_date)
                if status == "started":
                    enquiry.start_date = start_date
                    enquiry.save(update_fields=("start_date",))
                    enquiry.change_status("started")
                    self.set_stage_timestamp(enquiry, "started", start_date)
                elif status == "not_proceeding":
                    enquiry.not_proceeding_reason = rng.choice(
                        NOT_PROCEEDING_REASONS
                    )
                    enquiry.save(update_fields=("not_proceeding_reason",))
                    enquiry.change_status("not_proceeding")
                    self.set_stage_timestamp(enquiry, "not_proceeding", moved_date)

                self.set_followup_and_contact(enquiry, rng, today, received, child_index)
                if rng.random() < 0.3:
                    enquiry.notes = rng.choice(
                        (
                            "Parent prefers weekday sessions.",
                            "Sent programme details by email.",
                            "Follow up after the school term.",
                            "Interested in a small group class.",
                        )
                    )
                    enquiry.save(update_fields=("notes",))
                enquiry.subjects.set(rng.sample(subjects, rng.randint(2, min(3, len(subjects)))))

                if child_index in archived_indices:
                    enquiry.is_archived = True
                    enquiry.archived_at = _at_noon(today)
                    enquiry.save(update_fields=("is_archived", "archived_at"))
                child_index += 1

        counts = {
            "New": 14,
            "Assessed": 10,
            "Started": 14,
            "Not proceeding": 6,
            "Archived": len(archived_indices),
        }
        self.stdout.write(self.style.SUCCESS("Created 50 test enquiries across 35 families."))
        for label, count in counts.items():
            self.stdout.write(f"{label}: {count}")

    @staticmethod
    def received_date(rng, today, index, status):
        if status != "new":
            age = rng.randint(4, 180)
            return today - timedelta(days=age)
        if index == 0:
            return today
        if index < 5:
            return today - timedelta(days=index)
        return today - timedelta(days=rng.randint(7, 180))

    @staticmethod
    def set_stage_timestamp(enquiry, status, day):
        timestamp = _at_noon(day)
        StageChange.objects.filter(enquiry=enquiry, to_status=status).update(
            changed_at=timestamp
        )
        Enquiry.objects.filter(pk=enquiry.pk).update(status_changed_at=timestamp)

    @staticmethod
    def set_followup_and_contact(enquiry, rng, today, received, index):
        if enquiry.status in {"new", "assessed"}:
            if index % 3 == 0:
                due = max(
                    today - timedelta(days=rng.choice((1, 2, 7, 21))),
                    received,
                )
            elif index % 8 == 1:
                due = today
            elif index % 5 == 2:
                due = today + timedelta(days=rng.randint(2, 21))
            else:
                due = None
            enquiry.next_followup = due
            if index % 3 != 0 and received < today:
                enquiry.last_contacted = received + timedelta(
                    days=rng.randint(1, (today - received).days)
                )
            enquiry.save(update_fields=("next_followup", "last_contacted"))

    def clear_test_data(self):
        marked = Enquiry.objects.filter(submission_ref__startswith="TEST-")
        family_ids = list(marked.values_list("family_id", flat=True).distinct())
        child_count = marked.count()
        marked.delete()
        orphaned_count, _ = Family.objects.filter(
            pk__in=family_ids, children__isnull=True
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {child_count} test enquiries and {orphaned_count} empty families."
            )
        )
