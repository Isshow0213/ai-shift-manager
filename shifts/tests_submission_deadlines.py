from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from .forms import RequirementForm, SubmissionDeadlineForm
from .models import Availability, Requirement, StoreSubmissionDeadline
from .submission_deadlines import (
    get_store_submission_deadline, get_submission_period, is_submission_closed,
)


TOKYO = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 11, 10, 12, tzinfo=TOKYO)


class SubmissionPeriodTests(SimpleTestCase):
    def test_monthly_period_uses_previous_month_and_clamps_to_its_last_day(self):
        for work_date, cutoff_day, start, end, deadline in [
            (date(2026, 3, 1), 15, date(2026, 3, 1), date(2026, 3, 31), date(2026, 2, 15)),
            (date(2026, 3, 31), 31, date(2026, 3, 1), date(2026, 3, 31), date(2026, 2, 28)),
            (date(2028, 3, 15), 31, date(2028, 3, 1), date(2028, 3, 31), date(2028, 2, 29)),
            (date(2027, 1, 31), 31, date(2027, 1, 1), date(2027, 1, 31), date(2026, 12, 31)),
        ]:
            with self.subTest(work_date=work_date, cutoff_day=cutoff_day):
                policy = StoreSubmissionDeadline(mode="monthly", monthly_deadline_day=cutoff_day)
                period = get_submission_period(policy, work_date)
                self.assertEqual((period.start_date, period.end_date, period.deadline_date), (start, end, deadline))
                self.assertEqual(period.closes_at, datetime.combine(deadline + timedelta(days=1), time.min, tzinfo=TOKYO))
                self.assertTrue(timezone.is_aware(period.closes_at))
                self.assertEqual(period.closes_at.utcoffset(), timedelta(hours=9))

    def test_weekly_period_is_monday_to_sunday_and_deadline_is_in_previous_week(self):
        for work_date, start, end in [
            (date(2026, 10, 26), date(2026, 10, 26), date(2026, 11, 1)),
            (date(2026, 11, 1), date(2026, 10, 26), date(2026, 11, 1)),
            (date(2026, 12, 31), date(2026, 12, 28), date(2027, 1, 3)),
        ]:
            for weekday in range(7):
                with self.subTest(work_date=work_date, weekday=weekday):
                    policy = StoreSubmissionDeadline(mode="weekly", weekly_deadline_weekday=weekday)
                    period = get_submission_period(policy, work_date)
                    deadline = start - timedelta(days=7) + timedelta(days=weekday)
                    self.assertEqual((period.start_date, period.end_date, period.deadline_date), (start, end, deadline))
                    self.assertEqual(period.closes_at, datetime.combine(deadline + timedelta(days=1), time.min, tzinfo=TOKYO))

    def test_deadline_includes_entire_day_and_closes_at_japan_midnight(self):
        for policy in [
            StoreSubmissionDeadline(mode="weekly", weekly_deadline_weekday=2),
            StoreSubmissionDeadline(mode="monthly", monthly_deadline_day=15),
        ]:
            with self.subTest(mode=policy.mode):
                work_date = date(2026, 12, 18)
                closes_at = get_submission_period(policy, work_date).closes_at
                self.assertFalse(is_submission_closed(policy, work_date, now=closes_at - timedelta(microseconds=1)))
                self.assertTrue(is_submission_closed(policy, work_date, now=closes_at))
                self.assertTrue(is_submission_closed(policy, work_date, now=closes_at.astimezone(UTC)))
                with patch("shifts.submission_deadlines.timezone.now", return_value=closes_at):
                    self.assertTrue(is_submission_closed(policy, work_date))

    def test_no_policy_does_not_close_submissions(self):
        self.assertFalse(is_submission_closed(None, date(2020, 1, 1), now=NOW))


