from datetime import date, time
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift


class PermissionFormParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {**attrs, "inputs": []}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class StaffPermissionManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="従業員権限設定テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        users = get_user_model()
        cls.manager = users.objects.create_user(username="staff-permission-manager")
        cls.manager_membership = StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff = users.objects.create_user(username="staff-permission-staff", last_name="田中", first_name="花子")
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, rank="B", desired_shifts_per_week=3,
        )
        cls.other_membership = StoreMembership.objects.create(user=cls.staff, store=cls.other_store)
        inactive_user = users.objects.create_user(username="staff-permission-inactive")
        cls.inactive_membership = StoreMembership.objects.create(user=inactive_user, store=cls.store, is_active=False)
        cls.work_date = date(2030, 1, 10)
        Shift.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store, work_date=cls.work_date,
            start_time=time(9), end_time=time(17), is_generated=True, note="保存したシフト",
        )
        Availability.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store, work_date=cls.work_date,
            start_time=time(9), end_time=time(17),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.list_url = reverse("manager_staff_list")
        self.update_url = self.permission_url(self.membership)

    def permission_url(self, membership):
        return reverse("manager_staff_permissions_update", args=[membership.pk])

    def snapshots(self):
        return {
            "memberships": list(StoreMembership.objects.order_by("pk").values()),
            "users": list(get_user_model().objects.order_by("pk").values()),
            "shifts": list(Shift.objects.order_by("pk").values()),
            "availability": list(Availability.objects.order_by("pk").values()),
        }

    def test_staff_list_contains_individual_three_checkbox_forms_and_csrf(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        forms = {form.get("action"): form for form in PermissionFormParser(response.content.decode()).forms}
        for membership in (self.membership, self.inactive_membership):
            with self.subTest(membership=membership.pk):
                form = forms[self.permission_url(membership)]
                self.assertEqual(form["method"].lower(), "post")
                inputs = {field.get("name"): field for field in form["inputs"]}
                self.assertTrue(inputs["csrfmiddlewaretoken"].get("value"))
                for name in ("can_view_shifts", "can_submit_availability", "show_in_shift_overview"):
                    self.assertEqual(inputs[name].get("type"), "checkbox")
                    self.assertIn("checked", inputs[name])
        self.assertNotIn(self.permission_url(self.other_membership), forms)
        self.assertNotIn(self.permission_url(self.manager_membership), forms)
        self.assertNotContains(response, "会社メンバーの権限")

    def test_each_permission_combination_can_be_saved_including_all_disabled(self):
        fields = ("can_view_shifts", "can_submit_availability", "show_in_shift_overview")
        combinations = ((True, True, True), (False, True, False), (True, False, False),
                        (False, False, True), (False, False, False))
        for values in combinations:
            with self.subTest(values=values):
                response = self.client.post(self.update_url, {
                    field: "on" for field, enabled in zip(fields, values) if enabled
                })
                self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
                self.membership.refresh_from_db()
                self.assertEqual(tuple(getattr(self.membership, field) for field in fields), values)
                page = self.client.get(self.list_url)
                forms = {form.get("action"): form for form in PermissionFormParser(page.content.decode()).forms}
                inputs = {field.get("name"): field for field in forms[self.update_url]["inputs"]}
                self.assertEqual(tuple("checked" in inputs[field] for field in fields), values)

    def test_only_target_membership_permissions_change_and_tampered_fields_are_ignored(self):
        before = self.snapshots()

        response = self.client.post(self.update_url, {
            "can_view_shifts": "on", "show_in_shift_overview": "on",
            "user": self.manager.pk, "store": self.other_store.pk,
            "membership": self.other_membership.pk, "role": "manager",
            "rank": "A", "desired_shifts_per_week": 7, "is_active": "",
        })

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        expected = {**before, "memberships": [
            {**row, "can_view_shifts": True, "can_submit_availability": False, "show_in_shift_overview": True}
            if row["id"] == self.membership.pk else row for row in before["memberships"]
        ]}
        self.assertEqual(self.snapshots(), expected)

    def test_inactive_staff_can_have_permissions_changed_without_reactivation(self):
        response = self.client.post(self.permission_url(self.inactive_membership), {})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.inactive_membership.refresh_from_db()
        self.assertFalse(self.inactive_membership.is_active)
        self.assertFalse(self.inactive_membership.can_view_shifts)
        self.assertFalse(self.inactive_membership.can_submit_availability)
        self.assertFalse(self.inactive_membership.show_in_shift_overview)

    def test_manager_membership_permissions_cannot_be_changed(self):
        before = self.snapshots()

        response = self.client.post(self.permission_url(self.manager_membership), {})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshots(), before)

    def test_other_store_or_nonexistent_membership_returns_404_without_any_changes(self):
        before = self.snapshots()
        urls = (self.permission_url(self.other_membership),
                reverse("manager_staff_permissions_update", args=[999999]))
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 404)
                self.assertEqual(self.snapshots(), before)

    def test_staff_cannot_change_own_or_another_staff_permissions(self):
        self.client.force_login(self.staff)
        before = self.snapshots()
        for membership in (self.membership, self.inactive_membership):
            with self.subTest(membership=membership.pk):
                response = self.client.post(self.permission_url(membership), {})
                self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
                self.assertEqual(self.snapshots(), before)

    def test_inactive_manager_membership_does_not_authorize_permission_updates(self):
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        before = self.snapshots()

        response = self.client.post(self.update_url, {})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.snapshots(), before)

    def test_anonymous_permission_update_redirects_to_login(self):
        self.client.logout()
        before = self.snapshots()

        response = self.client.post(self.update_url, {})

        self.assertRedirects(response, f"{reverse('login')}?next={self.update_url}", fetch_redirect_response=False)
        self.assertEqual(self.snapshots(), before)

    def test_get_and_put_are_rejected_without_changes(self):
        before = self.snapshots()
        for method in (self.client.get, self.client.put):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(self.update_url).status_code, 405)
                self.assertEqual(self.snapshots(), before)

    def test_csrf_is_required_and_valid_token_saves_permissions(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        client.get(self.list_url)
        before = self.snapshots()

        self.assertEqual(client.post(self.update_url, {}).status_code, 403)
        self.assertEqual(self.snapshots(), before)
        response = client.post(self.update_url, {}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.can_view_shifts)
        self.assertFalse(self.membership.can_submit_availability)
        self.assertFalse(self.membership.show_in_shift_overview)
