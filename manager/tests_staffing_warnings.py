from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import HelpShift, Requirement, Shift, StaffingWarningDismissal
from shifts.staffing import get_staffing_status
from shifts.testing import submitted_availability


class StaffingWarningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="人数不足テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        users = get_user_model()
        cls.manager = users.objects.create_user(username="staffing_manager")
        cls.manager_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.manager, role="manager",
        )
        cls.staff = users.objects.create_user(username="staffing_staff")
        cls.membership = StoreMembership.objects.create(store=cls.store, user=cls.staff)
        cls.other_manager = users.objects.create_user(username="staffing_other_manager")
        StoreMembership.objects.create(store=cls.other_store, user=cls.other_manager, role="manager")
        cls.work_date = date(2030, 11, 12)
        cls.requirement = Requirement.objects.create(
            store=cls.store, work_date=cls.work_date,
            start_time=time(9), end_time=time(18), required_staff_count=2,
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.update_url = reverse("manager_staffing_warning_update")

    def calendar_url(self, work_date=None):
        work_date = work_date or self.work_date
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def calendar_day(self, response, work_date=None):
        return next(
            day for week in response.context["calendar_weeks"] for day in week
            if day["date"] == (work_date or self.work_date)
        )

    def update_data(self, action="dismiss", **overrides):
        data = {
            "work_date": self.work_date.isoformat(), "action": action,
            "staffing_signature": get_staffing_status(self.store, self.work_date)["staffing_signature"],
        }
        data.update(overrides)
        return data

    def make_shift(self, **overrides):
        fields = {
            "store": self.store, "work_date": self.work_date,
            "user": self.staff, "membership": self.membership,
            "start_time": time(9), "end_time": time(18),
        }
        fields.update(overrides)
        return Shift.objects.create(**fields)

    def test_shortage_is_visible_without_shifts_and_preferences_do_not_fill_it(self):
        submitted_availability(
            user=self.staff, membership=self.membership, store=self.store,
            work_date=self.work_date, start_time=time(9), end_time=time(18),
        )
        response = self.client.get(self.calendar_url())

        self.assertTrue(self.calendar_day(response)["show_staffing_warning"])
        self.assertEqual(self.calendar_day(response)["max_staffing_shortfall"], 2)
        self.assertContains(response, "staffing-warning-badge")
        self.assertContains(response, "必要人数に達していません")
        self.assertContains(response, "has-staffing-warning")
        status = response.context["selected_staffing"]
        self.assertEqual(status["shortages"][0]["scheduled_staff_count"], 0)
        self.assertContains(response, f'action="{self.update_url}"')
        self.assertContains(response, 'name="action" value="dismiss"')

    def test_sufficient_shifts_and_help_clear_warning_automatically(self):
        self.make_shift(is_generated=True)
        HelpShift.objects.create(
            store=self.store, work_date=self.work_date, name="応援担当",
            start_time=time(9), end_time=time(18),
        )
        response = self.client.get(self.calendar_url())

        self.assertFalse(self.calendar_day(response)["show_staffing_warning"])
        self.assertFalse(response.context["selected_staffing"]["has_shortage"])
        self.assertNotContains(response, 'name="action" value="dismiss"')

    def test_no_requirements_means_no_shortage(self):
        work_date = self.work_date + timedelta(days=1)
        response = self.client.get(self.calendar_url(work_date))

        self.assertIsNone(response.context["selected_staffing"])
        self.assertFalse(self.calendar_day(response, work_date)["show_staffing_warning"])
        self.assertNotContains(response, 'name="action" value="dismiss"')

    def test_holiday_and_shortage_states_coexist(self):
        work_date = date(2026, 11, 3)
        Requirement.objects.create(
            store=self.store, work_date=work_date,
            start_time=time(9), end_time=time(18), required_staff_count=1,
        )
        response = self.client.get(self.calendar_url(work_date))

        day = self.calendar_day(response, work_date)
        self.assertTrue(day["is_holiday"])
        self.assertTrue(day["is_selected"])
        self.assertTrue(day["show_staffing_warning"])
        self.assertContains(response, "is-holiday is-today" if day["is_today"] else "is-holiday is-selected has-staffing-warning")

    def test_dismissal_persists_without_changing_shifts_or_requirements_and_can_be_restored(self):
        self.make_shift()
        before_requirements = list(Requirement.objects.values())
        before_shifts = list(Shift.objects.values())
        response = self.client.post(self.update_url, self.update_data(), follow=True)

        self.assertRedirects(response, self.calendar_url())
        dismissal = StaffingWarningDismissal.objects.get()
        self.assertEqual(dismissal.dismissed_by, self.manager)
        self.assertEqual(dismissal.store, self.store)
        self.assertEqual(dismissal.work_date, self.work_date)
        day = self.calendar_day(response)
        self.assertFalse(day["show_staffing_warning"])
        self.assertTrue(day["staffing_warning_dismissed"])
        self.assertTrue(response.context["selected_staffing"]["has_shortage"])
        self.assertContains(response, "店長確認済み")
        self.assertContains(response, 'name="action" value="restore"')
        self.assertEqual(list(Requirement.objects.values()), before_requirements)
        self.assertEqual(list(Shift.objects.values()), before_shifts)

        # A new client session sees the saved decision too.
        second_client = Client()
        second_client.force_login(self.manager)
        self.assertTrue(self.calendar_day(second_client.get(self.calendar_url()))["staffing_warning_dismissed"])

        response = self.client.post(self.update_url, self.update_data("restore"), follow=True)
        self.assertFalse(StaffingWarningDismissal.objects.exists())
        self.assertTrue(self.calendar_day(response)["show_staffing_warning"])

    def test_changed_requirement_shift_and_help_reopen_dismissed_warning(self):
        shift = self.make_shift()
        self.client.post(self.update_url, self.update_data())
        self.requirement.required_staff_count = 3
        self.requirement.save(update_fields=["required_staff_count"])
        self.assertTrue(self.calendar_day(self.client.get(self.calendar_url()))["show_staffing_warning"])

        self.client.post(self.update_url, self.update_data())
        shift.start_time = time(10)
        shift.save(update_fields=["start_time"])
        self.assertTrue(self.calendar_day(self.client.get(self.calendar_url()))["show_staffing_warning"])

        self.client.post(self.update_url, self.update_data())
        HelpShift.objects.create(
            store=self.store, work_date=self.work_date, name="応援担当",
            start_time=time(9), end_time=time(18),
        )
        self.assertTrue(self.calendar_day(self.client.get(self.calendar_url()))["show_staffing_warning"])

    def test_stale_confirmation_cannot_dismiss_new_staffing(self):
        data = self.update_data()
        self.make_shift()
        response = self.client.post(self.update_url, data, follow=True)

        self.assertFalse(StaffingWarningDismissal.objects.exists())
        self.assertTrue(self.calendar_day(response)["show_staffing_warning"])
        self.assertContains(response, "現在の内容を確認してから警告を解除してください。")

    def test_notes_and_colors_do_not_reopen_warning(self):
        shift = self.make_shift()
        self.client.post(self.update_url, self.update_data())
        shift.note, shift.display_color = "レジ担当", "red"
        shift.save(update_fields=["note", "display_color"])
        self.requirement.memo = "店長判断で対応可能"
        self.requirement.save(update_fields=["memo"])
        self.assertTrue(self.calendar_day(self.client.get(self.calendar_url()))["staffing_warning_dismissed"])

    def test_store_and_day_decisions_are_isolated_and_posted_store_is_ignored(self):
        other_day = self.work_date + timedelta(days=1)
        for store, work_date in ((self.store, other_day), (self.other_store, self.work_date)):
            Requirement.objects.create(
                store=store, work_date=work_date,
                start_time=time(9), end_time=time(18), required_staff_count=2,
            )
        self.make_shift(store=self.other_store)
        response = self.client.post(
            self.update_url, self.update_data(store=self.other_store.pk), follow=True,
        )
        self.assertEqual(StaffingWarningDismissal.objects.get().store, self.store)
        self.assertTrue(self.calendar_day(response, other_day)["show_staffing_warning"])
        self.client.force_login(self.other_manager)
        response = self.client.get(self.calendar_url())
        self.assertTrue(self.calendar_day(response)["show_staffing_warning"])
        self.assertFalse(self.calendar_day(response)["staffing_warning_dismissed"])
        self.client.post(self.update_url, self.update_data("restore"))
        self.assertTrue(StaffingWarningDismissal.objects.filter(store=self.store).exists())

    def test_dismissal_is_idempotent_and_does_not_create_records_for_fully_staffed_days(self):
        data = self.update_data()
        self.client.post(self.update_url, data)
        self.client.post(self.update_url, data)
        self.assertEqual(StaffingWarningDismissal.objects.count(), 1)
        StaffingWarningDismissal.objects.all().delete()
        Requirement.objects.filter(pk=self.requirement.pk).update(required_staff_count=1)
        self.make_shift()
        self.client.post(self.update_url, self.update_data())
        self.assertFalse(StaffingWarningDismissal.objects.exists())
        Requirement.objects.all().delete()
        self.client.post(self.update_url, data)
        self.assertFalse(StaffingWarningDismissal.objects.exists())

    def test_only_active_store_managers_can_update_and_get_does_not_mutate(self):
        data = self.update_data()
        self.assertEqual(self.client.get(self.update_url).status_code, 405)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.update_url, data).status_code, 403)
        self.client.force_login(self.manager)
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.assertEqual(self.client.post(self.update_url, data).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.update_url, data).status_code, 302)
        self.assertFalse(StaffingWarningDismissal.objects.exists())

    def test_invalid_date_action_or_missing_signature_cannot_change_state(self):
        for overrides in (
            {"work_date": "not-a-date"}, {"work_date": "0001-01-01"},
            {"work_date": "9999-01-01"}, {"action": "delete"}, {"staffing_signature": ""},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.post(self.update_url, self.update_data(**overrides))
                self.assertEqual(response.status_code, 400)
        self.assertFalse(StaffingWarningDismissal.objects.exists())

    def test_csrf_is_required_for_dismissal(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        client.get(self.calendar_url())
        data = self.update_data()
        self.assertEqual(client.post(self.update_url, data).status_code, 403)
        self.assertFalse(StaffingWarningDismissal.objects.exists())
        response = client.post(self.update_url, data, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(StaffingWarningDismissal.objects.exists())

    def test_month_staffing_checks_do_not_query_once_per_day(self):
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(self.calendar_url())
        for offset in range(1, 16):
            Requirement.objects.create(
                store=self.store, work_date=self.work_date + timedelta(days=offset),
                start_time=time(9), end_time=time(18), required_staff_count=2,
            )
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.calendar_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(expanded), len(baseline))
