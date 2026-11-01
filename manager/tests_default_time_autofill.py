"""Personal default times only suggest initial values; they never limit eligibility."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from scheduler.services import generate_shifts_for_store
from shifts.eligibility import get_eligibility
from shifts.models import Availability, DayOffRequest, Requirement, Shift, ShiftSubmission, StoreOperatingHours


class PersonalDefaultAutofillTests(TestCase):
    work_date = date(2030, 11, 12)

    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="基本時間の会社")
        cls.store = Store.objects.create(company=company, name="昼夜の店舗")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        users = get_user_model()
        cls.manager = users.objects.create_user(username="default_manager")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.day_user = users.objects.create_user(username="default_day")
        cls.day = StoreMembership.objects.create(
            user=cls.day_user, store=cls.store, role="staff", submission_mode="day_off",
            default_start_time=time(10), default_end_time=time(15),
        )
        cls.night_user = users.objects.create_user(username="default_night")
        cls.night = StoreMembership.objects.create(
            user=cls.night_user, store=cls.store, role="staff", submission_mode="day_off",
            default_start_time=time(17), default_end_time=time(23),
        )
        cls.other_membership = StoreMembership.objects.create(
            user=cls.day_user, store=cls.other_store, role="staff", submission_mode="day_off",
            default_start_time=time(6), default_end_time=time(8),
        )
        cls.day_submission = cls.make_submission(cls.day)
        cls.night_submission = cls.make_submission(cls.night)
        cls.make_submission(cls.other_membership)

    @classmethod
    def make_submission(cls, membership):
        return ShiftSubmission.objects.create(
            membership=membership, target_month=cls.work_date.replace(day=1),
            submission_mode="day_off", is_submitted=True, submitted_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.query = {
            "year": self.work_date.year, "month": self.work_date.month,
            "date": self.work_date.isoformat(),
        }

    def calendar_data(self):
        response = self.client.get(reverse("manager_shift_list"), self.query)
        self.assertEqual(response.status_code, 200)
        return response.context["availability_autofill"]

    def overview_context(self):
        response = self.client.get(reverse("manager_shift_overview"), self.query)
        self.assertEqual(response.status_code, 200)
        return response.context

    def overview_cell(self, context, membership=None):
        member = membership or self.day
        row = next(row for row in context["rows"] if row["user"].pk == member.user_id)
        return next(cell for cell in row["cells"] if cell["date"] == self.work_date)

    def explicit_wish(self, start=time(12), end=time(14)):
        return Availability.objects.create(
            user=self.day_user, membership=self.day, store=self.store,
            submission=self.day_submission, work_date=self.work_date,
            start_time=start, end_time=end,
        )

    def test_day_and_night_defaults_are_separate_from_shared_fallback(self):
        Requirement.objects.create(
            store=self.store, work_date=self.work_date, start_time=time(8),
            end_time=time(22), required_staff_count=2,
        )
        data = self.calendar_data()
        self.assertEqual(data["all_day_default_times"], {
            str(self.day.pk): {"start_time": "10:00", "end_time": "15:00"},
            str(self.night.pk): {"start_time": "17:00", "end_time": "23:00"},
        })
        self.assertEqual((data["all_day_start_time"], data["all_day_end_time"]), ("08:00", "22:00"))
        self.assertEqual(data["memberships"][str(self.day.pk)], [])
        self.assertFalse(Availability.objects.exists())

    def test_explicit_daily_wishes_take_priority_over_the_personal_default(self):
        availability = self.explicit_wish()
        data = self.calendar_data()
        self.assertNotIn(str(self.day.pk), data.get("all_day_default_times", {}))
        self.assertEqual(data["memberships"][str(self.day.pk)], [{
            "id": availability.pk, "start_time": "12:00", "end_time": "14:00",
        }])
        context = self.overview_context()
        self.assertNotIn(self.work_date.isoformat(), context["overview_default_times"].get(str(self.day.pk), {}))
        self.assertEqual(context["overview_wish_times"][str(self.day.pk)][self.work_date.isoformat()], [
            {"start_time": "12:00", "end_time": "14:00"},
        ])

    def test_day_off_dates_do_not_receive_the_default(self):
        DayOffRequest.objects.create(submission=self.day_submission, work_date=self.work_date)
        self.assertNotIn(str(self.day.pk), self.calendar_data().get("all_day_default_times", {}))
        context = self.overview_context()
        self.assertNotIn(self.work_date.isoformat(), context["overview_default_times"].get(str(self.day.pk), {}))
        self.assertIn(str(self.night.pk), context["overview_default_times"])

    def test_unsubmitted_and_availability_mode_members_do_not_receive_defaults(self):
        self.day_submission.is_submitted = False
        self.day_submission.save(update_fields=["is_submitted"])
        self.night_submission.submission_mode = "availability"
        self.night_submission.save(update_fields=["submission_mode"])
        Availability.objects.create(
            user=self.night_user, membership=self.night, store=self.store,
            submission=self.night_submission, work_date=self.work_date,
            start_time=time(17), end_time=time(23),
        )
        self.assertEqual(self.calendar_data().get("all_day_default_times", {}), {})
        context = self.overview_context()
        self.assertNotIn(str(self.day.pk), context["overview_default_times"])
        self.assertNotIn(str(self.night.pk), context["overview_default_times"])

    def test_inactive_people_and_other_store_memberships_are_excluded(self):
        self.day.is_active = False
        self.day.save(update_fields=["is_active"])
        self.night_user.is_active = False
        self.night_user.save(update_fields=["is_active"])
        data = self.calendar_data()
        self.assertEqual(data.get("all_day_default_times", {}), {})
        self.assertNotIn(str(self.other_membership.pk), data["memberships"])
        self.assertEqual(self.overview_context()["overview_default_times"], {})

    def test_same_person_uses_the_selected_store_default_only(self):
        data = self.calendar_data()
        self.assertEqual(data["all_day_default_times"][str(self.day.pk)], {
            "start_time": "10:00", "end_time": "15:00",
        })
        self.assertNotIn(str(self.other_membership.pk), data["all_day_default_times"])
        context = self.overview_context()
        self.assertEqual(context["overview_default_times"][str(self.day.pk)][self.work_date.isoformat()], {
            "start_time": "10:00", "end_time": "15:00",
        })
        self.assertNotIn(str(self.other_membership.pk), context["overview_default_times"])

    def test_missing_defaults_keep_the_existing_shared_hours_fallback(self):
        StoreMembership.objects.filter(pk__in=[self.day.pk, self.night.pk]).update(
            default_start_time=None, default_end_time=None,
        )
        StoreOperatingHours.objects.create(store=self.store, start_time=time(8, 30), end_time=time(20))
        data = self.calendar_data()
        self.assertNotIn("all_day_default_times", data)
        self.assertEqual((data["all_day_start_time"], data["all_day_end_time"]), ("08:30", "20:00"))
        self.assertEqual(self.overview_context()["overview_default_times"], {})
        StoreOperatingHours.objects.filter(store=self.store).delete()
        data = self.calendar_data()
        self.assertEqual((data["all_day_start_time"], data["all_day_end_time"]), ("00:00", "23:59"))

    def test_submitted_month_mode_is_used_instead_of_later_settings_mode(self):
        self.day.submission_mode = "availability"
        self.day.save(update_fields=["submission_mode"])
        self.assertIn(str(self.day.pk), self.calendar_data()["all_day_default_times"])
        self.assertIn(self.work_date.isoformat(), self.overview_context()["overview_default_times"][str(self.day.pk)])

    def test_overview_defaults_do_not_become_wish_labels_or_time_limits(self):
        eligibility = get_eligibility(self.day, self.work_date)
        self.assertTrue(eligibility.all_day)
        self.assertTrue(eligibility.covers(time(20), time(22)))
        context = self.overview_context()
        self.assertNotIn(str(self.day.pk), context["overview_wish_times"])
        self.assertEqual(self.overview_cell(context)["unmet_preference"], "")
        response = self.client.post(
            reverse("manager_shift_overview") + "?year=2030&month=11", {
                "membership": self.day.pk, "work_date": self.work_date.isoformat(),
                "start_time": "20:00", "end_time": "22:00", "display_color": "blue", "note": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        saved = Shift.objects.get(user=self.day_user)
        self.assertEqual((saved.start_time, saved.end_time), (time(20), time(22)))
        self.assertFalse(Availability.objects.exists())
        self.assertEqual(self.overview_cell(self.overview_context())["unmet_preference"], "")

    def test_generator_still_allows_work_outside_the_personal_initial_times(self):
        Requirement.objects.create(
            store=self.store, work_date=self.work_date, start_time=time(20),
            end_time=time(22), required_staff_count=1,
        )
        result = generate_shifts_for_store(self.store.pk, self.work_date)
        self.assertEqual(result["created_count"], 1)
        saved = Shift.objects.get()
        self.assertEqual(saved.membership, self.day)
        self.assertEqual((saved.start_time, saved.end_time), (time(20), time(22)))
