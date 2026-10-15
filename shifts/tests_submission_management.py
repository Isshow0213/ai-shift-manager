from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from .models import Availability, DayOffRequest, ShiftSubmission


class MonthlySubmissionManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="月提出の管理確認会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        users = get_user_model()
        cls.manager = users.objects.create_user(username="monthly_management_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager",
        )
        cls.attendance = StoreMembership.objects.create(
            user=users.objects.create_user(username="monthly_management_attendance"), store=cls.store,
        )
        cls.day_off = StoreMembership.objects.create(
            user=users.objects.create_user(username="monthly_management_day_off"), store=cls.store,
            submission_mode="day_off",
        )
        cls.draft = StoreMembership.objects.create(
            user=users.objects.create_user(username="monthly_management_draft"), store=cls.store,
        )
        cls.not_started = StoreMembership.objects.create(
            user=users.objects.create_user(username="monthly_management_not_started"), store=cls.store,
        )
        cls.work_date = date(2030, 11, 12)
        cls.target_month = cls.work_date.replace(day=1)

    def setUp(self):
        self.client.force_login(self.manager)
        self.query = {"year": 2030, "month": 11, "date": self.work_date.isoformat()}

    def submit(self, membership, *, submitted=True, target_month=None):
        return ShiftSubmission.objects.create(
            membership=membership, target_month=target_month or self.target_month,
            submission_mode=membership.submission_mode, is_submitted=submitted,
            submitted_at=timezone.now() if submitted else None,
        )

    def make_availability(self, membership, submission, *, work_date=None):
        return Availability.objects.create(
            user=membership.user, membership=membership, store=membership.store,
            submission=submission, work_date=work_date or self.work_date,
            start_time=time(9), end_time=time(18),
        )

    def test_zero_request_months_count_as_submitted_for_both_modes(self):
        self.submit(self.attendance)
        self.submit(self.day_off)
        self.submit(self.draft, submitted=False)

        response = self.client.get(reverse("manager_dashboard"), self.query)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_staff_count"], 4)
        self.assertEqual(response.context["submitted_count"], 2)
        self.assertEqual(response.context["unsubmitted_count"], 2)
        self.assertFalse(Availability.objects.exists())
        self.assertFalse(DayOffRequest.objects.exists())

    def test_saved_draft_times_do_not_count_as_a_monthly_submission(self):
        submission = self.submit(self.draft, submitted=False)
        self.make_availability(self.draft, submission)

        dashboard = self.client.get(reverse("manager_dashboard"), self.query)
        listing = self.client.get(reverse("manager_availability_list"), self.query)

        self.assertEqual(dashboard.context["submitted_count"], 0)
        self.assertEqual(dashboard.context["unsubmitted_count"], 4)
        draft_row = next(row for row in listing.context["submission_rows"]
                         if row["membership"].pk == self.draft.pk)
        self.assertFalse(draft_row["is_submitted"])
        self.assertIn(self.draft, listing.context["unsubmitted_memberships"])

    def test_management_rows_include_empty_submissions_and_their_mode_snapshot(self):
        attendance_submission = self.submit(self.attendance)
        day_off_submission = self.submit(self.day_off)
        self.attendance.submission_mode = "day_off"
        self.attendance.save(update_fields=["submission_mode"])

        response = self.client.get(reverse("manager_availability_list"), self.query)

        rows = {row["membership"].pk: row for row in response.context["submission_rows"]}
        self.assertEqual(set(rows), {self.attendance.pk, self.day_off.pk, self.draft.pk, self.not_started.pk})
        self.assertEqual(rows[self.attendance.pk]["submission"], attendance_submission)
        self.assertEqual(rows[self.attendance.pk]["mode_label"], "出勤希望方式")
        self.assertEqual(rows[self.day_off.pk]["submission"], day_off_submission)
        self.assertEqual(rows[self.day_off.pk]["mode_label"], "休み希望方式")
        self.assertTrue(rows[self.attendance.pk]["is_submitted"])
        self.assertTrue(rows[self.day_off.pk]["is_submitted"])
        self.assertEqual(rows[self.day_off.pk]["day_off_dates"], [])
        self.assertFalse(response.context["availabilities"].exists())

    def test_other_store_submissions_times_and_days_off_are_excluded(self):
        foreign_membership = StoreMembership.objects.create(
            user=self.attendance.user, store=self.other_store, submission_mode="day_off",
        )
        foreign_submission = self.submit(foreign_membership)
        foreign_availability = self.make_availability(foreign_membership, foreign_submission)
        foreign_day_off = DayOffRequest.objects.create(
            submission=foreign_submission, work_date=date(2030, 11, 13),
        )

        dashboard = self.client.get(reverse("manager_dashboard"), self.query)
        listing = self.client.get(reverse("manager_availability_list"), self.query)

        self.assertEqual(dashboard.context["submitted_count"], 0)
        self.assertNotIn(foreign_membership.pk, {row["membership"].pk for row in listing.context["submission_rows"]})
        self.assertNotIn(foreign_availability, listing.context["availabilities"])
        self.assertNotIn(foreign_day_off, listing.context["day_off_requests"])

    def test_rest_mode_calendar_marks_all_days_except_requested_day_off(self):
        submission = self.submit(self.day_off)
        DayOffRequest.objects.create(submission=submission, work_date=self.work_date)

        response = self.client.get(reverse("manager_shift_list"), self.query)

        days = {day["date"]: day for week in response.context["calendar_weeks"] for day in week}
        self.assertFalse(days[self.work_date]["has_availability"])
        self.assertTrue(days[date(2030, 11, 13)]["has_availability"])
        self.assertTrue(all(day["has_availability"] for day in days.values()
                            if day["is_current_month"] and day["date"] != self.work_date))
        self.assertFalse(response.context["day_candidates"])
        other_day = self.client.get(reverse("manager_shift_list"), {
            **self.query, "date": "2030-11-13",
        })
        self.assertEqual([row["membership"] for row in other_day.context["day_candidates"]], [self.day_off])
        self.assertTrue(other_day.context["day_candidates"][0]["all_day"])

    def test_availability_validation_rejects_a_day_with_a_day_off_request(self):
        submission = self.submit(self.day_off)
        DayOffRequest.objects.create(submission=submission, work_date=self.work_date)
        availability = Availability(
            user=self.day_off.user, membership=self.day_off, store=self.store, submission=submission,
            work_date=self.work_date, start_time=time(9), end_time=time(18),
        )

        with self.assertRaisesMessage(ValidationError, "同じ日に休み希望と出勤時間"):
            availability.full_clean()
        self.assertFalse(Availability.objects.exists())

    def test_day_off_validation_rejects_a_day_with_an_availability(self):
        submission = self.submit(self.day_off)
        self.make_availability(self.day_off, submission)
        request_day = DayOffRequest(submission=submission, work_date=self.work_date)

        with self.assertRaisesMessage(ValidationError, "同じ日に休み希望と出勤時間"):
            request_day.full_clean()
        self.assertFalse(DayOffRequest.objects.exists())

    def test_manager_cannot_move_timed_request_onto_a_day_off_in_another_month(self):
        source_submission = self.submit(self.day_off)
        availability = self.make_availability(self.day_off, source_submission)
        target_month = date(2030, 12, 1)
        target_submission = self.submit(self.day_off, target_month=target_month)
        DayOffRequest.objects.create(submission=target_submission, work_date=date(2030, 12, 12))
        before = {
            "availabilities": list(Availability.objects.order_by("pk").values()),
            "submissions": list(ShiftSubmission.objects.order_by("pk").values()),
            "days_off": list(DayOffRequest.objects.order_by("pk").values()),
        }

        response = self.client.post(reverse("manager_availability_edit", args=[availability.pk]), {
            "work_date": "2030-12-12", "start_time": "10:00", "end_time": "17:00",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertIn("休み希望", str(response.context["form"].non_field_errors()))
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before["availabilities"])
        self.assertEqual(list(ShiftSubmission.objects.order_by("pk").values()), before["submissions"])
        self.assertEqual(list(DayOffRequest.objects.order_by("pk").values()), before["days_off"])
