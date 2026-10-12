from datetime import date, time
from html.parser import HTMLParser
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from .models import Availability


class CalendarAvailabilityParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.days = {}
        self.current_day = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "calendar-day" in attributes.get("class", "").split():
            date_text = parse_qs(urlsplit(attributes.get("href", "")).query)["date"][0]
            self.current_day = {**attributes, "text": []}
            self.days[date.fromisoformat(date_text)] = self.current_day

    def handle_data(self, data):
        if self.current_day is not None:
            self.current_day["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.current_day = None

    def ranges(self, work_date, *, accessible=False):
        day = self.days[work_date]
        text = day.get("aria-label", "") if accessible else "".join(day["text"])
        return re.findall(r"(\d{2}:\d{2})\s*〜\s*(\d{2}:\d{2})", text)


class CalendarAvailabilityTimesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="希望時刻カレンダーテスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.staff = user_model.objects.create_user(username="calendar_times_staff")
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff"
        )
        cls.other_store_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="staff", is_active=False
        )
        other_staff = user_model.objects.create_user(username="calendar_times_other_staff")
        cls.other_staff_membership = StoreMembership.objects.create(
            user=other_staff, store=cls.store, role="staff"
        )
        foreign_staff = user_model.objects.create_user(username="calendar_times_foreign_staff")
        cls.foreign_membership = StoreMembership.objects.create(
            user=foreign_staff, store=cls.other_store, role="staff"
        )
        cls.manager = user_model.objects.create_user(username="calendar_times_manager")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.work_date = date(2026, 11, 12)
        late = cls.make_availability(start_time=time(13), end_time=time(18))
        same_start_long = cls.make_availability(start_time=time(9, 5), end_time=time(12, 30))
        same_start_short = cls.make_availability(start_time=time(9, 5), end_time=time(11, 45))
        same_start_short_duplicate = cls.make_availability(start_time=time(9, 5), end_time=time(11, 45))
        cls.selected_availabilities = [
            same_start_short, same_start_short_duplicate, same_start_long, late,
        ]
        cls.other_day_availability = cls.make_availability(
            work_date=date(2026, 11, 18), start_time=time(10, 15), end_time=time(14, 45),
        )
        cls.legacy_availability = cls.make_availability(
            membership=None, work_date=date(2026, 11, 19), start_time=time(7), end_time=time(8, 30),
        )
        cls.other_store_availability = cls.make_availability(
            membership=cls.other_store_membership, work_date=date(2026, 11, 20),
            start_time=time(16), end_time=time(21),
        )
        cls.previous_month_availability = cls.make_availability(
            work_date=date(2026, 10, 31), start_time=time(5), end_time=time(6),
        )
        cls.next_month_availability = cls.make_availability(
            work_date=date(2026, 12, 1), start_time=time(22), end_time=time(23),
        )
        cls.make_availability(
            membership=cls.other_staff_membership, start_time=time(0, 1), end_time=time(0, 2),
        )
        cls.make_availability(
            membership=cls.other_staff_membership, work_date=date(2026, 11, 13),
            start_time=time(1, 1), end_time=time(1, 2),
        )
        cls.make_availability(
            membership=cls.foreign_membership, start_time=time(2, 1), end_time=time(2, 2),
        )

    @classmethod
    def make_availability(cls, *, membership="default", work_date=None, start_time=time(9), end_time=time(17)):
        if membership == "default":
            membership = cls.membership
        return Availability.objects.create(
            user=membership.user if membership else cls.staff, membership=membership,
            work_date=work_date or cls.work_date, start_time=start_time, end_time=end_time,
        )

    def setUp(self):
        self.client.force_login(self.staff)
        self.calendar_url = self.calendar_for(self.work_date)
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 13)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def calendar_for(self, work_date):
        return (
            f"{reverse('availability_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def days_by_date(self, response):
        self.assertEqual(response.status_code, 200)
        return {day["date"]: day for week in response.context["calendar_weeks"] for day in week}

    def parse_calendar(self, response):
        parser = CalendarAvailabilityParser()
        parser.feed(response.content.decode())
        return parser

    def expected_ranges(self, availabilities):
        return [(availability.start_time.strftime("%H:%M"), availability.end_time.strftime("%H:%M"))
                for availability in availabilities]

    def test_each_day_contains_all_own_wishes_in_start_end_and_id_order(self):
        response = self.client.get(self.calendar_url)
        days = self.days_by_date(response)

        self.assertEqual(days[self.work_date]["availabilities"], self.selected_availabilities)
        self.assertIsInstance(days[self.work_date]["availabilities"], list)
        self.assertEqual(days[date(2026, 11, 18)]["availabilities"], [self.other_day_availability])
        self.assertEqual(days[date(2026, 11, 13)]["availabilities"], [])
        for day in days.values():
            for availability in day["availabilities"]:
                self.assertEqual(availability.user, self.staff)
                self.assertEqual(availability.work_date, day["date"])

    def test_calendar_link_shows_every_wish_and_accessible_time_in_order(self):
        response = self.client.get(self.calendar_url)
        parser = self.parse_calendar(response)
        expected = self.expected_ranges(self.selected_availabilities)

        self.assertEqual(parser.ranges(self.work_date), expected)
        self.assertEqual(parser.ranges(self.work_date, accessible=True), expected)
        self.assertIn("提出済み", parser.days[self.work_date]["aria-label"])
        self.assertEqual(parser.days[self.work_date].get("aria-current"), "date")

    def test_nonselected_days_show_their_wish_times_without_opening_details(self):
        response = self.client.get(self.calendar_url)
        parser = self.parse_calendar(response)
        work_date = self.other_day_availability.work_date
        expected = self.expected_ranges([self.other_day_availability])

        self.assertNotIn("is-selected", parser.days[work_date]["class"].split())
        self.assertEqual(parser.ranges(work_date), expected)
        self.assertEqual(parser.ranges(work_date, accessible=True), expected)
        self.assertEqual(
            parse_qs(urlsplit(parser.days[work_date]["href"]).query)["date"], [work_date.isoformat()]
        )

    def test_other_employees_times_do_not_leak_into_calendar_links_or_day_lists(self):
        response = self.client.get(self.calendar_url)
        parser = self.parse_calendar(response)
        calendar_text = "".join("".join(day["text"]) + day.get("aria-label", "")
                                for day in parser.days.values())

        for private_time in ["00:01", "00:02", "01:01", "01:02", "02:01", "02:02"]:
            with self.subTest(private_time=private_time):
                self.assertNotIn(private_time, calendar_text)
        self.assertEqual(parser.ranges(date(2026, 11, 13)), [])
        self.assertIn("未提出", parser.days[date(2026, 11, 13)]["aria-label"])

    def test_own_legacy_and_other_store_wishes_are_included_by_user(self):
        response = self.client.get(self.calendar_url)
        days = self.days_by_date(response)
        parser = self.parse_calendar(response)

        for availability in [self.legacy_availability, self.other_store_availability]:
            with self.subTest(availability=availability.pk):
                self.assertEqual(days[availability.work_date]["availabilities"], [availability])
                expected = self.expected_ranges([availability])
                self.assertEqual(parser.ranges(availability.work_date), expected)
                self.assertEqual(parser.ranges(availability.work_date, accessible=True), expected)

    def test_wishes_remain_visible_when_the_employee_has_no_active_membership(self):
        StoreMembership.objects.filter(user=self.staff).update(is_active=False)

        response = self.client.get(self.calendar_url)

        self.assertIsNone(response.context["membership"])
        self.assertEqual(self.days_by_date(response)[self.work_date]["availabilities"], self.selected_availabilities)
        self.assertEqual(self.parse_calendar(response).ranges(self.work_date), self.expected_ranges(self.selected_availabilities))

    def test_outside_month_muted_days_have_no_wishes_or_calendar_links(self):
        response = self.client.get(self.calendar_url)
        days = self.days_by_date(response)
        parser = self.parse_calendar(response)

        for work_date in [date(2026, 10, 31), date(2026, 12, 1)]:
            with self.subTest(work_date=work_date):
                self.assertFalse(days[work_date]["is_current_month"])
                self.assertEqual(days[work_date]["availabilities"], [])
                self.assertFalse(days[work_date]["is_submitted"])
                self.assertNotIn(work_date, parser.days)
        self.assertEqual(len(parser.days), 30)

    def test_submitted_summary_counts_dates_instead_of_wish_records(self):
        response = self.client.get(self.calendar_url)

        self.assertEqual(response.context["submitted_days_count"], 4)
        submitted_days = [day for day in self.days_by_date(response).values() if day["is_submitted"]]
        self.assertEqual(len(submitted_days), 4)
        self.assertEqual(sum(len(day["availabilities"]) for day in submitted_days), 7)

    def test_empty_month_keeps_every_day_with_empty_availability_lists(self):
        response = self.client.get(self.calendar_for(date(2027, 1, 1)))
        days = self.days_by_date(response)
        parser = self.parse_calendar(response)

        self.assertEqual(sorted(day["day"] for day in days.values() if day["is_current_month"]), list(range(1, 32)))
        self.assertTrue(all(day["availabilities"] == [] for day in days.values()))
        self.assertTrue(all(not day["is_submitted"] for day in days.values()))
        self.assertEqual(response.context["submitted_days_count"], 0)
        self.assertEqual(len(parser.days), 31)
        self.assertTrue(all(parser.ranges(work_date) == [] for work_date in parser.days))

    def test_selecting_next_month_displays_that_months_wishes_only(self):
        response = self.client.get(self.calendar_for(date(2026, 12, 1)))
        days = self.days_by_date(response)
        parser = self.parse_calendar(response)

        self.assertEqual(days[date(2026, 12, 1)]["availabilities"], [self.next_month_availability])
        self.assertEqual(parser.ranges(date(2026, 12, 1)), [("22:00", "23:00")])
        self.assertEqual(response.context["submitted_days_count"], 1)

    def test_deleted_wish_disappears_from_its_day_and_summary(self):
        response = self.client.post(reverse("availability_delete", args=[self.other_day_availability.pk]))

        self.assertRedirects(response, self.calendar_for(self.other_day_availability.work_date), fetch_redirect_response=False)
        calendar = self.client.get(self.calendar_url)
        self.assertEqual(self.days_by_date(calendar)[self.other_day_availability.work_date]["availabilities"], [])
        self.assertEqual(self.parse_calendar(calendar).ranges(self.other_day_availability.work_date), [])
        self.assertEqual(calendar.context["submitted_days_count"], 3)

    def test_manager_edit_immediately_updates_calendar_date_and_times(self):
        availability = self.selected_availabilities[0]
        new_date = date(2026, 11, 23)
        self.client.force_login(self.manager)

        response = self.client.post(reverse("manager_availability_edit", args=[availability.pk]), {
            "work_date": new_date.isoformat(), "start_time": "16:10", "end_time": "19:50",
        })

        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.staff)
        calendar = self.client.get(self.calendar_url)
        parser = self.parse_calendar(calendar)
        self.assertEqual(parser.ranges(self.work_date), self.expected_ranges(self.selected_availabilities[1:]))
        self.assertEqual(parser.ranges(new_date), [("16:10", "19:50")])
        self.assertEqual(parser.ranges(new_date, accessible=True), [("16:10", "19:50")])
        self.assertEqual(calendar.context["submitted_days_count"], 5)

    def test_calendar_get_does_not_write_to_the_database(self):
        before = list(Availability.objects.order_by("pk").values())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.calendar_url)
            self.assertEqual(response.status_code, 200)

        self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        )], [])
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.calendar_url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