class SubmissionDeadlineFormTests(SimpleTestCase):
    def test_selected_field_is_required_and_missing_unselected_field_uses_default(self):
        for data, weekday, month_day in [
            ({"mode": "weekly", "weekly_deadline_weekday": "0"}, 0, 15),
            ({"mode": "monthly", "monthly_deadline_day": "31"}, 2, 31),
        ]:
            with self.subTest(data=data):
                form = SubmissionDeadlineForm(data)
                self.assertEqual(set(form.fields), {"mode", "weekly_deadline_weekday", "monthly_deadline_day"})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["weekly_deadline_weekday"], weekday)
                self.assertEqual(form.cleaned_data["monthly_deadline_day"], month_day)
        self.assertNotIn("deadline", RequirementForm().fields)

    def test_unselected_field_keeps_existing_value_and_ignores_posted_tampering(self):
        for data, weekday, month_day in [
            ({"mode": "weekly", "weekly_deadline_weekday": "0", "monthly_deadline_day": "999"}, 0, 26),
            ({"mode": "monthly", "monthly_deadline_day": "31", "weekly_deadline_weekday": "99"}, 6, 31),
        ]:
            with self.subTest(data=data):
                policy = StoreSubmissionDeadline(mode="monthly", weekly_deadline_weekday=6, monthly_deadline_day=26)
                form = SubmissionDeadlineForm(data, instance=policy)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["weekly_deadline_weekday"], weekday)
                self.assertEqual(form.cleaned_data["monthly_deadline_day"], month_day)

    def test_missing_mode_or_selected_value_and_out_of_range_values_are_rejected(self):
        for data in [
            {}, {"mode": "invalid"}, {"mode": "weekly"}, {"mode": "monthly"},
            {"mode": "weekly", "weekly_deadline_weekday": "-1"},
            {"mode": "weekly", "weekly_deadline_weekday": "7"},
            {"mode": "monthly", "monthly_deadline_day": "0"},
            {"mode": "monthly", "monthly_deadline_day": "32"},
        ]:
            with self.subTest(data=data):
                self.assertFalse(SubmissionDeadlineForm(data).is_valid())


class DeadlineFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(username="deadline_manager")
        cls.manager_membership = StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff = user_model.objects.create_user(username="deadline_staff")
        cls.membership = StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.second_membership = StoreMembership.objects.create(user=cls.staff, store=cls.other_store, role="staff")
        cls.foreign_staff = user_model.objects.create_user(username="deadline_foreign")
        cls.foreign_membership = StoreMembership.objects.create(user=cls.foreign_staff, store=cls.other_store, role="staff")
        cls.foreign_policy = StoreSubmissionDeadline.objects.create(
            store=cls.other_store, mode="monthly", monthly_deadline_day=1,
        )

    def calendar_url(self, work_date):
        return (
            f"{reverse('availability_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )


class SubmissionDeadlineSettingsTests(DeadlineFixtures):
    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("manager_submission_deadline")

    def test_get_and_policy_lookup_do_not_create_or_update_settings(self):
        before = list(StoreSubmissionDeadline.objects.order_by("pk").values())
        self.assertIsNone(get_store_submission_deadline(self.store))
        self.assertEqual(get_store_submission_deadline(self.other_store), self.foreign_policy)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)
        StoreSubmissionDeadline.objects.create(store=self.store, mode="weekly", weekly_deadline_weekday=1, monthly_deadline_day=20)
        before = list(StoreSubmissionDeadline.objects.order_by("pk").values())

        response = self.client.get(self.url)

        self.assertEqual(response.context["form"]["mode"].value(), "weekly")
        self.assertEqual(response.context["form"]["weekly_deadline_weekday"].value(), 1)
        self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)

    def test_posts_upsert_managers_store_and_ignore_forged_store_and_id(self):
        before_foreign = StoreSubmissionDeadline.objects.filter(pk=self.foreign_policy.pk).values().get()
        for data, expected_mode, expected_weekday, expected_day in [
            ({"mode": "weekly", "weekly_deadline_weekday": "0"}, "weekly", 0, 15),
            ({"mode": "monthly", "monthly_deadline_day": "31"}, "monthly", 0, 31),
        ]:
            with self.subTest(data=data):
                response = self.client.post(self.url, {
                    **data, "store": self.other_store.pk, "id": self.foreign_policy.pk,
                })
                self.assertRedirects(response, self.url, fetch_redirect_response=False)
                policy = StoreSubmissionDeadline.objects.get(store=self.store)
                self.assertEqual((policy.mode, policy.weekly_deadline_weekday, policy.monthly_deadline_day), (expected_mode, expected_weekday, expected_day))
                self.assertEqual(StoreSubmissionDeadline.objects.filter(store=self.store).count(), 1)
                self.assertEqual(StoreSubmissionDeadline.objects.filter(pk=self.foreign_policy.pk).values().get(), before_foreign)

    def test_invalid_post_preserves_existing_settings_or_leaves_store_unconfigured(self):
        for configured in [False, True]:
            if configured:
                StoreSubmissionDeadline.objects.create(store=self.store, mode="weekly", weekly_deadline_weekday=2)
            before = list(StoreSubmissionDeadline.objects.order_by("pk").values())
            for data in [
                {}, {"mode": "invalid"}, {"mode": "weekly"},
                {"mode": "weekly", "weekly_deadline_weekday": "7"},
                {"mode": "monthly", "monthly_deadline_day": "32"},
            ]:
                with self.subTest(configured=configured, data=data):
                    response = self.client.post(self.url, data)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["form"].errors)
                    self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)

    def test_staff_inactive_manager_and_anonymous_cannot_save_settings(self):
        before = list(StoreSubmissionDeadline.objects.order_by("pk").values())
        data = {"mode": "weekly", "weekly_deadline_weekday": "2"}
        self.client.force_login(self.staff)
        for method in ["get", "post"]:
            response = getattr(self.client, method)(self.url, data if method == "post" else {})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)
        self.client.logout()
        response = self.client.post(self.url, data)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)
        self.assertEqual(list(StoreSubmissionDeadline.objects.order_by("pk").values()), before)

    def test_requirement_form_removes_deadline_but_preserves_legacy_value_on_edit(self):
        legacy_deadline = datetime(2020, 1, 1, 12, tzinfo=UTC)
        requirement = Requirement.objects.create(
            store=self.store, work_date=date(2026, 11, 18), start_time=time(9),
            end_time=time(10), required_staff_count=1, deadline=legacy_deadline,
        )
        response = self.client.get(reverse("manager_requirement_list"))
        self.assertNotIn("deadline", response.context["form"].fields)
        self.assertNotContains(response, 'name="deadline"')
        form = RequirementForm({
            "work_date": "2026-11-18", "start_time": "09:00", "end_time": "10:00",
            "required_staff_count": "2", "deadline": "2099-01-01T00:00",
        }, instance=requirement)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        requirement.refresh_from_db()
        self.assertEqual(requirement.required_staff_count, 2)
        self.assertEqual(requirement.deadline, legacy_deadline)


