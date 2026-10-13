from datetime import date, time
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, CompanyMembership, Store, StoreMembership, StoreShiftViewingPermission
from .models import Availability, Requirement, Shift, StoreSubmissionDeadline


class CompanyAvailabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.company = Company.objects.create(name="会社提出テスト会社")
        cls.foreign_company = Company.objects.create(name="他社")
        cls.store = Store.objects.create(company=cls.company, name="本店")
        cls.other_store = Store.objects.create(company=cls.company, name="分店")
        cls.foreign_store = Store.objects.create(company=cls.foreign_company, name="他社店")
        cls.manager = user_model.objects.create_user(username="company_submit_manager")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.company_user = user_model.objects.create_user(username="company_submit_user")
        cls.company_membership = CompanyMembership.objects.create(user=cls.company_user, company=cls.company, role="member")
        cls.permission = StoreShiftViewingPermission.objects.create(
            company_membership=cls.company_membership, store=cls.store, created_by=cls.manager,
            can_view_shifts=False, can_submit_availability=True,
        )
        cls.staff = user_model.objects.create_user(username="company_submit_staff")
        cls.staff_membership = StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.work_date = date(2030, 1, 12)
        cls.staff_availability = Availability.objects.create(
            user=cls.staff, membership=cls.staff_membership, work_date=cls.work_date,
            start_time=time(9), end_time=time(17),
        )

    def setUp(self):
        self.client.force_login(self.company_user)
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 13)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def create_url(self, store=None, work_date=None):
        return f"{reverse('availability_create')}?{urlencode({
            'store': (store or self.store).pk, 'date': (work_date or self.work_date).isoformat(),
        })}"

    def calendar_url(self, store=None, work_date=None):
        store = store or self.store
        work_date = work_date or self.work_date
        return f"{reverse('availability_list')}?{urlencode({
            'store': store.pk, 'year': work_date.year, 'month': work_date.month,
            'date': work_date.isoformat(),
        })}"

    def post_data(self, **extra):
        return {"work_date": self.work_date.isoformat(), "start_time": "10:00", "end_time": "16:00", **extra}

    def make_company_availability(self, *, store=None, user=None, work_date=None, start_time=time(10), end_time=time(16)):
        return Availability.objects.create(
            user=user or self.company_user, membership=None, store=store or self.store,
            work_date=work_date or self.work_date, start_time=start_time, end_time=end_time,
        )

    def assert_calendar_redirect(self, response, store=None, work_date=None):
        self.assertEqual(response.status_code, 302)
        expected = urlsplit(self.calendar_url(store, work_date))
        actual = urlsplit(response.url)
        self.assertEqual(actual.path, expected.path)
        self.assertEqual(parse_qs(actual.query), parse_qs(expected.query))

    def test_company_submission_records_owner_and_store_without_staff_membership(self):
        memberships_before = list(StoreMembership.objects.order_by("pk").values())

        response = self.client.post(self.create_url(), self.post_data(
            user=self.staff.pk, membership=self.staff_membership.pk, store=self.other_store.pk,
        ))

        self.assert_calendar_redirect(response)
        availability = Availability.objects.get(user=self.company_user)
        self.assertIsNone(availability.membership)
        self.assertEqual(availability.store, self.store)
        self.assertEqual(availability.start_time, time(10))
        self.assertEqual(availability.end_time, time(16))
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)

    def test_calendar_only_displays_own_wishes_in_the_selected_company_store(self):
        own = self.make_company_availability()
        self.make_company_availability(store=self.other_store, start_time=time(18), end_time=time(19))
        self.make_company_availability(user=self.staff, start_time=time(20), end_time=time(21))
        Availability.objects.create(
            user=self.company_user, membership=None, store=None, work_date=self.work_date,
            start_time=time(2), end_time=time(3),
        )

        response = self.client.get(self.calendar_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["selected_availabilities"]), [own])
        self.assertEqual(response.context["submitted_days_count"], 1)
        days = {day["date"]: day for week in response.context["calendar_weeks"] for day in week}
        self.assertEqual(days[self.work_date]["availabilities"], [own])
        self.assertNotContains(response, "18:00")
        self.assertNotContains(response, "20:00")
        self.assertNotContains(response, "02:00")

    def test_company_user_can_submit_and_delete_only_while_the_store_capability_is_active(self):
        availability = self.make_company_availability()
        response = self.client.post(reverse("availability_delete", args=[availability.pk]))
        self.assert_calendar_redirect(response)
        self.assertFalse(Availability.objects.filter(pk=availability.pk).exists())
        availability = self.make_company_availability()
        self.permission.can_submit_availability = False
        self.permission.can_view_shifts = True
        self.permission.save(update_fields=["can_submit_availability", "can_view_shifts"])
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(reverse("availability_delete", args=[availability.pk]))

        self.assertIn(response.status_code, [302, 403, 404])
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_read_only_revoked_ungranted_and_cross_company_posts_never_create_wishes(self):
        before = list(Availability.objects.order_by("pk").values())
        for view, submit, active in [(True, False, True), (False, True, False), (False, False, True)]:
            with self.subTest(view=view, submit=submit, active=active):
                self.permission.can_view_shifts = view
                self.permission.can_submit_availability = submit
                self.permission.is_active = active
                self.permission.save(update_fields=["can_view_shifts", "can_submit_availability", "is_active"])
                response = self.client.post(self.create_url(), self.post_data())
                self.assertIn(response.status_code, [302, 403, 404])
                self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
        self.permission.can_submit_availability = True
        self.permission.is_active = True
        self.permission.save(update_fields=["can_submit_availability", "is_active"])
        StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=self.foreign_store,
            created_by=self.manager, can_view_shifts=True, can_submit_availability=True,
        )
        for store in [self.other_store, self.foreign_store]:
            with self.subTest(store=store.pk):
                response = self.client.post(self.create_url(store), self.post_data())
                self.assertEqual(response.status_code, 404)
                self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_company_user_cannot_delete_another_users_wish(self):
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(reverse("availability_delete", args=[self.staff_availability.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_submission_and_deletion_respect_the_selected_stores_deadline(self):
        StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=self.other_store, created_by=self.manager,
            can_view_shifts=False, can_submit_availability=True,
        )
        StoreSubmissionDeadline.objects.create(
            store=self.other_store, mode=StoreSubmissionDeadline.Mode.MONTHLY, monthly_deadline_day=15,
        )
        closed_date = date(2020, 11, 12)
        availability = self.make_company_availability(store=self.other_store, work_date=closed_date)
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(self.create_url(self.other_store, closed_date), self.post_data(
            work_date=closed_date.isoformat(), start_time="18:00", end_time="19:00",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
        response = self.client.post(reverse("availability_delete", args=[availability.pk]))
        self.assert_calendar_redirect(response, self.other_store, closed_date)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_invalid_time_range_and_overlap_keep_input_and_do_not_save(self):
        self.make_company_availability(start_time=time(10), end_time=time(12))
        before = list(Availability.objects.order_by("pk").values())
        for start, end in [("12:00", "10:00"), ("10:00", "10:00"), ("11:00", "13:00")]:
            with self.subTest(start=start, end=end):
                response = self.client.post(self.create_url(), self.post_data(start_time=start, end_time=end))

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(response.context["form"]["start_time"].value(), start)
                self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_staff_self_submission_and_company_submission_to_another_store_stay_independent(self):
        staff_company_membership = CompanyMembership.objects.create(user=self.staff, company=self.company, role="member")
        StoreShiftViewingPermission.objects.create(
            company_membership=staff_company_membership, store=self.other_store, created_by=self.manager,
            can_view_shifts=True, can_submit_availability=True,
        )
        self.client.force_login(self.staff)
        new_date = date(2030, 1, 13)
        response = self.client.post(reverse("availability_create"), self.post_data(work_date=new_date.isoformat()))
        self.assertEqual(response.status_code, 302)
        staff_wish = Availability.objects.get(user=self.staff, work_date=new_date)
        self.assertEqual(staff_wish.membership, self.staff_membership)

        response = self.client.post(self.create_url(self.other_store, new_date), self.post_data(
            work_date=new_date.isoformat(), start_time="17:00", end_time="18:00",
        ))

        self.assert_calendar_redirect(response, self.other_store, new_date)
        company_wish = Availability.objects.get(user=self.staff, membership=None, work_date=new_date)
        self.assertEqual(company_wish.store, self.other_store)
        self.assertEqual(StoreMembership.objects.filter(user=self.staff).count(), 1)
        self.assertEqual(self.client.get(reverse("availability_list"), {"year": 2030, "month": 1}).status_code, 200)

    def test_manager_list_selected_day_and_dashboard_include_company_wishes_separately(self):
        company_wish = self.make_company_availability()
        self.client.force_login(self.manager)
        query = {"year": 2030, "month": 1, "date": self.work_date.isoformat()}

        response = self.client.get(reverse("manager_availability_list"), query)
        self.assertCountEqual(response.context["availabilities"], [self.staff_availability, company_wish])
        response = self.client.get(reverse("manager_shift_list"), query)
        self.assertCountEqual(response.context["availabilities"], [self.staff_availability, company_wish])
        for form_name in ["form", "outside_form"]:
            self.assertNotIn(self.company_user.pk, response.context[form_name].fields["membership"].queryset.values_list("user_id", flat=True))
        response = self.client.get(reverse("manager_dashboard"), query)
        self.assertEqual(response.context["total_staff_count"], 1)
        self.assertEqual(response.context["submitted_count"], 1)
        self.assertEqual(response.context["company_submitters_count"], 1)
        self.assertEqual(response.context["company_submitted_count"], 1)
        self.assertEqual(response.context["company_unsubmitted_count"], 0)

    def test_manager_store_queries_keep_membership_as_the_canonical_store(self):
        self.staff_availability.store = self.other_store
        self.staff_availability.save(update_fields=["store"])
        self.make_company_availability(store=self.other_store)
        self.client.force_login(self.manager)

        response = self.client.get(reverse("manager_availability_list"), {"year": 2030, "month": 1})

        self.assertEqual(list(response.context["availabilities"]), [self.staff_availability])

    def test_manager_edits_company_wish_without_changing_owner_store_or_nullable_membership(self):
        company_wish = self.make_company_availability()
        original_created_at = company_wish.created_at
        self.client.force_login(self.manager)
        edit_url = reverse("manager_availability_edit", args=[company_wish.pk])
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.company_user.full_name_japanese)

        response = self.client.post(edit_url, {
            "work_date": "2030-01-13", "start_time": "11:00", "end_time": "15:00",
            "store": self.other_store.pk, "membership": self.staff_membership.pk, "user": self.staff.pk,
        })

        self.assertRedirects(response, (
            f"{reverse('manager_availability_list')}?year=2030&month=1&date=2030-01-13"
        ), fetch_redirect_response=False)
        company_wish.refresh_from_db()
        self.assertEqual(company_wish.work_date, date(2030, 1, 13))
        self.assertEqual(company_wish.start_time, time(11))
        self.assertEqual(company_wish.end_time, time(15))
        self.assertEqual(company_wish.user, self.company_user)
        self.assertEqual(company_wish.store, self.store)
        self.assertIsNone(company_wish.membership)
        self.assertEqual(company_wish.created_at, original_created_at)

    def test_manager_cannot_edit_company_wishes_in_another_store(self):
        company_wish = self.make_company_availability(store=self.other_store)
        self.client.force_login(self.manager)
        before = list(Availability.objects.order_by("pk").values())
        edit_url = reverse("manager_availability_edit", args=[company_wish.pk])
        for method in [self.client.get, self.client.post]:
            response = method(edit_url, self.post_data())

            self.assertEqual(response.status_code, 404)
            self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_company_submission_does_not_make_the_person_eligible_for_generated_shifts(self):
        self.make_company_availability(start_time=time(9), end_time=time(17))
        Requirement.objects.create(
            store=self.store, work_date=self.work_date, start_time=time(9), end_time=time(17), required_staff_count=1,
        )
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        self.client.force_login(self.manager)

        response = self.client.post(reverse("generate_shift"), {"work_date": self.work_date.isoformat()})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Shift.objects.filter(user=self.staff, store=self.store).exists())
        self.assertFalse(Shift.objects.filter(user=self.company_user).exists())
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)

    def test_company_scope_gets_do_not_write_and_anonymous_users_cannot_enter(self):
        for url in [self.calendar_url(), self.create_url()]:
            with self.subTest(url=url):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE")
                )], [])
        self.client.logout()
        response = self.client.get(self.calendar_url())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))

    def test_company_submission_and_deletion_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.company_user)
        csrf_client.get(self.create_url())
        before = list(Availability.objects.order_by("pk").values())

        response = csrf_client.post(self.create_url(), self.post_data())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(self.create_url(), self.post_data(), HTTP_X_CSRFTOKEN=token)
        self.assert_calendar_redirect(response)
        availability = Availability.objects.get(user=self.company_user)
        delete_url = reverse("availability_delete", args=[availability.pk])
        self.assertEqual(csrf_client.post(delete_url).status_code, 403)
        self.assertTrue(Availability.objects.filter(pk=availability.pk).exists())
        self.assert_calendar_redirect(csrf_client.post(delete_url, HTTP_X_CSRFTOKEN=token))
        self.assertFalse(Availability.objects.filter(pk=availability.pk).exists())
