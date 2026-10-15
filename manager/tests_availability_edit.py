from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift, StoreSubmissionDeadline
from shifts.submission_deadlines import is_submission_closed


class ManagerAvailabilityEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="希望編集テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(username="availability_edit_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff = user_model.objects.create_user(
            username="availability_edit_staff", last_name="田中", first_name="花子"
        )
        cls.membership = StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.other_store_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="staff"
        )
        cls.other_staff = user_model.objects.create_user(username="availability_edit_other_staff")
        cls.other_staff_membership = StoreMembership.objects.create(
            user=cls.other_staff, store=cls.store, role="staff"
        )
        foreign_staff = user_model.objects.create_user(username="availability_edit_foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            user=foreign_staff, store=cls.other_store, role="staff"
        )
        cls.work_date = date(2026, 11, 12)
        cls.availability = Availability.objects.create(
            user=cls.staff, membership=cls.membership, work_date=cls.work_date,
            start_time=time(9), end_time=time(17),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.edit_url = self.edit_for(self.availability)
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 12)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def edit_for(self, availability):
        return reverse("manager_availability_edit", args=[availability.pk])

    def list_for(self, work_date):
        return (
            f"{reverse('manager_availability_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def employee_calendar_for(self, work_date):
        return (
            f"{reverse('availability_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def edit_data(self, **overrides):
        data = {
            "work_date": self.work_date.isoformat(), "start_time": "09:00", "end_time": "17:00",
        }
        data.update(overrides)
        return data

    def make_availability(self, membership, work_date=None, start_time=time(9), end_time=time(17)):
        return Availability.objects.create(
            user=membership.user, membership=membership,
            work_date=work_date or self.work_date, start_time=start_time, end_time=end_time,
        )

    def test_get_shows_existing_date_and_times_without_writing(self):
        before = list(Availability.objects.order_by("pk").values())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.edit_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "田中 花子")
        form = response.context["form"]
        self.assertEqual(set(form.fields), {"work_date", "start_time", "end_time"})
        self.assertEqual(form["work_date"].value(), self.work_date)
        self.assertEqual(form["start_time"].value(), time(9))
        self.assertEqual(form["end_time"].value(), time(17))
        self.assertEqual(response.context["selected_date"], self.work_date)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
        self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        )], [])

    def test_manager_can_change_date_and_times_and_returns_to_the_new_month(self):
        new_date = date(2026, 12, 3)

        response = self.client.post(self.edit_url, self.edit_data(
            work_date=new_date.isoformat(), start_time="10:15", end_time="16:30",
        ))

        self.assertRedirects(response, self.list_for(new_date), fetch_redirect_response=False)
        self.availability.refresh_from_db()
        self.assertEqual(self.availability.work_date, new_date)
        self.assertEqual(self.availability.start_time, time(10, 15))
        self.assertEqual(self.availability.end_time, time(16, 30))
        self.assertEqual(Availability.objects.count(), 1)

    def test_edit_keeps_owner_membership_and_creation_time_even_with_tampered_fields(self):
        original_created_at = self.availability.created_at

        response = self.client.post(self.edit_url, self.edit_data(
            start_time="10:00", end_time="16:00", user=self.other_staff.pk,
            membership=self.foreign_membership.pk, store=self.other_store.pk,
            created_at="2000-01-01T00:00:00Z",
        ))

        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)
        self.availability.refresh_from_db()
        self.assertEqual(self.availability.user, self.staff)
        self.assertEqual(self.availability.membership, self.membership)
        self.assertEqual(self.availability.created_at, original_created_at)

    def test_unchanged_availability_does_not_overlap_itself(self):
        response = self.client.post(self.edit_url, self.edit_data())

        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)
        self.assertEqual(Availability.objects.count(), 1)

    def test_reversed_or_equal_times_keep_saved_values_and_entered_values(self):
        before = list(Availability.objects.values())
        for start_time, end_time in [("17:00", "09:00"), ("09:00", "09:00")]:
            with self.subTest(start_time=start_time, end_time=end_time):
                response = self.client.post(self.edit_url, self.edit_data(
                    start_time=start_time, end_time=end_time,
                ))

                self.assertEqual(response.status_code, 200)
                form = response.context["form"]
                self.assertTrue(form.non_field_errors())
                self.assertEqual(form["start_time"].value(), start_time)
                self.assertEqual(form["end_time"].value(), end_time)
                self.assertEqual(response.context["original_work_date"], self.work_date)
                self.assertEqual(response.context["original_start_time"], time(9))
                self.assertEqual(response.context["original_end_time"], time(17))
                self.assertEqual(list(Availability.objects.values()), before)

    def test_invalid_dates_and_year_bounds_do_not_save(self):
        before = list(Availability.objects.values())
        for invalid_date in ["invalid", "2026-02-30", "0001-01-01", "9999-12-31"]:
            with self.subTest(invalid_date=invalid_date):
                response = self.client.post(self.edit_url, self.edit_data(work_date=invalid_date))

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors.get("work_date"))
                self.assertEqual(response.context["form"]["work_date"].value(), invalid_date)
                self.assertEqual(list(Availability.objects.values()), before)
                self.assertEqual(response.context["original_work_date"], self.work_date)

    def test_other_store_wish_does_not_block_editing_this_stores_wish(self):
        new_date = date(2026, 11, 13)
        self.make_availability(
            self.other_store_membership, work_date=new_date, start_time=time(10), end_time=time(12)
        )
        other_before = list(Availability.objects.filter(membership=self.other_store_membership).values())

        response = self.client.post(self.edit_url, self.edit_data(
            work_date=new_date.isoformat(), start_time="09:00", end_time="11:00",
            user=self.other_staff.pk,
        ))

        self.assertRedirects(response, self.list_for(new_date), fetch_redirect_response=False)
        self.availability.refresh_from_db()
        self.assertEqual(self.availability.user, self.staff)
        self.assertEqual(self.availability.membership, self.membership)
        self.assertEqual(self.availability.work_date, new_date)
        self.assertEqual(self.availability.start_time, time(9))
        self.assertEqual(self.availability.end_time, time(11))
        self.assertEqual(list(Availability.objects.filter(membership=self.other_store_membership).values()), other_before)

    def test_adjacent_wishes_are_allowed(self):
        self.make_availability(self.other_store_membership, start_time=time(17), end_time=time(18))

        response = self.client.post(self.edit_url, self.edit_data())

        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)

    def test_another_employees_wish_does_not_block_the_edit(self):
        self.make_availability(self.other_staff_membership)

        response = self.client.post(self.edit_url, self.edit_data(start_time="10:00", end_time="16:00"))

        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)

    def test_manager_can_edit_after_the_employee_submission_deadline(self):
        policy = StoreSubmissionDeadline.objects.create(
            store=self.store, mode=StoreSubmissionDeadline.Mode.MONTHLY, monthly_deadline_day=15
        )
        closed_date = date(2020, 11, 12)
        self.availability.work_date = closed_date
        self.availability.save(update_fields=["work_date"])
        self.assertTrue(is_submission_closed(policy, closed_date))

        response = self.client.post(self.edit_url, self.edit_data(
            work_date=closed_date.isoformat(), start_time="10:00", end_time="16:00",
        ))

        self.assertRedirects(response, self.list_for(closed_date), fetch_redirect_response=False)
        self.availability.refresh_from_db()
        self.assertEqual(self.availability.start_time, time(10))

    def test_edit_leaves_confirmed_shifts_unchanged(self):
        Shift.objects.create(
            user=self.staff, membership=self.membership, store=self.store,
            work_date=self.work_date, start_time=time(9), end_time=time(17),
            note="確定シフト", display_color="green", is_generated=True,
        )
        before = list(Shift.objects.order_by("pk").values())

        response = self.client.post(self.edit_url, self.edit_data(
            work_date="2026-12-03", start_time="10:00", end_time="16:00",
        ))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before)

    def test_employee_calendar_immediately_shows_the_edited_wish(self):
        new_date = date(2026, 12, 3)
        self.client.post(self.edit_url, self.edit_data(
            work_date=new_date.isoformat(), start_time="10:00", end_time="16:00",
        ))
        self.client.force_login(self.staff)

        old_calendar = self.client.get(self.employee_calendar_for(self.work_date))
        new_calendar = self.client.get(self.employee_calendar_for(new_date))

        self.assertEqual(list(old_calendar.context["selected_availabilities"]), [])
        self.assertEqual(list(new_calendar.context["selected_availabilities"]), [self.availability])
        self.assertContains(new_calendar, "10:00")
        self.assertContains(new_calendar, "16:00")

    def test_same_store_inactive_membership_can_still_be_edited(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        response = self.client.post(self.edit_url, self.edit_data(start_time="10:00", end_time="16:00"))

        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)

    def test_foreign_null_membership_and_nonexistent_wishes_are_not_accessible(self):
        foreign = self.make_availability(self.foreign_membership)
        legacy = Availability.objects.create(
            user=self.staff, membership=None, work_date=self.work_date,
            start_time=time(18), end_time=time(19),
        )
        before = list(Availability.objects.order_by("pk").values())
        urls = [self.edit_for(foreign), self.edit_for(legacy), reverse("manager_availability_edit", args=[999999])]
        for url in urls:
            for method in [self.client.get, self.client.post]:
                with self.subTest(url=url, method=method.__name__):
                    response = method(url, self.edit_data())

                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_staff_and_inactive_manager_cannot_edit(self):
        before = list(Availability.objects.values())
        for user in [self.staff, self.manager]:
            with self.subTest(user=user.pk):
                if user == self.manager:
                    self.manager_membership.is_active = False
                    self.manager_membership.save(update_fields=["is_active"])
                self.client.force_login(user)
                for method in [self.client.get, self.client.post]:
                    response = method(self.edit_url, self.edit_data())

                    self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
                    self.assertEqual(list(Availability.objects.values()), before)

    def test_anonymous_users_cannot_edit(self):
        self.client.logout()
        before = list(Availability.objects.values())
        for method in [self.client.get, self.client.post]:
            response = method(self.edit_url, self.edit_data())

            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
            self.assertEqual(list(Availability.objects.values()), before)

    def test_edit_is_get_or_post_only(self):
        before = list(Availability.objects.values())
        for method in [self.client.put, self.client.patch, self.client.delete]:
            response = method(self.edit_url, self.edit_data())

            self.assertEqual(response.status_code, 405)
            self.assertEqual(list(Availability.objects.values()), before)

    def test_edit_requires_csrf_and_accepts_a_valid_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        before = list(Availability.objects.values())

        response = csrf_client.post(self.edit_url, self.edit_data(start_time="10:00", end_time="16:00"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(Availability.objects.values()), before)
        csrf_client.get(self.edit_url)
        response = csrf_client.post(self.edit_url, self.edit_data(
            start_time="10:00", end_time="16:00",
            csrfmiddlewaretoken=csrf_client.cookies["csrftoken"].value,
        ))
        self.assertRedirects(response, self.list_for(self.work_date), fetch_redirect_response=False)
