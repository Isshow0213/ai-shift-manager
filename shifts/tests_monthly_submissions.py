from datetime import date, time
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from shifts.eligibility import get_eligibility
from shifts.models import Availability, DayOffRequest, ShiftSubmission, StoreSubmissionDeadline


class MonthlySubmissionFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="月別提出の会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="支店")
        cls.employee = get_user_model().objects.create_user(username="monthly_employee")
        cls.other_employee = get_user_model().objects.create_user(username="monthly_other")
        cls.membership = StoreMembership.objects.create(user=cls.employee, store=cls.store)
        cls.other_membership = StoreMembership.objects.create(user=cls.other_employee, store=cls.store)
        cls.second_membership = StoreMembership.objects.create(user=cls.employee, store=cls.other_store)
        cls.month = date(2030, 11, 1)
        cls.work_date = date(2030, 11, 12)

    def submission(self, membership=None, mode="availability", submitted=True, month=None):
        return ShiftSubmission.objects.create(
            membership=membership or self.membership,
            target_month=month or self.month,
            submission_mode=mode,
            is_submitted=submitted,
            submitted_at=timezone.now() if submitted else None,
        )

    def availability(self, submission=None, *, membership=None, work_date=None,
                     start=time(9), end=time(18)):
        membership = membership or self.membership
        return Availability.objects.create(
            user=membership.user, membership=membership, store=membership.store,
            submission=submission, work_date=work_date or self.work_date,
            start_time=start, end_time=end,
        )


class MonthlyEligibilityTests(MonthlySubmissionFixture):
    def test_legacy_availability_alone_is_not_a_monthly_submission(self):
        self.availability()
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.available)
        self.assertEqual(eligibility.reason, "not_submitted")
        self.assertFalse(eligibility.covers(time(10), time(11)))

    def test_draft_day_off_mode_does_not_make_unselected_dates_available(self):
        self.submission(mode="day_off", submitted=False)
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.available)
        self.assertEqual(eligibility.reason, "not_submitted")

    def test_explicit_empty_availability_submission_means_no_available_days(self):
        self.submission()
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.available)
        self.assertEqual(eligibility.reason, "no_availability")

    def test_explicit_empty_day_off_submission_means_all_day_available(self):
        self.submission(mode="day_off")
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertTrue(eligibility.available)
        self.assertTrue(eligibility.all_day)
        self.assertTrue(eligibility.covers(time(0), time(23, 59)))

    def test_day_off_is_not_available_and_other_days_remain_available(self):
        submission = self.submission(mode="day_off")
        DayOffRequest.objects.create(submission=submission, work_date=self.work_date)
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.available)
        self.assertEqual(eligibility.reason, "day_off")
        self.assertTrue(get_eligibility(self.membership, date(2030, 11, 13)).all_day)

    def test_day_off_time_exception_is_available_only_inside_its_interval(self):
        submission = self.submission(mode="day_off")
        self.availability(submission, start=time(17), end=time(22))
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertTrue(eligibility.available)
        self.assertFalse(eligibility.all_day)
        self.assertTrue(eligibility.covers(time(17), time(22)))
        self.assertFalse(eligibility.covers(time(16, 59), time(22)))
        self.assertFalse(eligibility.covers(time(17), time(22, 1)))

    def test_attendance_interval_and_unspecified_day_have_different_results(self):
        submission = self.submission()
        self.availability(submission)
        self.assertTrue(get_eligibility(self.membership, self.work_date).covers(time(10), time(17)))
        self.assertFalse(get_eligibility(self.membership, date(2030, 11, 13)).available)

    def test_continuous_intervals_cover_while_a_gap_does_not(self):
        submission = self.submission()
        self.availability(submission, start=time(9), end=time(12))
        second = self.availability(submission, start=time(12), end=time(18))
        self.assertTrue(get_eligibility(self.membership, self.work_date).covers(time(9), time(18)))
        second.start_time = time(13)
        second.save(update_fields=["start_time"])
        self.assertFalse(get_eligibility(self.membership, self.work_date).covers(time(9), time(18)))

    def test_previous_month_and_other_store_submissions_never_grant_access(self):
        self.submission(mode="day_off", month=date(2030, 10, 1))
        self.submission(self.second_membership, mode="day_off")
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.available)
        self.assertEqual(eligibility.reason, "not_submitted")

    def test_stored_monthly_mode_takes_precedence_over_changed_preference(self):
        self.submission(mode="day_off")
        self.membership.submission_mode = "availability"
        self.membership.save(update_fields=["submission_mode"])
        self.assertTrue(get_eligibility(self.membership, self.work_date).all_day)

    def test_inactive_membership_or_user_cannot_be_assigned(self):
        self.submission(mode="day_off")
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertFalse(get_eligibility(self.membership, self.work_date).available)
        self.membership.is_active = True
        self.membership.save(update_fields=["is_active"])
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        self.assertFalse(get_eligibility(self.membership, self.work_date).available)

    def test_degenerate_or_reversed_requested_intervals_are_not_covered(self):
        self.submission(mode="day_off")
        eligibility = get_eligibility(self.membership, self.work_date)
        self.assertFalse(eligibility.covers(time(9), time(9)))
        self.assertFalse(eligibility.covers(time(18), time(9)))


