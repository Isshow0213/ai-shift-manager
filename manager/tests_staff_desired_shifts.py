from datetime import date, time
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from manager.forms import StaffDesiredShiftsForm
from shifts.models import Availability, Shift


class DesiredShiftsFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current_form = {**attributes, "inputs": []}
            self.forms.append(self.current_form)
        elif tag == "input" and self.current_form is not None:
            self.current_form["inputs"].append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


class StaffDesiredShiftsUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="勤務回数テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(
            username="desired_manager", last_name="柴田", first_name="一翔",
            rank="A", desired_shifts_per_week=2,
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager", rank="C",
            desired_shifts_per_week=1,
        )
        cls.staff = user_model.objects.create_user(
            username="desired_staff", last_name="田中", first_name="花子",
            rank="C", desired_shifts_per_week=6,
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="B",
            desired_shifts_per_week=3,
        )
        cls.other_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="manager", rank="A",
            desired_shifts_per_week=5, is_active=False,
        )
        inactive_staff = user_model.objects.create_user(
            username="desired_inactive", last_name="鈴木", first_name="太郎",
            desired_shifts_per_week=4,
        )
        cls.inactive_membership = StoreMembership.objects.create(
            user=inactive_staff, store=cls.store, role="staff", rank="C",
            desired_shifts_per_week=2, is_active=False,
        )
        cls.work_date = date(2030, 1, 10)
        Availability.objects.create(
            user=cls.staff, membership=cls.membership, work_date=cls.work_date,
            start_time=time(9), end_time=time(17),
        )
        Shift.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store,
            work_date=cls.work_date, start_time=time(10), end_time=time(16),
            is_generated=True, note="保存済みの確定シフト", display_color="green",
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.list_url = reverse("manager_staff_list")
        self.update_url = self.desired_url(self.membership)

    def desired_url(self, membership):
        return reverse("manager_staff_desired_shifts_update", args=[membership.pk])

    def membership_snapshot(self):
        return list(StoreMembership.objects.order_by("pk").values())

    def test_list_has_separate_forms_with_current_values_valid_bounds_and_csrf(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        memberships = list(response.context["memberships"])
        self.assertCountEqual([membership.pk for membership in memberships], [
            self.manager_membership.pk, self.membership.pk, self.inactive_membership.pk,
        ])
        parser = DesiredShiftsFormParser()
        parser.feed(response.content.decode())
        forms = {form.get("action"): form for form in parser.forms}
        for membership in memberships:
            with self.subTest(membership=membership.pk):
                desired_form = membership.desired_shifts_form
                self.assertIsInstance(desired_form, StaffDesiredShiftsForm)
                self.assertFalse(desired_form.is_bound)
                self.assertEqual(
                    desired_form["desired_shifts_per_week"].value(), membership.desired_shifts_per_week
                )
                expected_id = f"membership-{membership.pk}-desired-desired_shifts_per_week"
                self.assertEqual(desired_form["desired_shifts_per_week"].id_for_label, expected_id)
                self.assertFalse(membership.rank_form.is_bound)
                self.assertEqual(membership.rank_form["rank"].value(), membership.rank)
                form = forms[self.desired_url(membership)]
                self.assertEqual(form["method"].lower(), "post")
                self.assertTrue(any(
                    field.get("name") == "csrfmiddlewaretoken" and field.get("value")
                    for field in form["inputs"]
                ))
                count_input = next(field for field in form["inputs"]
                                   if field.get("name") == "desired_shifts_per_week")
                self.assertEqual(count_input.get("value"), str(membership.desired_shifts_per_week))
                self.assertEqual(count_input.get("id"), expected_id)
                self.assertEqual(count_input.get("type"), "number")
                self.assertEqual(count_input.get("min"), "0")
                self.assertEqual(count_input.get("max"), "7")
                self.assertIn("required", count_input)
        self.assertNotIn(self.desired_url(self.other_membership), forms)

    def test_zero_interior_and_seven_are_saved_and_redirect_to_list(self):
        for count in [0, 4, 7]:
            with self.subTest(count=count):
                response = self.client.post(self.update_url, {"desired_shifts_per_week": count})

                self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
                self.membership.refresh_from_db()
                self.assertEqual(self.membership.desired_shifts_per_week, count)
                list_response = self.client.get(self.list_url)
                rows = {membership.pk: membership for membership in list_response.context["memberships"]}
                self.assertEqual(rows[self.membership.pk].desired_shifts_form["desired_shifts_per_week"].value(), count)

    def test_only_target_membership_count_changes_and_tampered_fields_are_ignored(self):
        memberships_before = self.membership_snapshot()
        users_before = list(get_user_model().objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())
        availabilities_before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(self.update_url, {
            "desired_shifts_per_week": 7, "rank": "A", "store": self.other_store.pk,
            "user": self.manager.pk, "membership": self.other_membership.pk,
            "role": "manager", "is_active": "", "user_desired_shifts_per_week": 1,
        })

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        expected = [
            {**row, "desired_shifts_per_week": 7} if row["id"] == self.membership.pk else row
            for row in memberships_before
        ]
        self.assertEqual(self.membership_snapshot(), expected)
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), users_before)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), availabilities_before)

    def test_manager_can_update_own_store_membership_count(self):
        response = self.client.post(self.desired_url(self.manager_membership), {"desired_shifts_per_week": 7})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.manager_membership.refresh_from_db()
        self.assertEqual(self.manager_membership.desired_shifts_per_week, 7)
        self.assertEqual(self.manager_membership.role, "manager")

    def test_inactive_membership_count_can_change_without_reactivation(self):
        response = self.client.post(self.desired_url(self.inactive_membership), {"desired_shifts_per_week": 0})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.inactive_membership.refresh_from_db()
        self.assertEqual(self.inactive_membership.desired_shifts_per_week, 0)
        self.assertFalse(self.inactive_membership.is_active)

    def test_invalid_count_only_binds_target_count_form_and_preserves_entered_value(self):
        before = self.membership_snapshot()
        invalid_values = ["-1", "8", "3.5", "not-a-number", "", "9" * 500, None]
        for value in invalid_values:
            with self.subTest(value=value):
                data = {} if value is None else {"desired_shifts_per_week": value}
                response = self.client.post(self.update_url, {**data, "rank": "A"})

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "manager/staff_list.html")
                rows = {membership.pk: membership for membership in response.context["memberships"]}
                target_form = rows[self.membership.pk].desired_shifts_form
                self.assertTrue(target_form.is_bound)
                self.assertIn("desired_shifts_per_week", target_form.errors)
                self.assertEqual(target_form["desired_shifts_per_week"].value(), value)
                for pk, membership in rows.items():
                    self.assertFalse(membership.rank_form.is_bound)
                    self.assertFalse(membership.rank_form.errors)
                    if pk != self.membership.pk:
                        self.assertFalse(membership.desired_shifts_form.is_bound)
                        self.assertFalse(membership.desired_shifts_form.errors)
                self.assertEqual(self.membership_snapshot(), before)

    def test_invalid_rank_update_keeps_all_count_forms_unbound_and_unchanged(self):
        before = self.membership_snapshot()

        response = self.client.post(reverse("manager_staff_rank_update", args=[self.membership.pk]), {
            "rank": "invalid", "desired_shifts_per_week": 7,
        })

        self.assertEqual(response.status_code, 200)
        rows = {membership.pk: membership for membership in response.context["memberships"]}
        self.assertTrue(rows[self.membership.pk].rank_form.is_bound)
        self.assertTrue(rows[self.membership.pk].rank_form.errors)
        for membership in rows.values():
            self.assertFalse(membership.desired_shifts_form.is_bound)
            self.assertEqual(
                membership.desired_shifts_form["desired_shifts_per_week"].value(), membership.desired_shifts_per_week
            )
        self.assertEqual(self.membership_snapshot(), before)

    def test_other_store_and_nonexistent_memberships_return_404_without_changes(self):
        before = self.membership_snapshot()
        nonexistent_pk = StoreMembership.objects.order_by("-pk").first().pk + 1
        for url in [
            self.desired_url(self.other_membership),
            reverse("manager_staff_desired_shifts_update", args=[nonexistent_pk]),
        ]:
            with self.subTest(url=url):
                response = self.client.post(url, {"desired_shifts_per_week": 7})

                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.membership_snapshot(), before)

    def test_multiple_manager_stores_still_use_first_active_manager_membership(self):
        StoreMembership.objects.create(user=self.manager, store=self.other_store, role="manager")
        before = self.membership_snapshot()

        response = self.client.post(self.desired_url(self.other_membership), {"desired_shifts_per_week": 7})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.membership_snapshot(), before)

    def test_staff_cannot_update_count(self):
        before = self.membership_snapshot()
        self.client.force_login(self.staff)

        response = self.client.post(self.update_url, {"desired_shifts_per_week": 7})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.membership_snapshot(), before)

    def test_inactive_manager_cannot_update_count(self):
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        before = self.membership_snapshot()

        response = self.client.post(self.update_url, {"desired_shifts_per_week": 7})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.membership_snapshot(), before)

    def test_anonymous_user_is_redirected_to_login(self):
        before = self.membership_snapshot()
        self.client.logout()

        response = self.client.post(self.update_url, {"desired_shifts_per_week": 7})

        self.assertRedirects(response, f"{reverse('login')}?next={self.update_url}", fetch_redirect_response=False)
        self.assertEqual(self.membership_snapshot(), before)

    def test_get_and_put_are_rejected_without_changes(self):
        before = self.membership_snapshot()
        for method in [self.client.get, self.client.put]:
            with self.subTest(method=method.__name__):
                response = method(self.update_url)

                self.assertEqual(response.status_code, 405)
                self.assertEqual(self.membership_snapshot(), before)

    def test_listing_counts_does_not_write_to_the_database(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        )], [])

    def test_csrf_is_required_and_valid_csrf_allows_count_update(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        csrf_client.get(self.list_url)
        before = self.membership_snapshot()

        response = csrf_client.post(self.update_url, {"desired_shifts_per_week": 7})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.membership_snapshot(), before)
        response = csrf_client.post(
            self.update_url, {"desired_shifts_per_week": 7},
            HTTP_X_CSRFTOKEN=csrf_client.cookies["csrftoken"].value,
        )
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.desired_shifts_per_week, 7)
