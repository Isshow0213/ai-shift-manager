from datetime import UTC, date, datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership

from .calendar_utils import get_calendar_context
from .models import Availability, Shift


class EmployeeCalendarContextTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def calendar_context(self, query=None):
        with patch(
            "shifts.calendar_utils.timezone.now",
            return_value=datetime(2026, 10, 6, 15, 30, tzinfo=UTC),
        ):
            return get_calendar_context(self.factory.get("/availability/", query or {}))

    def test_current_month_uses_today_in_japan(self):
        context = self.calendar_context()

        self.assertEqual(context["today"], date(2026, 10, 7))
        self.assertEqual(context["selected_date"], date(2026, 10, 7))
        self.assertEqual((context["year"], context["month"]), (2026, 10))

    def test_month_navigation_selects_first_day_and_handles_year_boundary(self):
        context = self.calendar_context({"year": "2027", "month": "1"})

        self.assertEqual(context["selected_date"], date(2027, 1, 1))
        self.assertEqual((context["prev_year"], context["prev_month"]), (2026, 12))
        self.assertEqual((context["next_year"], context["next_month"]), (2027, 2))

    def test_invalid_parameters_and_other_month_dates_fall_back_safely(self):
        examples = [
            ({"year": "invalid", "month": "invalid"}, date(2026, 10, 7)),
            ({"year": "0", "month": "0"}, date(2026, 10, 7)),
            ({"year": "10000", "month": "13"}, date(2026, 10, 7)),
            ({"year": "1", "month": "1"}, date(2026, 1, 1)),
            ({"year": "9999", "month": "12"}, date(2026, 12, 1)),
            ({"date": "invalid"}, date(2026, 10, 7)),
            ({"date": "2026-02-30"}, date(2026, 10, 7)),
            ({"year": "2026", "month": "11", "date": "2026-10-12"}, date(2026, 11, 1)),
            ({"year": "2026", "month": "11", "date": "2026-11-12"}, date(2026, 11, 12)),
        ]
        for query, expected in examples:
            with self.subTest(query=query):
                context = self.calendar_context(query)
                self.assertEqual(context["selected_date"], expected)
                self.assertEqual(
                    (context["selected_date"].year, context["selected_date"].month),
                    (context["year"], context["month"]),
                )


class EmployeeShiftViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.employee = user_model.objects.create_user(
            username="employee_login", last_name="田中", first_name="花子"
        )
        cls.other_employee = user_model.objects.create_user(
            username="other_employee_login", last_name="佐藤", first_name="美咲"
        )
        StoreMembership.objects.create(
            user=cls.employee, store=cls.other_store, is_active=False
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.employee, store=cls.store, role="staff"
        )
        cls.other_membership = StoreMembership.objects.create(
            user=cls.other_employee, store=cls.store, role="staff"
        )
        cls.work_date = date(2026, 10, 12)

    def setUp(self):
        self.client.force_login(self.employee)
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 7)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)
        self.availability_url = (
            f"{reverse('availability_list')}?year=2026&month=10&date=2026-10-12"
        )
        self.shift_url = f"{reverse('shift_list')}?year=2026&month=10"

    def make_availability(self, *, membership=None, work_date=None, start_time=time(9)):
        membership = membership or self.membership
        return Availability.objects.create(
            user=membership.user,
            membership=membership,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=time(18),
        )

    def make_shift(self, *, membership=None, work_date=None, start_time=time(9)):
        membership = membership or self.membership
        return Shift.objects.create(
            user=membership.user,
            membership=membership,
            store=membership.store,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=time(18),
        )

    def test_empty_calendar_still_has_every_day_and_click_urls(self):
        response = self.client.get(reverse("availability_list"))

        self.assertEqual(response.status_code, 200)
        current_days = [
            day
            for week in response.context["calendar_weeks"]
            for day in week
            if day["is_current_month"]
        ]
        self.assertEqual([day["day"] for day in current_days], list(range(1, 32)))
        self.assertEqual(response.context["selected_date"], date(2026, 10, 7))
        self.assertEqual(response.context["submitted_days_count"], 0)
        self.assertEqual(response.context["monthly_shifts_count"], 0)
        self.assertEqual(current_days[11]["url"], "?year=2026&month=10&date=2026-10-12")
        self.assertTrue(all(not day["is_submitted"] for day in current_days))
        self.assertTrue(all(not day["has_shift"] for day in current_days))
        self.assertEqual(list(response.context["selected_shifts"]), [])

    def test_calendar_details_and_month_totals_only_include_the_employee(self):
        first = self.make_availability()
        second = self.make_availability(start_time=time(13))
        self.make_availability(work_date=date(2026, 10, 13))
        self.make_availability(work_date=date(2026, 11, 1))
        self.make_availability(membership=self.other_membership)
        selected_shift = self.make_shift()
        self.make_shift(work_date=date(2026, 10, 13))
        self.make_shift(work_date=date(2026, 11, 1))
        self.make_shift(membership=self.other_membership)

        response = self.client.get(self.availability_url)

        self.assertEqual(response.context["membership"], self.membership)
        self.assertEqual(list(response.context["selected_availabilities"]), [first, second])
        self.assertEqual(list(response.context["selected_shifts"]), [selected_shift])
        self.assertEqual(response.context["submitted_days_count"], 2)
        self.assertEqual(response.context["monthly_shifts_count"], 2)
        self.assertNotContains(response, "佐藤 美咲")

    def test_month_navigation_and_invalid_query_do_not_return_server_errors(self):
        examples = [
            ({"year": "2026", "month": "11"}, date(2026, 11, 1)),
            ({"year": "invalid", "month": "13", "date": "2026-02-30"}, date(2026, 10, 7)),
            ({"year": "2026", "month": "11", "date": "2026-10-12"}, date(2026, 11, 1)),
        ]
        for query, selected_date in examples:
            with self.subTest(query=query):
                response = self.client.get(reverse("availability_list"), query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["selected_date"], selected_date)

    def test_submission_form_initial_date_and_labels_are_japanese(self):
        response = self.client.get(
            reverse("availability_create"), {"date": "2026-11-12"}
        )

        self.assertEqual(response.context["selected_date"], date(2026, 11, 12))
        form = response.context["form"]
        self.assertEqual(form.initial["work_date"], date(2026, 11, 12))
        self.assertEqual(form.fields["work_date"].label, "日付")
        self.assertEqual(form.fields["start_time"].label, "開始時刻")
        self.assertEqual(form.fields["end_time"].label, "終了時刻")

    def test_submission_saves_authenticated_user_and_active_membership(self):
        response = self.client.post(
            reverse("availability_create"),
            {
                "work_date": "2026-10-12",
                "start_time": "09:00",
                "end_time": "18:00",
                "user": self.other_employee.pk,
                "membership": self.other_membership.pk,
            },
            follow=True,
        )

        self.assertRedirects(response, self.availability_url)
        saved = Availability.objects.get()
        self.assertEqual(saved.user, self.employee)
        self.assertEqual(saved.membership, self.membership)
        self.assertEqual(saved.work_date, self.work_date)
        self.assertContains(response, "シフト希望を提出しました。")

    def test_same_or_reversed_times_are_rejected_without_saving(self):
        for start_time, end_time in [("09:00", "09:00"), ("18:00", "09:00")]:
            with self.subTest(start_time=start_time, end_time=end_time):
                response = self.client.post(
                    reverse("availability_create"),
                    {
                        "work_date": "2026-10-12",
                        "start_time": start_time,
                        "end_time": end_time,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertFormError(
                    response.context["form"], None,
                    "終了時刻は開始時刻より後にしてください。",
                )
                self.assertFalse(Availability.objects.exists())

    def test_invalid_initial_date_falls_back_and_user_without_membership_cannot_submit(self):
        response = self.client.get(
            reverse("availability_create"), {"date": "2026-02-30"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_date"], date(2026, 10, 7))
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("availability_create"),
            {"work_date": "2026-10-12", "start_time": "09:00", "end_time": "18:00"},
            follow=True,
        )

        self.assertRedirects(response, reverse("availability_list"))
        self.assertContains(response, "所属している店舗がありません。")
        self.assertFalse(Availability.objects.exists())

    def test_delete_post_removes_own_availability_and_returns_to_its_day(self):
        availability = self.make_availability()

        response = self.client.post(
            reverse("availability_delete", args=[availability.pk]), follow=True
        )

        self.assertRedirects(response, self.availability_url)
        self.assertFalse(Availability.objects.filter(pk=availability.pk).exists())
        self.assertContains(response, "シフト希望を削除しました。")

    def test_delete_get_keeps_availability_and_returns_to_its_day(self):
        availability = self.make_availability()

        response = self.client.get(reverse("availability_delete", args=[availability.pk]))

        self.assertRedirects(response, self.availability_url)
        self.assertTrue(Availability.objects.filter(pk=availability.pk).exists())

    def test_other_employees_availability_cannot_be_deleted_or_read(self):
        availability = self.make_availability(membership=self.other_membership)
        url = reverse("availability_delete", args=[availability.pk])
        for method in [self.client.get, self.client.post]:
            with self.subTest(method=method.__name__):
                self.assertEqual(method(url).status_code, 404)
                self.assertTrue(Availability.objects.filter(pk=availability.pk).exists())

    def test_confirmed_shifts_are_monthly_own_records_with_distinct_day_count(self):
        first = self.make_shift()
        second = self.make_shift(start_time=time(13))
        third = self.make_shift(work_date=date(2026, 10, 13))
        self.make_shift(work_date=date(2026, 11, 1))
        self.make_shift(membership=self.other_membership)

        response = self.client.get(self.shift_url)

        self.assertTemplateUsed(response, "shifts/shift_list.html")
        self.assertEqual(response.context["membership"], self.membership)
        self.assertEqual(list(response.context["shifts"]), [first, second, third])
        self.assertEqual(response.context["shift_days_count"], 2)
        self.assertEqual(response.context["shifts_count"], 3)
        self.assertNotContains(response, "佐藤 美咲")

    def test_confirmed_shift_empty_month_and_invalid_query_are_valid_pages(self):
        self.make_shift()
        for query, expected_date in [
            ({"year": "2026", "month": "11"}, date(2026, 11, 1)),
            ({"year": "invalid", "month": "0"}, date(2026, 10, 7)),
        ]:
            with self.subTest(query=query):
                response = self.client.get(reverse("shift_list"), query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["selected_date"], expected_date)
                if query["month"] == "11":
                    self.assertEqual(list(response.context["shifts"]), [])
                    self.assertEqual(response.context["shift_days_count"], 0)
                    self.assertEqual(response.context["shifts_count"], 0)

    def test_anonymous_users_are_redirected_to_login_for_employee_pages(self):
        availability = self.make_availability()
        self.client.logout()
        urls = [
            reverse("availability_list"),
            reverse("availability_create"),
            reverse("availability_delete", args=[availability.pk]),
            reverse("shift_list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(
                    response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
                )
    def test_availability_form_displays_previous_availabilities_and_requirements(self):
        from .models import Requirement
        
        # Create previous availabilities (before selected_date)
        Availability.objects.create(
            user=self.employee,
            membership=self.membership,
            work_date=date(2026, 10, 5),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        Availability.objects.create(
            user=self.employee,
            membership=self.membership,
            work_date=date(2026, 10, 1),
            start_time=time(10, 0),
            end_time=time(15, 0),
        )
        
        # Create requirements for the selected date
        Requirement.objects.create(
            store=self.membership.store,
            work_date=self.work_date,
            start_time=time(8, 0),
            end_time=time(17, 0),
            required_staff_count=5,
        )
        Requirement.objects.create(
            store=self.membership.store,
            work_date=self.work_date,
            start_time=time(18, 0),
            end_time=time(22, 0),
            required_staff_count=3,
        )
        
        response = self.client.get(
            reverse("availability_create"), {"date": self.work_date.isoformat()}
        )
        
        self.assertEqual(response.status_code, 200)
        # Check that previous availabilities are passed to template
        self.assertIn("previous_availabilities_unique", response.context)
        self.assertEqual(len(response.context["previous_availabilities_unique"]), 2)
        # Check that requirements for the date are passed to template
        self.assertIn("requirements_for_date", response.context)
        self.assertEqual(len(response.context["requirements_for_date"]), 2)
        # Check that the content is displayed in the response
        self.assertContains(response, "前回提出した希望時間")
        self.assertContains(response, "店長が設定した必要時間")
        self.assertContains(response, "09:00〜18:00")  # Previous availability
        self.assertContains(response, "08:00〜17:00")  # Requirement
        self.assertContains(response, "18:00〜22:00")  # Requirement