class MonthlySubmissionEmployeeTests(MonthlySubmissionFixture):
    def setUp(self):
        self.client.force_login(self.employee)

    def calendar_url(self, *, store=None, month=11):
        return f"{reverse('availability_list')}?store={(store or self.store).pk}&year=2030&month={month}"

    def submit_url(self, *, store=None, month=11):
        return f"{reverse('monthly_submission_submit')}?store={(store or self.store).pk}&year=2030&month={month}"

    def create_url(self, *, store=None, work_date=None):
        return (f"{reverse('availability_create')}?store={(store or self.store).pk}"
                f"&date={(work_date or self.work_date).isoformat()}")

    def settings_url(self, *, store=None):
        return f"{reverse('submission_settings')}?store={(store or self.store).pk}"

    def save_day(self, status, **overrides):
        data = {"work_date": self.work_date.isoformat(), "day_status": status}
        data.update(overrides)
        return self.client.post(self.create_url(), data)

    def test_reading_calendar_form_and_settings_never_creates_submissions(self):
        for url in [self.calendar_url(), self.create_url(), self.settings_url()]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(ShiftSubmission.objects.exists())
        self.assertFalse(DayOffRequest.objects.exists())
        self.assertFalse(Availability.objects.exists())

    def test_empty_attendance_month_can_be_explicitly_submitted(self):
        response = self.client.post(self.submit_url())
        self.assertEqual(response.status_code, 302)
        submission = ShiftSubmission.objects.get(membership=self.membership, target_month=self.month)
        self.assertTrue(submission.is_submitted)
        self.assertIsNotNone(submission.submitted_at)
        self.assertEqual(submission.submission_mode, "availability")
        self.assertFalse(get_eligibility(self.membership, self.work_date).available)

    def test_empty_day_off_month_can_be_explicitly_submitted(self):
        self.membership.submission_mode = "day_off"
        self.membership.save(update_fields=["submission_mode"])
        response = self.client.post(self.submit_url())
        self.assertEqual(response.status_code, 302)
        submission = ShiftSubmission.objects.get(membership=self.membership, target_month=self.month)
        self.assertTrue(submission.is_submitted)
        self.assertEqual(submission.submission_mode, "day_off")
        self.assertTrue(get_eligibility(self.membership, self.work_date).all_day)

    def test_get_and_repeated_posts_do_not_create_duplicate_months(self):
        response = self.client.get(self.submit_url())
        self.assertIn(response.status_code, [302, 405])
        self.assertFalse(ShiftSubmission.objects.exists())
        for _ in range(2):
            self.assertEqual(self.client.post(self.submit_url()).status_code, 302)
        self.assertEqual(ShiftSubmission.objects.count(), 1)

    def test_attendance_input_is_a_draft_until_monthly_submit(self):
        response = self.save_day("available", start_time="09:00", end_time="18:00")
        self.assertEqual(response.status_code, 302)
        availability = Availability.objects.get()
        submission = ShiftSubmission.objects.get()
        self.assertEqual(availability.submission_id, submission.pk)
        self.assertEqual(submission.membership_id, self.membership.pk)
        self.assertFalse(submission.is_submitted)
        self.assertFalse(get_eligibility(self.membership, self.work_date).available)
        self.client.post(self.submit_url())
        self.assertTrue(get_eligibility(self.membership, self.work_date).covers(time(9), time(18)))

    def test_day_off_and_time_exception_replace_each_other(self):
        self.membership.submission_mode = "day_off"
        self.membership.save(update_fields=["submission_mode"])
        self.assertEqual(self.save_day("day_off").status_code, 302)
        submission = ShiftSubmission.objects.get()
        self.assertTrue(DayOffRequest.objects.filter(submission=submission, work_date=self.work_date).exists())
        self.assertFalse(Availability.objects.exists())
        self.assertEqual(self.save_day("available", start_time="17:00", end_time="22:00").status_code, 302)
        self.assertFalse(DayOffRequest.objects.exists())
        self.assertEqual(Availability.objects.get().start_time, time(17))
        self.assertEqual(self.save_day("day_off").status_code, 302)
        self.assertEqual(DayOffRequest.objects.count(), 1)
        self.assertFalse(Availability.objects.exists())

    def test_all_day_removes_both_time_exception_and_day_off(self):
        self.membership.submission_mode = "day_off"
        self.membership.save(update_fields=["submission_mode"])
        self.save_day("available", start_time="17:00", end_time="22:00")
        response = self.save_day("all_day")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Availability.objects.exists())
        self.assertFalse(DayOffRequest.objects.exists())
        self.assertFalse(ShiftSubmission.objects.get().is_submitted)

    def test_editing_a_previously_submitted_month_requires_resubmission(self):
        submission = self.submission(mode="day_off")
        submitted_at = submission.submitted_at
        response = self.save_day("day_off")
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertFalse(submission.is_submitted)
        self.assertEqual(submission.submitted_at, submitted_at)
        self.assertFalse(get_eligibility(self.membership, date(2030, 11, 13)).available)
        self.client.post(self.submit_url())
        self.assertTrue(get_eligibility(self.membership, date(2030, 11, 13)).available)

    def test_settings_are_per_store_and_existing_submitted_month_keeps_its_mode(self):
        submission = self.submission()
        response = self.client.post(self.settings_url(), {"submission_mode": "day_off"})
        self.assertEqual(response.status_code, 302)
        self.membership.refresh_from_db()
        self.second_membership.refresh_from_db()
        submission.refresh_from_db()
        self.assertEqual(self.membership.submission_mode, "day_off")
        self.assertEqual(self.second_membership.submission_mode, "availability")
        self.assertEqual(submission.submission_mode, "availability")
        self.assertEqual(self.client.post(self.submit_url(month=12)).status_code, 302)
        next_submission = ShiftSubmission.objects.get(membership=self.membership, target_month=date(2030, 12, 1))
        self.assertEqual(next_submission.submission_mode, "day_off")

    def test_posted_user_membership_and_mode_cannot_override_selected_store_identity(self):
        response = self.client.post(self.submit_url(), {
            "user": self.other_employee.pk, "membership": self.other_membership.pk,
            "store": self.other_store.pk, "submission_mode": "day_off",
        })
        self.assertEqual(response.status_code, 302)
        submission = ShiftSubmission.objects.get()
        self.assertEqual(submission.membership, self.membership)
        self.assertEqual(submission.submission_mode, "availability")

    def test_second_store_submission_does_not_submit_first_store(self):
        self.second_membership.submission_mode = "day_off"
        self.second_membership.save(update_fields=["submission_mode"])
        self.assertEqual(self.client.post(self.submit_url(store=self.other_store)).status_code, 302)
        self.assertFalse(ShiftSubmission.objects.filter(membership=self.membership).exists())
        self.assertTrue(get_eligibility(self.second_membership, self.work_date).all_day)
        self.assertFalse(get_eligibility(self.membership, self.work_date).available)

    def test_nonmember_store_is_rejected_without_writes(self):
        company = Company.objects.create(name="他会社")
        forbidden = Store.objects.create(company=company, name="他社店")
        for url in [self.submit_url(store=forbidden), self.settings_url(store=forbidden),
                    self.create_url(store=forbidden)]:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"submission_mode": "day_off"}).status_code, 404)
        self.assertFalse(ShiftSubmission.objects.exists())
        self.assertFalse(Availability.objects.exists())

    def test_disabled_submission_permission_prevents_monthly_submission(self):
        self.membership.can_submit_availability = False
        self.membership.save(update_fields=["can_submit_availability"])
        response = self.client.post(self.submit_url())
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(ShiftSubmission.objects.exists())

    def test_monthly_deadline_rejects_even_an_empty_month(self):
        StoreSubmissionDeadline.objects.create(
            store=self.store, mode="monthly", monthly_deadline_day=15,
        )
        with patch("shifts.submission_deadlines.timezone.now", return_value=timezone.datetime(2030, 10, 16, tzinfo=timezone.get_current_timezone())):
            response = self.client.post(self.submit_url())
        self.assertIn(response.status_code, [200, 302, 403])
        self.assertFalse(ShiftSubmission.objects.filter(is_submitted=True).exists())

    def test_weekly_deadline_uses_earliest_deadline_in_target_month(self):
        StoreSubmissionDeadline.objects.create(store=self.store, mode="weekly", weekly_deadline_weekday=2)
        # 11月1日は金曜。月初の週は10/23締切であり、月内最終週の締切ではない。
        with patch("shifts.submission_deadlines.timezone.now", return_value=timezone.datetime(2030, 10, 24, tzinfo=timezone.get_current_timezone())):
            response = self.client.post(self.submit_url())
        self.assertIn(response.status_code, [200, 302, 403])
        self.assertFalse(ShiftSubmission.objects.filter(is_submitted=True).exists())

    def test_csrf_and_login_protect_monthly_submission_and_settings(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.employee)
        self.assertEqual(csrf_client.post(self.submit_url()).status_code, 403)
        self.assertEqual(csrf_client.post(self.settings_url(), {"submission_mode": "day_off"}).status_code, 403)
        self.client.logout()
        for url in [self.submit_url(), self.settings_url()]:
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(urlparse(response.url).path, reverse("login"))
        self.assertFalse(ShiftSubmission.objects.exists())


