from datetime import date, time
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from .models import Availability, Requirement, Shift


class DashboardLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.append(href)


class ManagerDashboardMonthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="月選択テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(username="month_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff_memberships = []
        for index in range(3):
            user = user_model.objects.create_user(
                username=f"month_staff_{index}", last_name=f"従業員{index}"
            )
            cls.staff_memberships.append(StoreMembership.objects.create(
                user=user, store=cls.store, role="staff"
            ))
        disabled = user_model.objects.create_user(username="month_disabled", is_active=False)
        cls.disabled_membership = StoreMembership.objects.create(
            user=disabled, store=cls.store, role="staff"
        )
        inactive = user_model.objects.create_user(username="month_inactive")
        cls.inactive_membership = StoreMembership.objects.create(
            user=inactive, store=cls.store, role="staff", is_active=False
        )
        foreign = user_model.objects.create_user(username="month_foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            user=foreign, store=cls.other_store, role="staff"
        )
        cls.october_availability = cls.make_availability(cls.staff_memberships[0], date(2026, 10, 31))
        cls.november_availabilities = [
            cls.make_availability(cls.staff_memberships[1], date(2026, 11, 1)),
            cls.make_availability(cls.staff_memberships[1], date(2026, 11, 30)),
            cls.make_availability(cls.manager_membership, date(2026, 11, 12)),
            cls.make_availability(cls.inactive_membership, date(2026, 11, 12)),
        ]
        cls.december_availability = cls.make_availability(cls.staff_memberships[2], date(2026, 12, 1))
        cls.make_availability(cls.foreign_membership, date(2026, 11, 12))
        Availability.objects.create(
            user=cls.staff_memberships[0].user, membership=None,
            work_date=date(2026, 11, 12), start_time=time(9), end_time=time(12),
        )
        for month, count in [(10, 1), (11, 2), (12, 3)]:
            for day in range(1, count + 1):
                Requirement.objects.create(
                    store=cls.store, work_date=date(2026, month, day),
                    start_time=time(9), end_time=time(12), required_staff_count=1,
                )
        for store in [cls.other_store, None]:
            Requirement.objects.create(
                store=store, work_date=date(2026, 11, 12),
                start_time=time(9), end_time=time(12), required_staff_count=1,
            )
        for month, days in [(10, [31]), (11, [1, 30]), (12, [1])]:
            for day in days:
                Shift.objects.create(
                    user=cls.staff_memberships[1].user, membership=cls.staff_memberships[1],
                    store=cls.store, work_date=date(2026, month, day),
                    start_time=time(9), end_time=time(12),
                )
        for store in [cls.other_store, None]:
            Shift.objects.create(
                user=cls.foreign_membership.user, membership=cls.foreign_membership,
                store=store, work_date=date(2026, 11, 12), start_time=time(9), end_time=time(12),
            )

    @classmethod
    def make_availability(cls, membership, work_date):
        return Availability.objects.create(
            user=membership.user, membership=membership, work_date=work_date,
            start_time=time(9), end_time=time(12),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.dashboard_url = reverse("manager_dashboard")
        self.availability_url = reverse("manager_availability_list")
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 12)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def test_selected_month_counts_are_scoped_to_the_store_and_distinct_active_staff(self):
        for month, requirements_count, shifts_count in [(10, 1, 1), (11, 2, 2), (12, 3, 1)]:
            with self.subTest(month=month):
                response = self.client.get(self.dashboard_url, {"year": 2026, "month": month})

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["store"], self.store)
                self.assertEqual(response.context["month_start"], date(2026, month, 1))
                self.assertEqual(response.context["total_staff_count"], 4)
                self.assertEqual(response.context["submitted_count"], 1)
                self.assertEqual(response.context["unsubmitted_count"], 3)
                self.assertEqual(response.context["requirements_count"], requirements_count)
                self.assertEqual(response.context["shifts_count"], shifts_count)

    def test_default_dashboard_uses_the_current_japanese_month(self):
        response = self.client.get(self.dashboard_url)

        self.assertEqual((response.context["year"], response.context["month"]), (2026, 10))
        self.assertEqual(response.context["selected_date"], date(2026, 10, 12))
        self.assertEqual(response.context["month_start"], date(2026, 10, 1))
        self.assertEqual(response.context["requirements_count"], 1)

    def test_availability_list_only_shows_the_selected_month_and_that_months_unsubmitted_staff(self):
        for month, submitted_index, expected in [
            (10, 0, [self.october_availability]),
            (11, 1, self.november_availabilities),
            (12, 2, [self.december_availability]),
        ]:
            with self.subTest(month=month):
                response = self.client.get(self.availability_url, {"year": 2026, "month": month})

                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.context["year"], response.context["month"]), (2026, month))
                self.assertCountEqual(response.context["availabilities"], expected)
                self.assertEqual(
                    set(response.context["unsubmitted_memberships"]),
                    {self.disabled_membership, *[
                        membership for index, membership in enumerate(self.staff_memberships)
                        if index != submitted_index
                    ]},
                )

    def test_default_availability_list_uses_current_month_instead_of_all_history(self):
        response = self.client.get(self.availability_url)

        self.assertEqual(list(response.context["availabilities"]), [self.october_availability])
        self.assertEqual(response.context["selected_date"], date(2026, 10, 12))
        self.assertIn(self.staff_memberships[1], response.context["unsubmitted_memberships"])

    def test_shared_calendar_fallbacks_and_selected_dates_apply_to_both_views(self):
        examples = [
            ({"year": "bad", "month": "13", "date": "2026-02-30"}, date(2026, 10, 12)),
            ({"year": "1", "month": "1"}, date(2026, 1, 1)),
            ({"year": "9999", "month": "12"}, date(2026, 12, 1)),
            ({"year": "2026", "month": "11", "date": "2026-10-31"}, date(2026, 11, 1)),
            ({"year": "2026", "month": "11", "date": "2026-11-12"}, date(2026, 11, 12)),
        ]
        for url in [self.dashboard_url, self.availability_url]:
            for query, expected_date in examples:
                with self.subTest(url=url, query=query):
                    response = self.client.get(url, query)

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["selected_date"], expected_date)
                    self.assertEqual(
                        (response.context["year"], response.context["month"]),
                        (expected_date.year, expected_date.month),
                    )

    def test_month_navigation_handles_year_boundaries_and_empty_month_counts(self):
        for url in [self.dashboard_url, self.availability_url]:
            response = self.client.get(url, {"year": 2027, "month": 1})

            self.assertEqual((response.context["prev_year"], response.context["prev_month"]), (2026, 12))
            self.assertEqual((response.context["next_year"], response.context["next_month"]), (2027, 2))
            self.assertEqual(response.context["selected_date"], date(2027, 1, 1))
            if url == self.dashboard_url:
                self.assertEqual(response.context["submitted_count"], 0)
                self.assertEqual(response.context["unsubmitted_count"], 4)
                self.assertEqual(response.context["requirements_count"], 0)
                self.assertEqual(response.context["shifts_count"], 0)
            else:
                self.assertEqual(list(response.context["availabilities"]), [])
                self.assertEqual(len(response.context["unsubmitted_memberships"]), 4)

    def test_dashboard_action_links_keep_the_selected_month(self):
        response = self.client.get(self.dashboard_url, {"year": 2026, "month": 11, "date": "2026-11-12"})
        parser = DashboardLinks()
        parser.feed(response.content.decode())
        for name in [
            "manager_shift_overview", "manager_shift_list", "manager_availability_list",
            "manager_requirement_list",
        ]:
            with self.subTest(name=name):
                matching = [parse_qs(urlsplit(link).query) for link in parser.links
                            if urlsplit(link).path == reverse(name)]
                self.assertTrue(any(
                    query.get("year") == ["2026"] and query.get("month") == ["11"]
                    for query in matching
                ), matching)

    def test_posted_store_query_does_not_change_month_counts_or_list_scope(self):
        query = {"year": 2026, "month": 11, "store": self.other_store.pk}
        dashboard = self.client.get(self.dashboard_url, query)
        availability_list = self.client.get(self.availability_url, query)

        self.assertEqual(dashboard.context["store"], self.store)
        self.assertEqual(dashboard.context["requirements_count"], 2)
        self.assertCountEqual(availability_list.context["availabilities"], self.november_availabilities)

    def test_month_pages_are_get_only_and_do_not_write_to_the_database(self):
        for url in [self.dashboard_url, self.availability_url]:
            with self.subTest(url=url):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url, {"year": 2026, "month": 11})
                    self.assertEqual(response.status_code, 200)
                writes = [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE")
                )]
                self.assertEqual(writes, [])
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_staff_and_anonymous_users_cannot_view_manager_month_pages(self):
        self.client.force_login(self.staff_memberships[0].user)
        response = self.client.get(self.dashboard_url)
        self.assertRedirects(response, reverse("availability_list"), fetch_redirect_response=False)
        response = self.client.get(self.availability_url)
        self.assertEqual(response.status_code, 302)

        self.client.logout()
        for url in [self.dashboard_url, self.availability_url]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
