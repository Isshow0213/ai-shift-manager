from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, DayOffRequest, HelpShift, Requirement, Shift, ShiftSubmission

from .services import generate_shifts_for_store


class SubmissionModeGenerationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="提出方式の自動生成会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.work_date = date(2030, 11, 12)
        cls.month = cls.work_date.replace(day=1)

    def member(self, username, *, mode="availability", store=None, active=True):
        return StoreMembership.objects.create(
            user=get_user_model().objects.create_user(username=username),
            store=store or self.store, submission_mode=mode, is_active=active,
        )

    def submission(self, membership, *, submitted=True, month=None, mode=None):
        return ShiftSubmission.objects.create(
            membership=membership, target_month=month or self.month,
            submission_mode=mode or membership.submission_mode,
            is_submitted=submitted, submitted_at=timezone.now() if submitted else None,
        )

    def availability(self, membership, submission=None, *, start=time(9), end=time(18)):
        return Availability.objects.create(
            user=membership.user, membership=membership, store=membership.store,
            submission=submission, work_date=self.work_date, start_time=start, end_time=end,
        )

    def requirement(self, *, start=time(9), end=time(18), count=1):
        return Requirement.objects.create(
            store=self.store, work_date=self.work_date, start_time=start,
            end_time=end, required_staff_count=count,
        )

    def run_generator(self):
        return generate_shifts_for_store(self.store.pk, self.work_date)

    def test_unsubmitted_legacy_wish_draft_and_empty_attendance_are_excluded(self):
        legacy = self.member("generate_legacy")
        self.availability(legacy)
        draft = self.member("generate_draft", mode="day_off")
        self.submission(draft, submitted=False)
        empty = self.member("generate_empty")
        self.submission(empty)
        self.requirement(count=3)

        result = self.run_generator()

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["shortfall_count"], 3)
        self.assertFalse(Shift.objects.exists())

    def test_empty_day_off_submission_can_fill_any_required_interval(self):
        membership = self.member("generate_all_day", mode="day_off")
        self.submission(membership)
        self.requirement(start=time(7), end=time(23))

        result = self.run_generator()

        self.assertEqual(result["created_count"], 1)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, membership)
        self.assertEqual((shift.start_time, shift.end_time), (time(7), time(23)))
        self.assertTrue(shift.is_generated)

    def test_day_off_is_excluded_while_unspecified_day_is_eligible(self):
        day_off = self.member("generate_day_off", mode="day_off")
        submission = self.submission(day_off)
        DayOffRequest.objects.create(submission=submission, work_date=self.work_date)
        available = self.member("generate_available", mode="day_off")
        self.submission(available)
        self.requirement(count=2)

        result = self.run_generator()

        self.assertEqual(result["shortfall_count"], 1)
        self.assertEqual(list(Shift.objects.values_list("membership_id", flat=True)), [available.pk])

    def test_day_off_time_exception_is_never_extended_outside_its_bounds(self):
        membership = self.member("generate_exception", mode="day_off")
        submission = self.submission(membership)
        self.availability(membership, submission, start=time(17), end=time(22))
        self.requirement(start=time(16), end=time(22))

        result = self.run_generator()

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["shortfall_count"], 1)
        self.assertFalse(Shift.objects.exists())

    def test_day_off_time_exception_can_fill_exact_and_shorter_requirements(self):
        membership = self.member("generate_exception_inside", mode="day_off")
        submission = self.submission(membership)
        self.availability(membership, submission, start=time(17), end=time(22))
        self.requirement(start=time(18), end=time(21))

        self.assertEqual(self.run_generator()["created_count"], 1)
        self.assertEqual(Shift.objects.get().membership, membership)

    def test_attendance_only_uses_submitted_membership_month_and_intervals(self):
        eligible = self.member("generate_attendance")
        self.availability(eligible, self.submission(eligible))
        wrong_month = self.member("generate_wrong_month", mode="day_off")
        self.submission(wrong_month, month=date(2030, 10, 1))
        wrong_store = self.member("generate_other_store", mode="day_off", store=self.other_store)
        self.submission(wrong_store)
        inactive = self.member("generate_inactive", mode="day_off", active=False)
        self.submission(inactive)
        no_wish = self.member("generate_no_wish")
        self.submission(no_wish)
        self.requirement(count=5)

        result = self.run_generator()

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["shortfall_count"], 4)
        self.assertEqual(Shift.objects.get().membership, eligible)

    def test_submitted_month_keeps_mode_when_preference_changes(self):
        membership = self.member("generate_snapshot", mode="day_off")
        self.submission(membership)
        membership.submission_mode = "availability"
        membership.save(update_fields=["submission_mode"])
        self.requirement()

        self.assertEqual(self.run_generator()["created_count"], 1)

    def test_adjacent_attendance_intervals_cover_requirement_but_gap_does_not(self):
        continuous = self.member("generate_continuous")
        submitted = self.submission(continuous)
        self.availability(continuous, submitted, start=time(9), end=time(12))
        self.availability(continuous, submitted, start=time(12), end=time(18))
        gap = self.member("generate_gap")
        submitted = self.submission(gap)
        self.availability(gap, submitted, start=time(9), end=time(12))
        self.availability(gap, submitted, start=time(13), end=time(18))
        self.requirement(count=2)

        result = self.run_generator()

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(Shift.objects.get().membership, continuous)
        self.assertEqual(result["shortfall_count"], 1)

    def test_manual_and_help_coverage_are_preserved_and_reduce_required_staff(self):
        membership = self.member("generate_coverage", mode="day_off")
        self.submission(membership)
        manual = Shift.objects.create(
            store=self.store, user=membership.user, membership=membership,
            work_date=self.work_date, start_time=time(8), end_time=time(20), is_generated=False,
        )
        helper = HelpShift.objects.create(
            store=self.store, name="他店ヘルプ", work_date=self.work_date,
            start_time=time(8), end_time=time(20), note="応援依頼済み",
        )
        self.requirement(count=2)

        result = self.run_generator()

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["shortfall_count"], 0)
        self.assertEqual(Shift.objects.get().pk, manual.pk)
        self.assertEqual(HelpShift.objects.get().pk, helper.pk)

    def test_resubmission_required_after_edit_excludes_previously_eligible_person(self):
        membership = self.member("generate_resubmission", mode="day_off")
        submission = self.submission(membership)
        submission.is_submitted = False
        submission.save(update_fields=["is_submitted"])
        self.requirement()

        result = self.run_generator()

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["shortfall_count"], 1)
