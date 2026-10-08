from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Company, Store, StoreMembership


class LoginRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "shift-password-2026"
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        User = get_user_model()
        cls.manager = User.objects.create_user(
            username="manager", password=cls.password, role="staff"
        )
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff = User.objects.create_user(
            username="staff", password=cls.password, role="admin", is_staff=True
        )
        StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.unassigned = User.objects.create_user(
            username="unassigned", password=cls.password
        )
        cls.inactive_manager = User.objects.create_user(
            username="inactive-manager", password=cls.password, role="admin"
        )
        StoreMembership.objects.create(
            user=cls.inactive_manager, store=cls.store, role="manager", is_active=False
        )

    def login(self, user, **data):
        self.client.logout()
        return self.client.post(
            reverse("login"),
            {"username": user.username, "password": self.password, **data},
        )

    def test_active_manager_uses_membership_instead_of_legacy_user_role(self):
        response = self.login(self.manager)
        self.assertRedirects(response, reverse("manager_dashboard"))

    def test_staff_with_legacy_admin_role_uses_employee_calendar(self):
        response = self.login(self.staff)
        self.assertRedirects(response, reverse("availability_list"))

    def test_unassigned_and_inactive_manager_use_employee_calendar(self):
        for user in (self.unassigned, self.inactive_manager):
            with self.subTest(user=user.username):
                response = self.login(user)
                self.assertRedirects(response, reverse("availability_list"))

    def test_manager_membership_takes_priority_over_staff_membership(self):
        StoreMembership.objects.create(
            user=self.staff, store=self.other_store, role="manager"
        )
        response = self.login(self.staff)
        self.assertRedirects(response, reverse("manager_dashboard"))

    def test_safe_employee_next_keeps_selected_date(self):
        target = reverse("availability_create") + "?date=2026-07-12"
        response = self.login(self.staff, next=target)
        self.assertRedirects(response, target)

    def test_manager_next_keeps_selected_calendar_day(self):
        target = reverse("manager_shift_list") + "?year=2026&month=7&date=2026-07-12"
        response = self.login(self.manager, next=target)
        self.assertRedirects(response, target)

    def test_staff_cannot_follow_manager_next_including_aliases_and_normalized_paths(self):
        targets = (
            reverse("manager_dashboard"),
            reverse("manager_shift_list") + "?year=2026&month=7",
            "http://testserver" + reverse("manager_staff_list"),
            "/%6danager/dashboard/",
            "/availability/../manager/dashboard/",
            reverse("dashboard"),
            "/generate/",
        )
        for target in targets:
            with self.subTest(next=target):
                response = self.login(self.staff, next=target)
                self.assertRedirects(response, reverse("availability_list"))

    def test_inactive_manager_cannot_follow_manager_next(self):
        response = self.login(self.inactive_manager, next=reverse("manager_dashboard"))
        self.assertRedirects(response, reverse("availability_list"))

    def test_external_and_non_http_next_are_rejected(self):
        targets = (
            "https://outside.example/",
            "//outside.example/availability/",
            "javascript:alert(1)",
        )
        for target in targets:
            with self.subTest(next=target):
                response = self.login(self.manager, next=target)
                self.assertRedirects(response, reverse("manager_dashboard"))

    def test_https_login_rejects_http_next(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.staff.username,
                "password": self.password,
                "next": "http://testserver" + reverse("availability_list"),
            },
            secure=True,
        )
        self.assertRedirects(response, reverse("availability_list"))

    def test_login_page_and_invalid_login_preserve_safe_next(self):
        target = reverse("availability_create") + "?date=2026-07-12"
        response = self.client.get(reverse("login"), {"next": target})
        self.assertEqual(response.context["next"], target)
        self.assertContains(response, 'name="next" value="' + target + '"')
        self.assertContains(response, "ユーザー名")
        self.assertContains(response, "パスワード")

        response = self.client.post(
            reverse("login"),
            {"username": self.staff.username, "password": "incorrect", "next": target},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next" value="' + target + '"')
        self.assertTrue(response.context["form"].non_field_errors())

    def test_authenticated_login_uses_role_and_rejects_login_loop(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("login"), {"next": reverse("manager_dashboard")})
        self.assertRedirects(response, reverse("availability_list"))
        response = self.client.get(reverse("login"), {"next": reverse("login")})
        self.assertRedirects(response, reverse("availability_list"))

    def test_index_routes_anonymous_to_login(self):
        response = self.client.get(reverse("index"))
        self.assertRedirects(response, reverse("login"))

    def test_index_uses_same_membership_rules_as_login(self):
        for user, destination in (
            (self.manager, "manager_dashboard"),
            (self.staff, "availability_list"),
            (self.unassigned, "availability_list"),
            (self.inactive_manager, "availability_list"),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("index"))
                self.assertRedirects(response, reverse(destination))
