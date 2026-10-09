from datetime import date, time
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from .forms import StoreOperatingHoursForm
from .models import Availability, Requirement, StoreOperatingHours


class FullDayButtonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "all-day-button" in attrs.get("class", "").split():
            self.buttons.append(attrs)


class StoreOperatingHoursFormTests(SimpleTestCase):
    def test_only_time_fields_are_present_required_and_validated(self):
        form = StoreOperatingHoursForm({"start_time": "09:30", "end_time": "18:15"})

        self.assertEqual(set(form.fields), {"start_time", "end_time"})
        self.assertTrue(all(field.required for field in form.fields.values()))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["start_time"], time(9, 30))
        self.assertEqual(form.cleaned_data["end_time"], time(18, 15))

    def test_missing_invalid_equal_and_reversed_hours_are_rejected(self):
        for data in [
            {}, {"start_time": "09:00"}, {"end_time": "18:00"},
            {"start_time": "invalid", "end_time": "18:00"},
            {"start_time": "09:00", "end_time": "24:00"},
            {"start_time": "09:00", "end_time": "09:00"},
            {"start_time": "18:00", "end_time": "09:00"},
        ]:
            with self.subTest(data=data):
                form = StoreOperatingHoursForm(data)
                self.assertFalse(form.is_valid())
                self.assertTrue(form.errors)


class FullDayHoursAndAvailabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(username="full_day_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff = user_model.objects.create_user(username="full_day_staff")
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff"
        )
        cls.foreign_staff = user_model.objects.create_user(username="full_day_foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            user=cls.foreign_staff, store=cls.other_store, role="staff"
        )
        cls.other_hours = StoreOperatingHours.objects.create(
            store=cls.other_store, start_time=time(6), end_time=time(20)
        )
        cls.work_date = date(2026, 11, 18)
        Requirement.objects.create(
            store=cls.store, work_date=cls.work_date, start_time=time(9),
            end_time=time(18), required_staff_count=1,
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.requirements_url = reverse("manager_requirement_list")
        self.submission_url = f"{reverse('availability_create')}?date={self.work_date.isoformat()}"

    def hours_data(self, **overrides):
        data = {"form_type": "operating_hours", "start_time": "09:30", "end_time": "18:15"}
        data.update(overrides)
        return data

    def buttons(self, response):
        parser = FullDayButtonParser()
        parser.feed(response.content.decode())
        return parser.buttons

    def test_requirement_get_neither_creates_hours_nor_updates_existing_business_data(self):
        before_hours = list(StoreOperatingHours.objects.order_by("pk").values())
        before_requirements = list(Requirement.objects.order_by("pk").values())

        response = self.client.get(self.requirements_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["operating_hours_form"].is_bound)
        self.assertFalse(StoreOperatingHours.objects.filter(store=self.store).exists())
        self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before_hours)
        self.assertEqual(list(Requirement.objects.order_by("pk").values()), before_requirements)
        StoreOperatingHours.objects.create(store=self.store, start_time=time(9), end_time=time(18))
        before_hours = list(StoreOperatingHours.objects.order_by("pk").values())

        response = self.client.get(self.requirements_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before_hours)
        self.assertEqual(list(Requirement.objects.order_by("pk").values()), before_requirements)

    def test_hours_posts_create_and_update_only_managers_store_despite_forged_fields(self):
        before_other = StoreOperatingHours.objects.filter(pk=self.other_hours.pk).values().get()
        before_requirements = list(Requirement.objects.order_by("pk").values())

        for start, end, expected_start, expected_end in [
            ("09:30", "18:15", time(9, 30), time(18, 15)),
            ("08:45", "19:00", time(8, 45), time(19)),
        ]:
            with self.subTest(start=start, end=end):
                response = self.client.post(self.requirements_url, self.hours_data(
                    start_time=start, end_time=end, store=self.other_store.pk,
                    id=self.other_hours.pk, user=self.foreign_staff.pk,
                ))
                self.assertRedirects(response, self.requirements_url, fetch_redirect_response=False)
                hours = StoreOperatingHours.objects.get(store=self.store)
                self.assertEqual(hours.start_time, expected_start)
                self.assertEqual(hours.end_time, expected_end)
                self.assertEqual(StoreOperatingHours.objects.filter(store=self.store).count(), 1)
                self.assertEqual(StoreOperatingHours.objects.filter(pk=self.other_hours.pk).values().get(), before_other)
                self.assertEqual(list(Requirement.objects.order_by("pk").values()), before_requirements)

    def test_invalid_hours_posts_keep_saved_hours_or_leave_unconfigured_store_without_a_row(self):
        before_requirements = list(Requirement.objects.order_by("pk").values())
        for configured in [False, True]:
            if configured:
                StoreOperatingHours.objects.create(store=self.store, start_time=time(9), end_time=time(18))
            before_hours = list(StoreOperatingHours.objects.order_by("pk").values())
            for start, end in [("", "18:00"), ("09:00", ""), ("09:00", "09:00"), ("18:00", "09:00")]:
                with self.subTest(configured=configured, start=start, end=end):
                    response = self.client.post(self.requirements_url, self.hours_data(start_time=start, end_time=end))
                    self.assertEqual(response.status_code, 200)
                    form = response.context["operating_hours_form"]
                    self.assertTrue(form.is_bound)
                    self.assertTrue(form.errors)
                    self.assertEqual(form["start_time"].value(), start)
                    self.assertEqual(form["end_time"].value(), end)
                    self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before_hours)
                    self.assertEqual(list(Requirement.objects.order_by("pk").values()), before_requirements)

    def test_staff_inactive_manager_and_anonymous_cannot_save_hours(self):
        before = list(StoreOperatingHours.objects.order_by("pk").values())
        self.client.force_login(self.staff)
        response = self.client.post(self.requirements_url, self.hours_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before)
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        response = self.client.post(self.requirements_url, self.hours_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before)
        self.client.logout()
        response = self.client.post(self.requirements_url, self.hours_data())
        self.assertRedirects(response, f"{reverse('login')}?next={self.requirements_url}", fetch_redirect_response=False)
        self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before)

    def test_full_day_button_only_uses_complete_ordered_hours_from_own_store(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.submission_url)
        self.assertFalse(response.context["has_full_day_hours"])
        self.assertEqual(self.buttons(response), [])

        for start, end, expected_visible in [
            (None, None, False), (time(9), None, False), (None, time(18), False),
            (time(18), time(9), False), (time(9), time(9), False),
            (time(9, 30), time(18, 15), True),
        ]:
            with self.subTest(start=start, end=end):
                StoreOperatingHours.objects.update_or_create(
                    store=self.store, defaults={"start_time": start, "end_time": end}
                )
                before = list(StoreOperatingHours.objects.order_by("pk").values())
                response = self.client.get(self.submission_url)
                self.assertEqual(response.context["has_full_day_hours"], expected_visible)
                buttons = self.buttons(response)
                self.assertEqual(len(buttons), int(expected_visible))
                if expected_visible:
                    self.assertEqual(buttons[0].get("type"), "button")
                    self.assertEqual(buttons[0].get("data-start-time"), "09:30")
                    self.assertEqual(buttons[0].get("data-end-time"), "18:15")
                self.assertEqual(list(StoreOperatingHours.objects.order_by("pk").values()), before)

    def test_full_day_submission_saves_current_user_membership_date_and_rejects_duplicate(self):
        StoreOperatingHours.objects.create(store=self.store, start_time=time(9, 30), end_time=time(18, 15))
        Availability.objects.create(
            user=self.foreign_staff, membership=self.foreign_membership,
            work_date=self.work_date, start_time=time(9, 30), end_time=time(18, 15),
        )
        self.client.force_login(self.staff)
        data = {
            "work_date": self.work_date.isoformat(), "start_time": "09:30", "end_time": "18:15",
            "user": self.foreign_staff.pk, "membership": self.foreign_membership.pk,
            "store": self.other_store.pk,
        }

        response = self.client.post(self.submission_url, data)

        calendar_url = (
            f"{reverse('availability_list')}?year={self.work_date.year}"
            f"&month={self.work_date.month}&date={self.work_date.isoformat()}"
        )
        self.assertRedirects(response, calendar_url, fetch_redirect_response=False)
        availability = Availability.objects.get(user=self.staff)
        self.assertEqual(availability.membership, self.membership)
        self.assertEqual(availability.work_date, self.work_date)
        self.assertEqual(availability.start_time, time(9, 30))
        self.assertEqual(availability.end_time, time(18, 15))
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(self.submission_url, data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("重複", str(response.context["form"].non_field_errors()))
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