class MonthlySubmissionModelValidationTests(MonthlySubmissionFixture):
    def test_target_month_must_be_first_day(self):
        submission = ShiftSubmission(
            membership=self.membership, target_month=self.work_date,
            submission_mode="availability",
        )
        with self.assertRaises(ValidationError):
            submission.full_clean()

    def test_day_off_request_must_belong_to_its_month_and_day_off_mode(self):
        attendance = self.submission()
        day_off = self.submission(self.other_membership, mode="day_off")
        for submission, work_date in [
            (attendance, self.work_date), (day_off, date(2030, 12, 1)),
        ]:
            with self.subTest(submission=submission.pk, work_date=work_date):
                request = DayOffRequest(submission=submission, work_date=work_date)
                with self.assertRaises(ValidationError):
                    request.full_clean()

    def test_day_off_request_and_timed_availability_are_mutually_exclusive(self):
        submission = self.submission(mode="day_off")
        availability = self.availability(submission)
        request = DayOffRequest(submission=submission, work_date=self.work_date)
        with self.assertRaises(ValidationError):
            request.full_clean()
        availability.delete()
        request.save()
        availability = Availability(
            user=self.employee, membership=self.membership, store=self.store,
            submission=submission, work_date=self.work_date, start_time=time(9), end_time=time(18),
        )
        with self.assertRaises(ValidationError):
            availability.full_clean()
