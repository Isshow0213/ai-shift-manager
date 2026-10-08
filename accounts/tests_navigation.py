from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from .context_processors import app_access
from .models import Company, Store, StoreMembership


class ScreenSwitchParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.targets = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "screen-switch" in attributes.get("class", "").split():
            self.targets.append(attributes.get("href"))


class ScreenNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        User = get_user_model()
        cls.manager = User.objects.create_user(username="navigation-manager", role="staff")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff = User.objects.create_user(
            username="navigation-staff", role="admin", is_staff=True
        )
        StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.inactive_manager = User.objects.create_user(username="navigation-inactive")
        StoreMembership.objects.create(
            user=cls.inactive_manager, store=cls.store, role="manager", is_active=False
        )
        cls.unassigned = User.objects.create_user(username="navigation-unassigned")
        cls.calendar_query = {"year": 2026, "month": 7, "date": "2026-07-12"}

    def switch_target(self, response):
        parser = ScreenSwitchParser(response.content.decode())
        self.assertEqual(len(parser.targets), 1)
        return parser.targets[0]

    def test_context_processor_uses_active_manager_membership(self):
        for user, expected in (
            (self.manager, True),
            (self.staff, False),
            (self.inactive_manager, False),
            (self.unassigned, False),
        ):
            with self.subTest(user=user.username):
                self.assertEqual(
                    app_access(SimpleNamespace(user=user)), {"can_manage_store": expected}
                )

    def test_context_processor_does_not_query_for_anonymous_requests(self):
        with self.assertNumQueries(0):
            self.assertEqual(
                app_access(SimpleNamespace(user=AnonymousUser())),
                {"can_manage_store": False},
            )
            self.assertEqual(app_access(SimpleNamespace()), {"can_manage_store": False})

    def test_active_manager_membership_in_another_store_enables_switch(self):
        StoreMembership.objects.create(user=self.staff, store=self.other_store, role="manager")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("availability_list"), self.calendar_query)
        self.assertContains(response, "管理画面へ")
        self.assertTrue(response.context["can_manage_store"])

    def test_manager_can_switch_from_shift_calendar_to_employee_view_and_back(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("manager_shift_list"), self.calendar_query)
        self.assertContains(response, "従業員画面へ")
        employee_target = self.switch_target(response)
        parsed = urlsplit(employee_target)
        self.assertEqual(parsed.path, reverse("availability_list"))
        self.assertEqual(
            parse_qs(parsed.query),
            {"year": ["2026"], "month": ["7"], "date": ["2026-07-12"]},
        )

        response = self.client.get(employee_target)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "管理画面へ")
        self.assertNotContains(response, "従業員画面へ")
        manager_target = self.switch_target(response)
        self.assertEqual(urlsplit(manager_target).path, reverse("manager_dashboard"))
        response = self.client.get(manager_target)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "従業員画面へ")
        self.assertContains(response, reverse("manager_shift_list"))

    def test_all_manager_pages_offer_employee_view(self):
        self.client.force_login(self.manager)
        for view_name in (
            "manager_dashboard",
            "manager_shift_list",
            "manager_availability_list",
            "manager_requirement_list",
            "manager_staff_list",
        ):
            with self.subTest(view=view_name):
                response = self.client.get(reverse(view_name), self.calendar_query)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "従業員画面へ")
                self.assertEqual(
                    urlsplit(self.switch_target(response)).path, reverse("availability_list")
                )

    def test_manager_switch_is_available_on_every_employee_page(self):
        self.client.force_login(self.manager)
        for view_name in ("availability_list", "availability_create", "shift_list"):
            with self.subTest(view=view_name):
                response = self.client.get(reverse(view_name), self.calendar_query)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "管理画面へ")
                self.assertEqual(
                    urlsplit(self.switch_target(response)).path, reverse("manager_dashboard")
                )

    def test_staff_pages_hide_manager_switch_even_with_legacy_admin_role(self):
        self.client.force_login(self.staff)
        for view_name in ("availability_list", "availability_create", "shift_list"):
            with self.subTest(view=view_name):
                response = self.client.get(reverse(view_name), self.calendar_query)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "管理画面へ")
                self.assertNotContains(response, "従業員画面へ")
                self.assertFalse(response.context["can_manage_store"])

    def test_inactive_manager_and_unassigned_users_do_not_get_manager_switch(self):
        for user in (self.inactive_manager, self.unassigned):
            self.client.force_login(user)
            for view_name in ("availability_list", "shift_list"):
                with self.subTest(user=user.username, view=view_name):
                    response = self.client.get(reverse(view_name), self.calendar_query)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, "管理画面へ")
                    self.assertFalse(response.context["can_manage_store"])
