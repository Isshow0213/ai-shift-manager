import json
from datetime import date, time, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift


SOURCE_ID = "manual-shift-availabilities"


class AutofillMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.source_attrs = None
        self.source_content = []
        self.form_attrs = None
        self.input_values = {}
        self.in_source = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == SOURCE_ID:
            self.source_attrs = attrs
            self.in_source = True
        if tag == "form" and attrs.get("data-availability-source") == SOURCE_ID:
            self.form_attrs = attrs
        if tag == "input" and attrs.get("name") in {"start_time", "end_time"}:
            self.input_values[attrs["name"]] = attrs.get("value")

    def handle_data(self, data):
        if self.in_source:
            self.source_content.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_source = False


class ShiftAvailabilityAutofillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(username="autofill_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff = user_model.objects.create_user(username="autofill_staff")
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff"
        )
        cls.second_staff = user_model.objects.create_user(username="autofill_second")
        cls.second_membership = StoreMembership.objects.create(
            user=cls.second_staff, store=cls.store, role="staff"
        )
        cls.foreign_staff = user_model.objects.create_user(username="autofill_foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            user=cls.foreign_staff, store=cls.other_store, role="staff"
        )
        cls.work_date = date(2026, 11, 15)

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = (
            f"{reverse('manager_shift_list')}?year={self.work_date.year}"
            f"&month={self.work_date.month}&date={self.work_date.isoformat()}"
        )

    def make_availability(
        self, membership=None, *, user=None, work_date=None,
        start_time=time(9), end_time=time(18),
    ):
        membership = membership or self.membership
        return Availability.objects.create(
            user=user or membership.user, membership=membership,
            work_date=work_date or self.work_date, start_time=start_time, end_time=end_time,
        )

    def assert_autofill_markup(self, response, expected, *, is_bound=False):
        parser = AutofillMarkupParser()
        parser.feed(response.content.decode())
        self.assertIsNotNone(parser.source_attrs)
        self.assertEqual(parser.source_attrs.get("type"), "application/json")
        self.assertEqual(json.loads("".join(parser.source_content)), expected)
        self.assertIsNotNone(parser.form_attrs)
        self.assertEqual(parser.form_attrs.get("data-is-bound"), "true" if is_bound else "false")
        return parser

    def test_selected_day_data_only_contains_selectable_staff_and_preserves_separate_slots(self):
        late = self.make_availability(start_time=time(16), end_time=time(18))
        long = self.make_availability(start_time=time(9), end_time=time(12))
        first_short = self.make_availability(start_time=time(9), end_time=time(10))
        second_short = self.make_availability(start_time=time(9), end_time=time(10))
        # 既存の候補判定と同じく、人物は所属を基準にする。
        mismatched_user = self.make_availability(
            user=self.foreign_staff, start_time=time(13), end_time=time(14)
        )
        second_staff_slot = self.make_availability(
            self.second_membership, start_time=time(10, 30), end_time=time(15, 45)
        )
        inactive_staff = get_user_model().objects.create_user(username="autofill_inactive")
        inactive_membership = StoreMembership.objects.create(
            user=inactive_staff, store=self.store, role="staff", is_active=False
        )
        disabled_staff = get_user_model().objects.create_user(
            username="autofill_disabled", is_active=False
        )
        disabled_membership = StoreMembership.objects.create(
            user=disabled_staff, store=self.store, role="staff"
        )
        for membership in [
            self.manager_membership, self.foreign_membership,
            inactive_membership, disabled_membership,
        ]:
            self.make_availability(membership)
        self.make_availability(work_date=self.work_date + timedelta(days=1))
        Availability.objects.create(
            user=self.staff, work_date=self.work_date,
            start_time=time(7), end_time=time(8), membership=None,
        )
        expected = {
            "date": self.work_date.isoformat(),
            "memberships": {
                str(self.membership.pk): [
                    {"id": first_short.pk, "start_time": "09:00", "end_time": "10:00"},
                    {"id": second_short.pk, "start_time": "09:00", "end_time": "10:00"},
                    {"id": long.pk, "start_time": "09:00", "end_time": "12:00"},
                    {"id": mismatched_user.pk, "start_time": "13:00", "end_time": "14:00"},
                    {"id": late.pk, "start_time": "16:00", "end_time": "18:00"},
                ],
                str(self.second_membership.pk): [
                    {"id": second_staff_slot.pk, "start_time": "10:30", "end_time": "15:45"},
                ],
            },
        }
        before_availabilities = list(Availability.objects.order_by("pk").values())
        before_memberships = list(StoreMembership.objects.order_by("pk").values())
        before_shifts = list(Shift.objects.order_by("pk").values())

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["availability_autofill"], expected)
        self.assertEqual(
            set(response.context["form"].fields["membership"].queryset),
            {self.membership, self.second_membership},
        )
        self.assert_autofill_markup(response, expected)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before_availabilities)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), before_memberships)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before_shifts)

    def test_no_selected_day_availability_keeps_empty_json_and_form_source(self):
        self.make_availability(work_date=self.work_date - timedelta(days=1))
        expected = {"date": self.work_date.isoformat(), "memberships": {}}

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["availability_autofill"], expected)
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(list(response.context["form"].fields["membership"].queryset), [])
        self.assert_autofill_markup(response, expected)

    def test_invalid_post_keeps_submitted_times_in_form_and_markup(self):
        availability = self.make_availability()
        expected = {
            "date": self.work_date.isoformat(),
            "memberships": {str(self.membership.pk): [
                {"id": availability.pk, "start_time": "09:00", "end_time": "18:00"},
            ]},
        }

        response = self.client.post(self.url, {
            "membership": self.membership.pk, "work_date": self.work_date.isoformat(),
            "start_time": "13:37", "end_time": "12:19",
        })

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertTrue(form.errors.get("end_time"))
        self.assertEqual(form["start_time"].value(), "13:37")
        self.assertEqual(form["end_time"].value(), "12:19")
        self.assertEqual(response.context["availability_autofill"], expected)
        parser = self.assert_autofill_markup(response, expected, is_bound=True)
        self.assertEqual(parser.input_values, {"start_time": "13:37", "end_time": "12:19"})
        self.assertFalse(Shift.objects.exists())

    def test_staff_inactive_manager_and_anonymous_requests_keep_access_restrictions(self):
        self.make_availability()
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.get(self.url)
        login_url = f"{reverse('login')}?{urlencode({'next': self.url}, safe='/')}"
        self.assertRedirects(response, login_url, fetch_redirect_response=False)
