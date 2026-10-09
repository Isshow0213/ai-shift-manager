from datetime import date, time
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import urljoin

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Shift


class OverviewLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class ShiftOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = User.objects.create_user(
            username="overview_manager", last_name="柴田", first_name="一翔"
        )
        cls.manager_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.manager, role="manager", rank="A"
        )
        cls.staff = User.objects.create_user(
            username="overview_staff", last_name="田中", first_name="花子"
        )
        cls.membership = StoreMembership.objects.create(
            store=cls.store, user=cls.staff, role="staff", rank="B"
        )
        cls.disabled_user = User.objects.create_user(
            username="overview_disabled", last_name="山田", first_name="次郎", is_active=False
        )
        cls.disabled_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.disabled_user, role="staff", is_active=True
        )
        cls.inactive_staff = User.objects.create_user(
            username="overview_inactive", last_name="鈴木", first_name="太郎"
        )
        cls.inactive_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.inactive_staff, role="staff", is_active=False
        )
        cls.legacy_user = User.objects.create_user(
            username="overview_legacy", last_name="高橋", first_name="恵子"
        )
        cls.foreign_user = User.objects.create_user(
            username="overview_foreign", last_name="佐藤", first_name="美咲"
        )
        cls.foreign_membership = StoreMembership.objects.create(
            store=cls.other_store, user=cls.foreign_user, role="staff", rank="A"
        )
        cls.work_date = date(2028, 2, 3)

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("manager_shift_overview")
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 9)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def get_overview(self, **query):
        return self.client.get(self.url, {"year": "2028", "month": "2", **query})

    def make_shift(
        self, *, user=None, membership=None, store=None, work_date=None,
        start_time=time(9), end_time=time(10),
    ):
        return Shift.objects.create(
            user=user or self.staff,
            membership=membership,
            store=store or self.store,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=end_time,
        )

    def calendar_url(self, work_date):
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def rows_by_user(self, response):
        return {row["user"].pk: row for row in response.context["rows"]}

    def populate_month(self):
        late = self.make_shift(membership=self.membership, start_time=time(14), end_time=time(17, 15))
        long = self.make_shift(membership=self.membership, start_time=time(9), end_time=time(12, 30))
        short_first = self.make_shift(membership=self.membership)
        short_second = self.make_shift(membership=self.membership)
        self.make_shift(
            membership=self.membership, work_date=date(2028, 2, 4),
            start_time=time(8, 15), end_time=time(10),
        )
        self.make_shift(user=self.inactive_staff, membership=self.inactive_membership, start_time=time(10), end_time=time(11))
        self.make_shift(user=self.legacy_user, start_time=time(9, 30), end_time=time(10, 45))
        # 人物はShift.userを基準にし、別の人物の所属FKが付いていても混同しない。
        self.make_shift(
            user=self.legacy_user, membership=self.membership,
            work_date=date(2028, 2, 4), start_time=time(14), end_time=time(15),
        )
        # 古い不整合データでも店舗の判定はShift.storeによる。
        self.make_shift(
            user=self.foreign_user, membership=self.foreign_membership,
            work_date=date(2028, 2, 4), start_time=time(11), end_time=time(12),
        )
        self.make_shift(user=self.staff, store=self.other_store, start_time=time(0), end_time=time(23))
        self.make_shift(membership=self.membership, work_date=date(2028, 1, 31), end_time=time(18))
        self.make_shift(membership=self.membership, work_date=date(2028, 3, 1), end_time=time(18))
        return [short_first, short_second, long, late]

    def test_empty_month_has_every_day_and_all_active_store_memberships(self):
        response = self.get_overview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [day["date"] for day in response.context["month_days"]],
            [date(2028, 2, day) for day in range(1, 30)],
        )
        rows = self.rows_by_user(response)
        self.assertEqual(set(rows), {self.manager.pk, self.staff.pk, self.disabled_user.pk})
        self.assertEqual(rows[self.disabled_user.pk]["membership"], self.disabled_membership)
        for row in rows.values():
            self.assertEqual(len(row["cells"]), 29)
            self.assertTrue(all(cell["shifts"] == [] for cell in row["cells"]))
            self.assertEqual(row["working_days"], 0)
            self.assertEqual(row["shift_count"], 0)
            self.assertEqual(row["hours_display"], "0:00")
        self.assertEqual(response.context["total_shifts"], 0)
        self.assertEqual(response.context["scheduled_staff_count"], 0)

    def test_cells_sort_shifts_by_start_end_and_id(self):
        expected = self.populate_month()

        response = self.get_overview()

        staff_row = self.rows_by_user(response)[self.staff.pk]
        cell = next(cell for cell in staff_row["cells"] if cell["date"] == self.work_date)
        self.assertEqual(cell["shifts"], expected)
        self.assertEqual(cell["url"], self.calendar_url(self.work_date))

    def test_empty_month_headers_have_japanese_weekdays_weekend_flags_and_date_links(self):
        response = self.get_overview()

        days = {day["date"]: day for day in response.context["month_days"]}
        self.assertEqual(days[date(2028, 2, 1)]["weekday"], "火")
        saturday = days[date(2028, 2, 5)]
        sunday = days[date(2028, 2, 6)]
        self.assertEqual(saturday["weekday"], "土")
        self.assertTrue(saturday["is_weekend"])
        self.assertTrue(saturday["is_saturday"])
        self.assertFalse(saturday["is_sunday"])
        self.assertEqual(sunday["weekday"], "日")
        self.assertTrue(sunday["is_weekend"])
        self.assertFalse(sunday["is_saturday"])
        self.assertTrue(sunday["is_sunday"])
        parser = OverviewLinkParser()
        parser.feed(response.content.decode())
        links = {urljoin(self.url, link) for link in parser.links}
        for work_date, day in days.items():
            self.assertEqual(day["url"], self.calendar_url(work_date))
            self.assertIn(self.calendar_url(work_date), links)

    def test_staff_and_month_totals_count_distinct_days_and_users(self):
        self.populate_month()

        response = self.get_overview()

        rows = self.rows_by_user(response)
        self.assertEqual(rows[self.staff.pk]["working_days"], 2)
        self.assertEqual(rows[self.staff.pk]["shift_count"], 5)
        self.assertEqual(rows[self.staff.pk]["total_minutes"], 630)
        self.assertEqual(rows[self.staff.pk]["hours_display"], "10:30")
        self.assertEqual(rows[self.legacy_user.pk]["working_days"], 2)
        self.assertEqual(rows[self.legacy_user.pk]["hours_display"], "2:15")
        self.assertEqual(response.context["total_shifts"], 9)
        self.assertEqual(response.context["scheduled_staff_count"], 4)
        self.assertEqual(response.context["total_working_days"], 6)
        self.assertEqual(response.context["total_hours_display"], "14:45")
        self.assertEqual(response.context["day_staff_counts"][2:4], [3, 3])

    def test_legacy_inactive_and_foreign_memberships_use_current_store_and_shift_user(self):
        self.populate_month()

        response = self.get_overview()

        rows = self.rows_by_user(response)
        self.assertIn(self.inactive_staff.pk, rows)
        self.assertFalse(rows[self.inactive_staff.pk]["is_active"])
        self.assertEqual(rows[self.inactive_staff.pk]["membership"], self.inactive_membership)
        self.assertIn(self.legacy_user.pk, rows)
        self.assertIsNone(rows[self.legacy_user.pk]["membership"])
        self.assertIn(self.foreign_user.pk, rows)
        self.assertIsNone(rows[self.foreign_user.pk]["membership"])
        for row in rows.values():
            if row["membership"] is not None:
                self.assertEqual(row["membership"].store_id, self.store.pk)
            for cell in row["cells"]:
                for shift in cell["shifts"]:
                    self.assertEqual(shift.user_id, row["user"].pk)
                    self.assertEqual(shift.store_id, self.store.pk)
                    self.assertEqual((shift.work_date.year, shift.work_date.month), (2028, 2))

    def test_other_store_only_users_and_shifts_are_not_displayed(self):
        user = get_user_model().objects.create_user(
            username="overview_only_other_store", last_name="別店", first_name="専属"
        )
        membership = StoreMembership.objects.create(user=user, store=self.other_store, role="staff")
        foreign_shift = self.make_shift(user=user, membership=membership, store=self.other_store)

        response = self.get_overview()

        self.assertNotIn(user.pk, self.rows_by_user(response))
        self.assertNotContains(response, "別店 専属")
        self.assertNotContains(response, reverse("manager_shift_edit", args=[foreign_shift.pk]))
        self.assertEqual(response.context["total_shifts"], 0)

    def test_japanese_names_times_edit_links_and_date_links_are_rendered(self):
        shift = self.make_shift(membership=self.membership, start_time=time(9, 5), end_time=time(12, 30))

        response = self.get_overview()

        self.assertContains(response, "田中 花子")
        self.assertContains(response, "09:05")
        self.assertContains(response, "12:30")
        parser = OverviewLinkParser()
        parser.feed(response.content.decode())
        links = {urljoin(self.url, link) for link in parser.links}
        self.assertIn(reverse("manager_shift_edit", args=[shift.pk]), links)
        self.assertIn(self.calendar_url(self.work_date), links)
        self.assertIn(f"{self.url}?year=2028&month=1", links)
        self.assertIn(f"{self.url}?year=2028&month=3", links)

    def test_month_navigation_rolls_over_year_boundaries(self):
        for query, prev_month, next_month in [
            ({"year": "2028", "month": "1"}, (2027, 12), (2028, 2)),
            ({"year": "2028", "month": "12"}, (2028, 11), (2029, 1)),
        ]:
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual((response.context["prev_year"], response.context["prev_month"]), prev_month)
                self.assertEqual((response.context["next_year"], response.context["next_month"]), next_month)
                self.assertEqual(len(response.context["month_days"]), 31)

    def test_selecting_year_and_month_changes_visible_shifts_days_and_totals(self):
        january = self.make_shift(work_date=date(2028, 1, 31))
        february_first = self.make_shift(work_date=date(2028, 2, 1), end_time=time(11))
        february_last = self.make_shift(work_date=date(2028, 2, 29), end_time=time(12))
        march = self.make_shift(work_date=date(2028, 3, 1), end_time=time(13))
        previous_year = self.make_shift(work_date=date(2027, 2, 1), end_time=time(19))
        other_store = self.make_shift(
            store=self.other_store, work_date=date(2028, 2, 1), end_time=time(20)
        )
        all_shifts = [january, february_first, february_last, march, previous_year, other_store]

        for year, month, days_count, expected_shifts, expected_hours in [
            (2028, 1, 31, [january], "1:00"),
            (2028, 2, 29, [february_first, february_last], "5:00"),
            (2028, 3, 31, [march], "4:00"),
            (2027, 2, 28, [previous_year], "10:00"),
        ]:
            with self.subTest(year=year, month=month):
                response = self.get_overview(year=str(year), month=str(month))

                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.context["year"], response.context["month"]), (year, month))
                self.assertEqual(
                    [day["date"] for day in response.context["month_days"]],
                    [date(year, month, day) for day in range(1, days_count + 1)],
                )
                staff_row = self.rows_by_user(response)[self.staff.pk]
                self.assertEqual(
                    [shift for cell in staff_row["cells"] for shift in cell["shifts"]],
                    expected_shifts,
                )
                self.assertEqual(staff_row["working_days"], len(expected_shifts))
                self.assertEqual(staff_row["shift_count"], len(expected_shifts))
                self.assertEqual(staff_row["hours_display"], expected_hours)
                self.assertEqual(response.context["total_shifts"], len(expected_shifts))
                self.assertEqual(response.context["scheduled_staff_count"], 1)
                self.assertEqual(response.context["total_working_days"], len(expected_shifts))
                self.assertEqual(response.context["total_hours_display"], expected_hours)
                self.assertEqual(sum(response.context["day_staff_counts"]), len(expected_shifts))
                parser = OverviewLinkParser()
                parser.feed(response.content.decode())
                links = {urljoin(self.url, link) for link in parser.links}
                for shift in all_shifts:
                    edit_url = reverse("manager_shift_edit", args=[shift.pk])
                    if shift in expected_shifts:
                        self.assertIn(edit_url, links)
                    else:
                        self.assertNotIn(edit_url, links)

    def test_invalid_date_parameters_fall_back_without_server_errors(self):
        for query in [
            {"year": "invalid", "month": "invalid"},
            {"year": "0", "month": "13", "date": "2026-02-30"},
            {"year": "10000", "month": "0"},
        ]:
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.context["year"], response.context["month"]), (2026, 10))
                self.assertEqual(len(response.context["month_days"]), 31)
                today = next(day for day in response.context["month_days"] if day["is_today"])
                self.assertEqual(today["date"], date(2026, 10, 9))

    def test_get_does_not_change_shifts_users_or_memberships(self):
        self.populate_month()
        before_shifts = list(Shift.objects.order_by("pk").values())
        before_memberships = list(StoreMembership.objects.order_by("pk").values())
        before_users = list(get_user_model().objects.order_by("pk").values())

        response = self.get_overview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before_shifts)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), before_memberships)
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), before_users)

    def test_post_is_rejected_without_modifying_shifts(self):
        self.make_shift(membership=self.membership)
        before = list(Shift.objects.order_by("pk").values())

        response = self.client.post(self.url, {"year": "2028", "month": "2"})

        self.assertEqual(response.status_code, 405)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before)

    def test_staff_and_inactive_managers_cannot_view_overview(self):
        self.client.force_login(self.staff)
        response = self.get_overview()
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        response = self.get_overview()
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )

    def test_multiple_manager_memberships_keep_first_store_as_the_scope(self):
        StoreMembership.objects.create(user=self.manager, store=self.other_store, role="manager")
        self.make_shift(store=self.other_store)

        response = self.get_overview()

        self.assertEqual(response.context["store"], self.store)
        self.assertEqual(response.context["total_shifts"], 0)

    def test_query_count_does_not_grow_per_employee_or_shift(self):
        self.make_shift(membership=self.membership)
        with CaptureQueriesContext(connection) as baseline:
            response = self.get_overview()
            self.assertEqual(response.status_code, 200)
        for index in range(8):
            user = get_user_model().objects.create_user(username=f"overview_extra_{index}")
            membership = StoreMembership.objects.create(user=user, store=self.store, role="staff")
            self.make_shift(user=user, membership=membership)
            self.make_shift(user=user, membership=membership, work_date=date(2028, 2, 4))
        with CaptureQueriesContext(connection) as expanded:
            response = self.get_overview()
            self.assertEqual(response.status_code, 200)

        self.assertLessEqual(len(expanded), len(baseline) + 2)
