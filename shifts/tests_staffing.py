from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from accounts.models import Company, Store
from .models import HelpShift, Requirement, Shift, StaffingWarningDismissal
from .staffing import build_staffing_statuses, get_staffing_status


class StaffingCoverageTests(SimpleTestCase):
    work_date = date(2030, 11, 12)

    def requirement(self, start=9, end=18, count=2, **kwargs):
        return Requirement(
            work_date=self.work_date, start_time=time(start), end_time=time(end),
            required_staff_count=count, **kwargs,
        )

    def shift(self, user_id=1, start=9, end=18, **kwargs):
        return Shift(
            user_id=user_id, work_date=self.work_date,
            start_time=time(start), end_time=time(end), **kwargs,
        )

    def helper(self, name="ヘルプ", start=9, end=18, **kwargs):
        return HelpShift(
            name=name, work_date=self.work_date,
            start_time=time(start), end_time=time(end), **kwargs,
        )

    def status(self, requirements=None, shifts=(), helpers=(), dismissals=()):
        if requirements is None:
            requirements = [self.requirement()]
        return build_staffing_statuses(requirements, shifts, helpers, dismissals)[self.work_date]

    def test_no_shifts_reports_whole_requirement_shortage(self):
        status = self.status()
        self.assertTrue(status["show_warning"])
        self.assertEqual(status["max_shortfall"], 2)
        self.assertEqual(status["shortages"], [{
            "start_time": time(9), "end_time": time(18),
            "required_staff_count": 2, "scheduled_staff_count": 0, "shortfall_count": 2,
        }])

    def test_filled_requirement_and_unscheduled_dates_have_no_warning(self):
        status = self.status(shifts=[self.shift(1), self.shift(2)])
        self.assertFalse(status["has_shortage"])
        self.assertFalse(status["show_warning"])
        self.assertEqual(status["max_shortfall"], 0)
        self.assertEqual(build_staffing_statuses([], [self.shift()], []), {})

    def test_adjacent_staff_handover_fulfills_requirement(self):
        status = self.status(shifts=[
            self.shift(1), self.shift(2, end=13), self.shift(3, start=13),
        ])
        self.assertFalse(status["has_shortage"])

    def test_partial_shift_gap_is_reported_with_exact_times(self):
        status = self.status(shifts=[
            self.shift(1), self.shift(2, end=12), self.shift(3, start=13),
        ])
        self.assertEqual(status["shortages"], [{
            "start_time": time(12), "end_time": time(13),
            "required_staff_count": 2, "scheduled_staff_count": 1, "shortfall_count": 1,
        }])

    def test_multiple_requirement_ranges_use_maximum_simultaneous_requirement(self):
        requirements = [self.requirement(count=1), self.requirement(start=12, end=14, count=2)]
        self.assertFalse(self.status(requirements, [self.shift(1), self.shift(2, start=12, end=14)])["has_shortage"])
        shortage = self.status(requirements, [self.shift(1)])["shortages"]
        self.assertEqual(len(shortage), 1)
        self.assertEqual((shortage[0]["start_time"], shortage[0]["end_time"]), (time(12), time(14)))
        self.assertEqual(shortage[0]["required_staff_count"], 2)

    def test_separate_requirements_do_not_count_the_gap_as_shortage(self):
        requirements = [self.requirement(end=12), self.requirement(start=14)]
        self.assertEqual(
            [(item["start_time"], item["end_time"]) for item in self.status(requirements)["shortages"]],
            [(time(9), time(12)), (time(14), time(18))],
        )

    def test_same_user_is_not_counted_twice(self):
        status = self.status(shifts=[self.shift(1), self.shift(1)])
        self.assertEqual(status["shortages"][0]["scheduled_staff_count"], 1)
        self.assertEqual(status["max_shortfall"], 1)

    def test_helpers_count_and_same_name_deduplicates(self):
        self.assertFalse(self.status(shifts=[self.shift()], helpers=[self.helper()])["has_shortage"])
        status = self.status(helpers=[self.helper(), self.helper(start=10)])
        self.assertEqual(status["shortages"][0]["scheduled_staff_count"], 1)
        self.assertEqual(len(status["shortages"]), 1)

    def test_helper_handover_and_generated_shifts_count_normally(self):
        status = self.status(
            shifts=[self.shift(is_generated=True)],
            helpers=[self.helper(end=13), self.helper("午後ヘルプ", start=13)],
        )
        self.assertFalse(status["show_warning"])

    def test_shifts_ending_at_requirement_start_do_not_count(self):
        status = self.status(shifts=[self.shift(start=8, end=9), self.shift(start=18, end=20)])
        self.assertEqual(status["shortages"][0]["scheduled_staff_count"], 0)

    def test_warning_is_dismissed_only_for_matching_staffing_signature(self):
        original = self.status()
        dismissal = StaffingWarningDismissal(
            work_date=self.work_date, staffing_signature=original["staffing_signature"],
        )
        status = self.status(dismissals=[dismissal])
        self.assertTrue(status["has_shortage"])
        self.assertTrue(status["is_warning_dismissed"])
        self.assertFalse(status["show_warning"])
        self.assertIs(status["dismissal"], dismissal)

        updated = self.status(shifts=[self.shift()], dismissals=[dismissal])
        self.assertTrue(updated["show_warning"])
        self.assertFalse(updated["is_warning_dismissed"])
        self.assertIsNone(updated["dismissal"])

    def test_signature_changes_with_staffing_but_not_notes_colors_or_row_order(self):
        requirement = self.requirement()
        shifts = [self.shift(1), self.shift(2, end=12)]
        helper = self.helper(start=14)
        signature = self.status([requirement], shifts, [helper])["staffing_signature"]
        requirement.memo = "メモ変更"
        shifts[0].note = "注記"
        shifts[0].display_color = "red"
        helper.display_color = "green"
        self.assertEqual(signature, self.status([requirement], list(reversed(shifts)), [helper])["staffing_signature"])
        changes = [
            (requirement, "required_staff_count", 3),
            (requirement, "start_time", time(8)),
            (shifts[0], "user_id", 3),
            (shifts[0], "end_time", time(17)),
            (helper, "name", "別ヘルプ"),
            (helper, "start_time", time(15)),
        ]
        for item, field, new_value in changes:
            old_value = getattr(item, field)
            with self.subTest(field=field):
                setattr(item, field, new_value)
                self.assertNotEqual(signature, self.status([requirement], shifts, [helper])["staffing_signature"])
                setattr(item, field, old_value)

    def test_different_dates_are_independent(self):
        tomorrow = date(2030, 11, 13)
        requirement = self.requirement()
        requirement.work_date = tomorrow
        statuses = build_staffing_statuses(
            [self.requirement(count=1), requirement], [self.shift()], [],
        )
        self.assertFalse(statuses[self.work_date]["show_warning"])
        self.assertTrue(statuses[tomorrow]["show_warning"])


class StaffingStatusPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="人数確認会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="staffing_manager")
        cls.work_date = date(2030, 11, 12)
        cls.requirement = Requirement.objects.create(
            store=cls.store, work_date=cls.work_date,
            start_time=time(9), end_time=time(18), required_staff_count=2,
        )

    def test_current_store_only_and_no_requirements_returns_none(self):
        Shift.objects.create(
            store=self.other_store, user=self.manager, work_date=self.work_date,
            start_time=time(9), end_time=time(18),
        )
        HelpShift.objects.create(
            store=self.other_store, name="別店舗ヘルプ", work_date=self.work_date,
            start_time=time(9), end_time=time(18),
        )
        self.assertEqual(get_staffing_status(self.store, self.work_date)["max_shortfall"], 2)
        self.assertIsNone(get_staffing_status(self.other_store, self.work_date))

    def test_persisted_confirmation_is_invalidated_by_requirement_change(self):
        status = get_staffing_status(self.store, self.work_date)
        dismissal = StaffingWarningDismissal.objects.create(
            store=self.store, work_date=self.work_date,
            staffing_signature=status["staffing_signature"], dismissed_by=self.manager,
        )
        self.assertFalse(get_staffing_status(self.store, self.work_date)["show_warning"])
        self.requirement.required_staff_count = 3
        self.requirement.save()
        self.assertTrue(get_staffing_status(self.store, self.work_date)["show_warning"])
        self.assertEqual(StaffingWarningDismissal.objects.get().pk, dismissal.pk)

    def test_deleting_manager_preserves_confirmation_record(self):
        dismissal = StaffingWarningDismissal.objects.create(
            store=self.store, work_date=self.work_date,
            staffing_signature=get_staffing_status(self.store, self.work_date)["staffing_signature"],
            dismissed_by=self.manager,
        )
        self.manager.delete()
        dismissal.refresh_from_db()
        self.assertIsNone(dismissal.dismissed_by_id)
        self.assertIsNotNone(dismissal.dismissed_at)
