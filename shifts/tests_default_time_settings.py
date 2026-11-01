"""Per-store basic hours are saved settings, never availability restrictions."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from scheduler.services import generate_shifts_for_store
from shifts.eligibility import get_eligibility
from shifts.models import Availability, DayOffRequest, Requirement, Shift, ShiftSubmission


class DefaultTimeSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="基本時間の確認会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.second_store = Store.objects.create(company=company, name="夜の店")
        cls.foreign_store = Store.objects.create(company=company, name="所属のない店舗")
        cls.user = get_user_model().objects.create_user(username="default-time-staff")
        cls.other_user = get_user_model().objects.create_user(username="default-time-other")
        cls.member = StoreMembership.objects.create(user=cls.user, store=cls.store)
        cls.second_member = StoreMembership.objects.create(user=cls.user, store=cls.second_store)
        cls.other_member = StoreMembership.objects.create(user=cls.other_user, store=cls.store)
        cls.work_date = date(2030, 11, 12)

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, store=None):
        return reverse("submission_settings") + f"?store={(store or self.store).pk}&year=2030&month=11"

    def data(self, **overrides):
        return {
            "submission_mode": "day_off", "default_start_time": "10:00",
            "default_end_time": "15:00", **overrides,
        }

    def submitted_month(self):
        return ShiftSubmission.objects.create(
            membership=self.member, target_month=date(2030, 11, 1),
            submission_mode="day_off", is_submitted=True, submitted_at=timezone.now(),
        )

    def settings(self, membership=None):
        membership = membership or self.member
        membership.refresh_from_db()
        return membership.submission_mode, membership.default_start_time, membership.default_end_time

    def test_get_exposes_optional_time_fields_without_writes(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        for name in ("default_start_time", "default_end_time"):
            self.assertIn(name, form.fields)
            self.assertFalse(form.fields[name].required)
            self.assertEqual(form.fields[name].widget.input_type, "time")
        self.assertEqual(self.settings(), ("availability", None, None))
        self.assertFalse(ShiftSubmission.objects.exists())

    def test_saves_one_same_day_pair_per_person_and_store(self):
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(), ("day_off", time(10), time(15)))
        self.assertEqual(self.settings(self.second_member), ("availability", None, None))
        self.assertEqual(self.settings(self.other_member), ("availability", None, None))
        response = self.client.post(self.url(self.second_store), self.data(
            default_start_time="17:00", default_end_time="23:00",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(self.second_member), ("day_off", time(17), time(23)))
        self.assertEqual(self.settings(), ("day_off", time(10), time(15)))

    def test_blank_pair_is_valid_and_explicit_blanks_clear_previous_hours(self):
        self.client.post(self.url(), self.data())
        response = self.client.post(self.url(), self.data(default_start_time="", default_end_time=""))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(), ("day_off", None, None))

    def test_mode_only_post_preserves_existing_hours(self):
        self.client.post(self.url(), self.data())
        response = self.client.post(self.url(), {"submission_mode": "availability"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(), ("availability", time(10), time(15)))
        response = self.client.post(self.url(), {"submission_mode": "day_off"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(), ("day_off", time(10), time(15)))

    def test_invalid_or_partial_pair_does_not_change_saved_settings(self):
        self.client.post(self.url(), self.data())
        for overrides in [
            {"default_start_time": "", "default_end_time": "15:00"},
            {"default_start_time": "10:00", "default_end_time": ""},
            {"default_start_time": "15:00", "default_end_time": "15:00"},
            {"default_start_time": "18:00", "default_end_time": "02:00"},
            {"default_start_time": "25:00", "default_end_time": "15:00"},
        ]:
            with self.subTest(overrides=overrides):
                response = self.client.post(self.url(), self.data(**overrides))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(self.settings(), ("day_off", time(10), time(15)))
        response = self.client.post(self.url(), {"submission_mode": "day_off", "default_start_time": "12:00"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("default_end_time", response.context["form"].errors)
        self.assertEqual(self.settings(), ("day_off", time(10), time(15)))

    def test_midnight_start_and_same_day_evening_hours_are_valid(self):
        for start, end in [("00:00", "08:00"), ("17:00", "23:59")]:
            with self.subTest(start=start, end=end):
                response = self.client.post(self.url(), self.data(default_start_time=start, default_end_time=end))
                self.assertEqual(response.status_code, 302)

    def test_posted_identity_cannot_change_another_members_settings(self):
        response = self.client.post(self.url(), self.data(
            membership=self.other_member.pk, user=self.other_user.pk, store=self.second_store.pk,
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.settings(), ("day_off", time(10), time(15)))
        self.assertEqual(self.settings(self.other_member), ("availability", None, None))
        self.assertEqual(self.settings(self.second_member), ("availability", None, None))
        response = self.client.post(self.url(self.foreign_store), self.data())
        self.assertEqual(response.status_code, 404)

    def test_inactive_or_disallowed_members_and_anonymous_user_cannot_change_hours(self):
        self.member.can_submit_availability = False
        self.member.save(update_fields=["can_submit_availability"])
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 404)
        self.client.logout()
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertEqual(self.settings(), ("availability", None, None))

    def test_default_hours_do_not_change_submitted_wishes_or_eligibility(self):
        submission = self.submitted_month()
        DayOffRequest.objects.create(submission=submission, work_date=date(2030, 11, 13))
        Availability.objects.create(
            user=self.user, membership=self.member, store=self.store, submission=submission,
            work_date=date(2030, 11, 14), start_time=time(12), end_time=time(14),
        )
        before = {
            model: list(model.objects.order_by("pk").values())
            for model in (ShiftSubmission, Availability, DayOffRequest, Shift)
        }
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        for model, records in before.items():
            self.assertEqual(list(model.objects.order_by("pk").values()), records)
        self.member.refresh_from_db()
        eligibility = get_eligibility(self.member, self.work_date)
        self.assertTrue(eligibility.all_day)
        self.assertTrue(eligibility.covers(time(18), time(23)))
        self.assertFalse(get_eligibility(self.member, date(2030, 11, 13)).available)
        limited = get_eligibility(self.member, date(2030, 11, 14))
        self.assertTrue(limited.covers(time(12), time(14)))
        self.assertFalse(limited.covers(time(14), time(18)))

    def test_automatic_generation_can_still_use_times_outside_basic_hours(self):
        self.submitted_month()
        self.client.post(self.url(), self.data())
        Requirement.objects.create(
            store=self.store, work_date=self.work_date,
            start_time=time(18), end_time=time(22), required_staff_count=1,
        )
        result = generate_shifts_for_store(self.store.pk, self.work_date)
        self.assertEqual(result["created_count"], 1)
        shift = Shift.objects.get()
        self.assertEqual((shift.user, shift.start_time, shift.end_time), (self.user, time(18), time(22)))

    def test_database_rejects_partial_equal_or_reversed_default_pairs(self):
        for start, end in [(time(10), None), (None, time(15)), (time(15), time(15)), (time(18), time(2))]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    StoreMembership.objects.filter(pk=self.member.pk).update(
                        default_start_time=start, default_end_time=end,
                    )
        self.assertEqual(self.settings(), ("availability", None, None))