class EmployeeSubmissionDeadlineTests(DeadlineFixtures):
    def setUp(self):
        time_patch = patch("shifts.submission_deadlines.timezone.now", return_value=NOW)
        time_patch.start()
        self.addCleanup(time_patch.stop)
        self.client.force_login(self.staff)

    def submission_url(self, work_date):
        return (
            f"{reverse('availability_create')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def submit(self, work_date, *, selected_date=None, **overrides):
        data = {"work_date": work_date.isoformat(), "start_time": "09:00", "end_time": "10:00"}
        data.update(overrides)
        return self.client.post(self.submission_url(selected_date or work_date), data)

    def make_availability(self, work_date, *, membership=None, legacy=False):
        return Availability.objects.create(
            user=self.staff, membership=None if legacy else membership or self.membership,
            work_date=work_date, start_time=time(9), end_time=time(10),
        )

    def test_closed_posted_date_cannot_bypass_policy_with_open_get_date_or_foreign_store(self):
        StoreSubmissionDeadline.objects.create(store=self.store, mode="monthly", monthly_deadline_day=15)
        closed_date, open_date = date(2026, 11, 18), date(2026, 12, 18)

        response = self.submit(
            closed_date, selected_date=open_date, store=self.other_store.pk,
            user=self.foreign_staff.pk, membership=self.foreign_membership.pk,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(response.context["form"]["work_date"].value(), closed_date.isoformat())
        self.assertTrue(response.context["submission_closed"])
        self.assertEqual(response.context["selected_date"], closed_date)
        self.assertEqual(response.context["submission_period"].start_date, date(2026, 11, 1))
        self.assertEqual(response.context["submission_period"].end_date, date(2026, 11, 30))
        self.assertFalse(Availability.objects.exists())

    def test_open_posted_date_can_save_from_closed_get_date_and_uses_own_store_policy(self):
        StoreSubmissionDeadline.objects.create(store=self.store, mode="monthly", monthly_deadline_day=15)
        open_date = date(2026, 12, 18)
        self.assertTrue(is_submission_closed(self.foreign_policy, open_date, now=NOW))

        response = self.submit(open_date, selected_date=date(2026, 11, 18), store=self.other_store.pk)

        self.assertRedirects(response, self.calendar_url(open_date), fetch_redirect_response=False)
        availability = Availability.objects.get()
        self.assertEqual(availability.user, self.staff)
        self.assertEqual(availability.membership, self.membership)
        self.assertEqual(availability.work_date, open_date)

    def test_unconfigured_store_allows_submission_and_delete_despite_legacy_requirement_deadline(self):
        work_date = date(2026, 11, 18)
        legacy = Requirement.objects.create(
            store=self.store, work_date=work_date, start_time=time(9), end_time=time(10),
            required_staff_count=1, deadline=datetime(2020, 1, 1, tzinfo=UTC),
        )

        response = self.submit(work_date)

        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        availability = Availability.objects.get()
        self.assertEqual((availability.user, availability.membership), (self.staff, self.membership))
        response = self.client.post(reverse("availability_delete", args=[availability.pk]))
        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        self.assertFalse(Availability.objects.exists())
        legacy.refresh_from_db()
        self.assertEqual(legacy.deadline, datetime(2020, 1, 1, tzinfo=UTC))
        self.assertFalse(StoreSubmissionDeadline.objects.filter(store=self.store).exists())

    def test_weekly_submission_remains_consistent_across_month_boundary(self):
        StoreSubmissionDeadline.objects.create(store=self.store, mode="weekly", weekly_deadline_weekday=2)
        with patch("shifts.submission_deadlines.timezone.now", return_value=datetime(2026, 10, 21, 23, 59, tzinfo=TOKYO)):
            self.client.force_login(self.staff)
            for work_date in [date(2026, 10, 31), date(2026, 11, 1)]:
                response = self.submit(work_date)
                self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        before = list(Availability.objects.order_by("pk").values())
        with patch("shifts.submission_deadlines.timezone.now", return_value=datetime(2026, 10, 22, tzinfo=TOKYO)):
            self.client.force_login(self.staff)
            response = self.submit(date(2026, 10, 30))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].non_field_errors())
            self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
            response = self.submit(date(2026, 11, 2))
            self.assertRedirects(response, self.calendar_url(date(2026, 11, 2)), fetch_redirect_response=False)

    def test_closed_delete_uses_saved_membership_store_and_get_never_deletes(self):
        work_date = date(2026, 12, 18)
        availability = self.make_availability(work_date, membership=self.second_membership)
        url = reverse("availability_delete", args=[availability.pk])
        before = list(Availability.objects.values())
        self.assertIsNone(get_store_submission_deadline(self.store))

        response = self.client.get(url)

        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        self.assertEqual(list(Availability.objects.values()), before)
        response = self.client.post(url)
        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        self.assertEqual(list(Availability.objects.values()), before)
        self.assertTrue(list(get_messages(response.wsgi_request)))

    def test_calendar_closed_flags_and_delete_links_follow_each_saved_store(self):
        work_date = date(2026, 12, 18)
        closed = self.make_availability(work_date, membership=self.second_membership)
        own = Availability.objects.create(
            user=self.staff, membership=self.membership, work_date=work_date,
            start_time=time(10), end_time=time(11),
        )
        legacy = Availability.objects.create(
            user=self.staff, membership=None, work_date=work_date,
            start_time=time(12), end_time=time(13),
        )
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.get(self.calendar_url(work_date))

        rows = {availability.pk: availability for availability in response.context["selected_availabilities"]}
        self.assertTrue(rows[closed.pk].submission_closed)
        self.assertFalse(rows[own.pk].submission_closed)
        self.assertFalse(rows[legacy.pk].submission_closed)
        self.assertFalse(response.context["submission_closed"])
        selected_day = next(
            day for week in response.context["calendar_weeks"] for day in week if day["date"] == work_date
        )
        self.assertFalse(selected_day["submission_closed"])
        self.assertNotContains(response, reverse("availability_delete", args=[closed.pk]))
        self.assertContains(response, reverse("availability_delete", args=[own.pk]))
        self.assertContains(response, reverse("availability_delete", args=[legacy.pk]))
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_delete_can_use_open_saved_store_even_when_current_store_is_closed(self):
        work_date = date(2026, 12, 18)
        StoreSubmissionDeadline.objects.create(store=self.store, mode="monthly", monthly_deadline_day=1)
        self.foreign_policy.delete()
        availability = self.make_availability(work_date, membership=self.second_membership)

        response = self.client.post(reverse("availability_delete", args=[availability.pk]))

        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        self.assertFalse(Availability.objects.filter(pk=availability.pk).exists())

    def test_legacy_null_membership_delete_uses_current_store_deadline(self):
        StoreSubmissionDeadline.objects.create(store=self.store, mode="monthly", monthly_deadline_day=15)
        work_date = date(2026, 11, 18)
        availability = self.make_availability(work_date, legacy=True)
        before = list(Availability.objects.values())

        response = self.client.post(reverse("availability_delete", args=[availability.pk]))

        self.assertRedirects(response, self.calendar_url(work_date), fetch_redirect_response=False)
        self.assertEqual(list(Availability.objects.values()), before)

    def test_without_active_membership_user_still_cannot_delete_someone_elses_availability(self):
        availability = Availability.objects.create(
            user=self.foreign_staff, membership=self.foreign_membership,
            work_date=date(2026, 12, 18), start_time=time(9), end_time=time(10),
        )
        StoreMembership.objects.filter(user=self.staff).update(is_active=False)
        before = list(Availability.objects.values())
        for method in ["get", "post"]:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse("availability_delete", args=[availability.pk]))
                self.assertEqual(response.status_code, 404)
                self.assertEqual(list(Availability.objects.values()), before)
