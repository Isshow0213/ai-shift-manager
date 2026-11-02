from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, DayOffRequest, Shift, ShiftSubmission
from shifts.overview import build_shift_overview_context
from shifts.overview_preferences import annotate_manager_overview, _consecutive_lengths


class OverviewPreferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="Company")
        cls.store = Store.objects.create(company=company, name="Main")
        cls.other_store = Store.objects.create(company=company, name="Other")
        cls.manager = get_user_model().objects.create_user(username="preference-manager")
        cls.manager_member = StoreMembership.objects.create(
            store=cls.store, user=cls.manager, role="manager")
        cls.user = get_user_model().objects.create_user(username="preference-staff")
        cls.member = StoreMembership.objects.create(store=cls.store, user=cls.user, role="staff")
        cls.other_member = StoreMembership.objects.create(
            store=cls.other_store, user=cls.user, role="staff")
        cls.submission = ShiftSubmission.objects.create(
            membership=cls.member, target_month=date(2026, 10, 1),
            submission_mode="availability", is_submitted=True)

    def wish(self, day=2, start=9, end=17, **kwargs):
        fields = dict(membership=self.member, user=self.user, submission=self.submission,
                      work_date=date(2026, 10, day), start_time=time(start), end_time=time(end))
        fields.update(kwargs)
        return Availability.objects.create(**fields)

    def shift(self, work_date=date(2026, 10, 2), start=9, end=17, membership=None):
        member = membership or self.member
        return Shift.objects.create(
            membership=member, user=member.user, store=member.store,
            work_date=work_date, start_time=time(start), end_time=time(end))

    def context(self):
        request = RequestFactory().get("/", {"year": 2026, "month": 10})
        context = build_shift_overview_context(request, self.store)
        annotate_manager_overview(context, self.store)
        return context

    def cells(self):
        return next(row["cells"] for row in self.context()["rows"] if row["user"] == self.user)

    def test_missing_shift_shows_submitted_wish(self):
        self.wish()
        self.assertEqual(self.cells()[1]["unmet_preference"], "希望：09:00〜17:00")

    def test_changed_time_shows_wish_but_exact_match_does_not(self):
        self.wish()
        shift = self.shift(start=10)
        self.assertEqual(self.cells()[1]["unmet_preference"], "希望：09:00〜17:00")
        shift.start_time = time(9)
        shift.save()
        self.assertEqual(self.cells()[1]["unmet_preference"], "")

    def test_split_shifts_and_duplicate_wishes_compare_covered_time(self):
        self.wish()
        self.wish()
        self.shift(end=12)
        self.shift(start=12)
        self.assertEqual(self.cells()[1]["unmet_preference"], "")

    def test_gap_in_split_shift_does_not_count_as_matched(self):
        self.wish()
        self.shift(end=12)
        self.shift(start=13)
        self.assertEqual(self.cells()[1]["unmet_preference"], "希望：09:00〜17:00")

    def test_only_submitted_current_month_wishes_are_used(self):
        self.wish()
        self.submission.is_submitted = False
        self.submission.save()
        self.assertEqual(self.cells()[1]["unmet_preference"], "")
        self.submission.is_submitted = True
        self.submission.save()
        Availability.objects.all().delete()
        old = ShiftSubmission.objects.create(
            membership=self.member, target_month=date(2026, 9, 1),
            submission_mode="availability", is_submitted=True)
        self.wish(submission=old)
        self.assertEqual(self.cells()[1]["unmet_preference"], "")

    def test_legacy_unlinked_wish_and_invalid_interval(self):
        self.wish(submission=None)
        self.wish(start=18, end=17)
        self.assertEqual(self.cells()[1]["unmet_preference"], "希望：09:00〜17:00")

    def test_day_off_has_no_label_even_when_shift_exists(self):
        self.submission.submission_mode = "day_off"
        self.submission.save()
        DayOffRequest.objects.create(submission=self.submission, work_date=date(2026, 10, 2))
        cell = self.cells()[1]
        self.assertEqual(cell["unmet_preference"], "")
        self.assertEqual(cell["availability_label"], "")
        self.shift()
        cell = self.cells()[1]
        self.assertEqual(cell["unmet_preference"], "")
        self.assertEqual(cell["availability_label"], "")
        self.assertEqual(self.cells()[2]["unmet_preference"], "")

    def test_day_off_mode_all_day_eligibility_does_not_claim_unmet_time(self):
        self.submission.submission_mode = "day_off"
        self.submission.save()
        self.shift()
        self.assertEqual(self.cells()[1]["unmet_preference"], "")
        self.assertEqual(self.cells()[2]["unmet_preference"], "")

    def test_submitted_availability_with_no_wish_has_no_label_for_registered_work(self):
        self.shift()
        cell = self.cells()[1]
        self.assertEqual(cell["unmet_preference"], "")
        self.assertEqual(cell["availability_label"], "")

    def test_other_store_wishes_are_not_used(self):
        other_submission = ShiftSubmission.objects.create(
            membership=self.other_member, target_month=date(2026, 10, 1),
            submission_mode="availability", is_submitted=True)
        self.wish(membership=self.other_member, submission=other_submission)
        self.assertEqual(self.cells()[1]["unmet_preference"], "")

    def test_streak_crosses_month_and_other_store_and_counts_days_once(self):
        for day in (27, 28, 29):
            self.shift(date(2026, 9, day))
        self.shift(date(2026, 9, 30), membership=self.other_member)
        self.shift(date(2026, 10, 1))
        self.shift(date(2026, 10, 1), start=18, end=20)
        self.shift(date(2026, 10, 2))
        self.shift(date(2026, 10, 4))
        cells = self.cells()
        self.assertEqual(cells[0]["consecutive_days"], 0)
        self.assertEqual(cells[1]["consecutive_days"], 6)
        self.assertEqual(cells[2]["consecutive_days"], 0)
        self.assertEqual(cells[3]["consecutive_days"], 0)

    def test_other_store_work_counts_between_local_shifts_without_marking_empty_cell(self):
        for day in (28, 29, 30):
            self.shift(date(2026, 9, day), membership=self.other_member)
        self.shift(date(2026, 10, 1))
        self.shift(date(2026, 10, 2), membership=self.other_member)
        self.shift(date(2026, 10, 3))
        cells = self.cells()
        self.assertEqual(cells[1]["consecutive_days"], 0)
        self.assertEqual(cells[2]["consecutive_days"], 6)

    def test_long_boundary_streak_loads_older_windows_and_stops_at_gap(self):
        first = date(2026, 10, 1)
        Shift.objects.bulk_create([
            Shift(store=self.store, membership=self.member, user=self.user,
                  work_date=first - timedelta(days=offset), start_time=time(9), end_time=time(17))
            for offset in range(100)
        ])
        self.assertEqual(self.cells()[0]["consecutive_days"], 100)

    def test_annotation_queries_do_not_grow_with_employees_or_days(self):
        self.wish()
        request = RequestFactory().get("/", {"year": 2026, "month": 10})
        for index in range(8):
            user = get_user_model().objects.create_user(username=f"more-staff-{index}")
            member = StoreMembership.objects.create(store=self.store, user=user, role="staff")
            ShiftSubmission.objects.create(
                membership=member, target_month=date(2026, 10, 1),
                submission_mode="availability", is_submitted=True)
            self.shift(membership=member)
        context = build_shift_overview_context(request, self.store)
        with self.assertNumQueries(4):
            annotate_manager_overview(context, self.store)

    def test_wishes_and_streak_annotations_are_private_to_manager_overview(self):
        for day in (27, 28, 29, 30):
            self.shift(date(2026, 9, day))
        self.wish()
        self.shift(date(2026, 10, 1), start=10)
        self.shift(start=10)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("manager_shift_overview"), {"year": 2026, "month": 10})
        self.assertContains(response, "希望：09:00〜17:00")
        self.assertContains(response, "6連勤")
        self.client.force_login(self.user)
        response = self.client.get(reverse("store_shift_overview"), {"year": 2026, "month": 10})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "希望：09:00〜17:00")
        for row in response.context["rows"]:
            for cell in row["cells"]:
                self.assertNotIn("unmet_preference", cell)
                self.assertNotIn("consecutive_days", cell)

    def test_autofill_data_contains_only_submitted_valid_wish_intervals(self):
        self.wish(start=9, end=12)
        self.wish(start=14, end=17)
        self.wish(start=18, end=17)
        context = self.context()
        self.assertEqual(context["overview_wish_times"], {
            str(self.member.pk): {"2026-10-02": [
                {"start_time": "09:00", "end_time": "12:00"},
                {"start_time": "14:00", "end_time": "17:00"},
            ]},
        })
        self.submission.is_submitted = False
        self.submission.save()
        self.assertEqual(self.context()["overview_wish_times"], {})

    def test_streaks_below_six_are_hidden_and_six_or_more_are_shown(self):
        for day in range(1, 8):
            self.shift(date(2026, 10, day))
        self.assertEqual([cell["consecutive_days"] for cell in self.cells()[:7]],
                         [0, 0, 0, 0, 0, 6, 7])

    def test_empty_context_is_safe(self):
        with self.assertNumQueries(0):
            annotate_manager_overview({"month_days": [], "rows": []}, self.store)

    def test_streak_at_minimum_date_does_not_underflow(self):
        self.shift(date.min)
        self.shift(date(1, 1, 2))
        lengths = _consecutive_lengths([self.user.pk], date(1, 1, 2), date(1, 1, 2))
        self.assertEqual(lengths[self.user.pk, date(1, 1, 2)], 2)
