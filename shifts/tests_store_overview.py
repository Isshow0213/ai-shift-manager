from datetime import date, time
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import urlencode, urljoin, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import Company, Store, StoreMembership
from .models import Availability, Shift


class StoreOverviewLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.append(href)


class StoreShiftOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.staff = user_model.objects.create_user(
            username="store_overview_staff", last_name="田中", first_name="花子"
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="B"
        )
        cls.second_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="staff", rank="A"
        )
        cls.manager = user_model.objects.create_user(
            username="store_overview_manager", last_name="柴田", first_name="一翔"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.coworker = user_model.objects.create_user(
            username="store_overview_coworker", last_name="鈴木", first_name="太郎",
            email="coworker-private@example.com",
        )
        cls.coworker_membership = StoreMembership.objects.create(
            user=cls.coworker, store=cls.store, role="staff", rank="A"
        )
        cls.inactive_staff = user_model.objects.create_user(
            username="store_overview_inactive", last_name="山田", first_name="次郎"
        )
        cls.inactive_membership = StoreMembership.objects.create(
            user=cls.inactive_staff, store=cls.store, role="staff", is_active=False
        )
        cls.disabled_staff = user_model.objects.create_user(
            username="store_overview_disabled", last_name="中村", first_name="三郎", is_active=False
        )
        cls.disabled_membership = StoreMembership.objects.create(
            user=cls.disabled_staff, store=cls.store, role="staff"
        )
        cls.legacy_user = user_model.objects.create_user(
            username="store_overview_legacy", last_name="高橋", first_name="恵子"
        )
        cls.foreign_user = user_model.objects.create_user(
            username="store_overview_foreign", last_name="佐藤", first_name="美咲",
            email="foreign-private@example.com",
        )
        cls.foreign_membership = StoreMembership.objects.create(
            user=cls.foreign_user, store=cls.other_store, role="staff"
        )
        cls.work_date = date(2028, 2, 3)

    def setUp(self):
        self.client.force_login(self.staff)
        self.url = reverse("store_shift_overview")
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 9)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def get_overview(self, **query):
        return self.client.get(self.url, {"year": 2028, "month": 2, **query})

    def make_shift(self, *, membership=None, **overrides):
        membership = membership or self.membership
        data = {
            "user": membership.user, "membership": membership, "store": membership.store,
            "work_date": self.work_date, "start_time": time(9), "end_time": time(10),
            "is_generated": False,
        }
        data.update(overrides)
        return Shift.objects.create(**data)

    def links(self, response):
        parser = StoreOverviewLinks()
        parser.feed(response.content.decode())
        return {urljoin(self.url, link) for link in parser.links}

    def test_empty_month_has_all_days_and_active_members_including_manager(self):
        response = self.get_overview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.store)
        self.assertEqual(
            [day["date"] for day in response.context["month_days"]],
            [date(2028, 2, day) for day in range(1, 30)],
        )
        self.assertEqual(
            {row["user"].pk for row in response.context["rows"]},
            {self.staff.pk, self.manager.pk, self.coworker.pk, self.disabled_staff.pk},
        )
        self.assertEqual(response.context["total_shifts"], 0)
        self.assertEqual(response.context["day_staff_counts"], [0] * 29)
        for row in response.context["rows"]:
            self.assertEqual(len(row["cells"]), 29)
            self.assertTrue(all(cell["shifts"] == [] for cell in row["cells"]))

    def test_store_scope_legacy_people_totals_and_get_are_read_only(self):
        own_first = self.make_shift()
        own_second = self.make_shift(start_time=time(14), end_time=time(15))
        manager_shift = self.make_shift(membership=self.manager_membership, start_time=time(10), end_time=time(11))
        coworker_shift = self.make_shift(
            membership=self.coworker_membership, work_date=date(2028, 2, 4), end_time=time(12)
        )
        inactive_shift = self.make_shift(
            membership=self.inactive_membership, start_time=time(8), end_time=time(10)
        )
        legacy_shift = Shift.objects.create(
            user=self.legacy_user, store=self.store, membership=None,
            work_date=date(2028, 2, 4), start_time=time(10), end_time=time(10, 30),
        )
        # 古い Shift の所属FKよりも Shift.user と Shift.store を優先する。
        mismatched_shift = self.make_shift(
            membership=self.foreign_membership, store=self.store,
            work_date=date(2028, 2, 4), start_time=time(11), end_time=time(12),
        )
        expected = [own_first, own_second, manager_shift, coworker_shift, inactive_shift, legacy_shift, mismatched_shift]
        self.make_shift(membership=self.second_membership, note="他店舗のシフト")
        self.make_shift(store=None, note="店舗未設定のシフト")
        self.make_shift(work_date=date(2028, 3, 1), note="翌月のシフト")
        before_shifts = list(Shift.objects.order_by("pk").values())
        before_memberships = list(StoreMembership.objects.order_by("pk").values())

        response = self.get_overview(store=self.other_store.pk)

        self.assertEqual(response.context["store"], self.store)
        rows = {row["user"].pk: row for row in response.context["rows"]}
        visible = [shift for row in rows.values() for cell in row["cells"] for shift in cell["shifts"]]
        self.assertCountEqual(visible, expected)
        self.assertEqual(rows[self.staff.pk]["working_days"], 1)
        self.assertEqual(rows[self.staff.pk]["shift_count"], 2)
        self.assertEqual(rows[self.staff.pk]["hours_display"], "2:00")
        self.assertEqual(rows[self.inactive_staff.pk]["membership"], self.inactive_membership)
        self.assertIsNone(rows[self.legacy_user.pk]["membership"])
        self.assertIsNone(rows[self.foreign_user.pk]["membership"])
        self.assertEqual(response.context["total_shifts"], 7)
        self.assertEqual(response.context["scheduled_staff_count"], 6)
        self.assertEqual(response.context["total_working_days"], 6)
        self.assertEqual(response.context["total_hours_display"], "9:30")
        self.assertEqual(response.context["day_staff_counts"][2:4], [3, 3])
        self.assertNotContains(response, "他店舗のシフト")
        self.assertNotContains(response, "店舗未設定のシフト")
        self.assertNotContains(response, "翌月のシフト")
        for day in response.context["month_days"]:
            self.assertIsNone(day["url"])
        for row in rows.values():
            for cell in row["cells"]:
                self.assertIsNone(cell["url"])
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before_shifts)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), before_memberships)

    def test_month_selection_changes_shifts_day_count_and_totals(self):
        february = self.make_shift(work_date=date(2028, 2, 29), end_time=time(11))
        march = self.make_shift(work_date=date(2028, 3, 1), end_time=time(12))
        for month, days_count, expected_shift, hours in [
            (2, 29, february, "2:00"), (3, 31, march, "3:00"),
        ]:
            with self.subTest(month=month):
                response = self.get_overview(month=month)
                self.assertEqual(len(response.context["month_days"]), days_count)
                self.assertEqual(response.context["total_shifts"], 1)
                self.assertEqual(response.context["total_hours_display"], hours)
                shifts = [shift for row in response.context["rows"] for cell in row["cells"] for shift in cell["shifts"]]
                self.assertEqual(shifts, [expected_shift])
                self.assertIn(f"{self.url}?year=2028&month={month - 1}", self.links(response))
                self.assertIn(f"{self.url}?year=2028&month={month + 1}", self.links(response))
        response = self.get_overview(year="invalid", month="13")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context["year"], response.context["month"]), (2026, 10))

    def test_read_only_page_escapes_notes_and_excludes_management_and_private_fields(self):
        note = '<script>alert("store-overview-note")</script>ヘルプ先メモ'
        shift = self.make_shift(
            membership=self.coworker_membership, start_time=time(9, 5), end_time=time(12, 30),
            note=note, display_color="green",
        )
        Availability.objects.create(
            user=self.coworker, membership=self.coworker_membership, work_date=self.work_date,
            start_time=time(3, 17), end_time=time(4, 23),
        )

        response = self.get_overview()

        self.assertContains(response, "鈴木 太郎")
        self.assertContains(response, "09:05")
        self.assertContains(response, "12:30")
        self.assertContains(response, escape(note))
        self.assertNotContains(response, note)
        self.assertContains(response, "shift-color-green")
        for private_value in ["coworker-private@example.com", "foreign-private@example.com", "03:17", "04:23", "ランク"]:
            self.assertNotContains(response, private_value)
        self.assertNotContains(response, reverse("manager_shift_edit", args=[shift.pk]))
        self.assertNotContains(response, reverse("manager_shift_delete", args=[shift.pk]))
        self.assertNotContains(response, reverse("generate_shift"))
        self.assertTrue(all(not urlsplit(link).path.startswith("/manager/") for link in self.links(response)))

    def test_post_and_users_without_active_membership_are_rejected(self):
        shift = self.make_shift(note="維持するメモ")
        before = list(Shift.objects.values())
        response = self.client.post(self.url, {"shift": shift.pk, "note": "変更不可"})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(list(Shift.objects.values()), before)
        for user in [self.inactive_staff, self.legacy_user]:
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                response = self.get_overview()
                self.assertRedirects(response, reverse("availability_list"), fetch_redirect_response=False)
        self.client.logout()
        for method in ["get", "post"]:
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                login_url = f"{reverse('login')}?{urlencode({'next': self.url}, safe='/')}"
                self.assertRedirects(response, login_url, fetch_redirect_response=False)

    def test_employee_links_reach_store_overview_and_personal_view_stays_personal(self):
        own = self.make_shift(note="自分のメモ")
        self.make_shift(membership=self.coworker_membership, note="同僚のメモ")
        for name in ["shift_list", "availability_list"]:
            with self.subTest(name=name):
                response = self.client.get(reverse(name), {"year": 2028, "month": 2, "date": self.work_date.isoformat()})
                self.assertTrue(any(urlsplit(link).path == self.url for link in self.links(response)))
                if name == "shift_list":
                    self.assertEqual(list(response.context["shifts"]), [own])
                    self.assertNotContains(response, "同僚のメモ")
        response = self.get_overview()
        self.assertContains(response, "自分のメモ")
        self.assertContains(response, "同僚のメモ")
