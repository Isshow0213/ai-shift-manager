from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from .models import Availability, Requirement, Shift


class ShiftGenerationViewTests(TestCase):
    def setUp(self):
        self.work_date = date(2026, 7, 12)
        company = Company.objects.create(name="テスト会社")
        self.store = Store.objects.create(company=company, name="テスト店舗")
        self.manager = get_user_model().objects.create_user(username="manager")
        self.staff = get_user_model().objects.create_user(
            username="staff_login", last_name="柴田", first_name="一翔"
        )
        StoreMembership.objects.create(
            store=self.store, user=self.manager, role="manager"
        )
        self.membership = StoreMembership.objects.create(
            store=self.store, user=self.staff, role="staff"
        )
        Availability.objects.create(
            user=self.staff,
            membership=self.membership,
            work_date=self.work_date,
            start_time=time(8),
            end_time=time(19),
        )
        self.requirement = Requirement.objects.create(
            store=self.store,
            work_date=self.work_date,
            start_time=time(9),
            end_time=time(18),
            required_staff_count=1,
        )
        self.calendar_url = (
            f"{reverse('manager_shift_list')}?year=2026&month=7&date=2026-07-12"
        )
        self.client.force_login(self.manager)

    def test_generation_returns_to_selected_day_with_result(self):
        response = self.client.post(
            reverse("generate_shift"),
            {"work_date": "2026-07-12"},
            follow=True,
        )

        self.assertRedirects(response, self.calendar_url)
        self.assertTemplateUsed(response, "manager/shift_list.html")
        self.assertEqual(response.context["selected_date"], self.work_date)
        self.assertContains(response, "シフトを1件自動生成しました。")
        self.assertContains(response, 'name="work_date" value="2026-07-12"')
        shift = Shift.objects.get()
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.membership, self.membership)
        self.assertTrue(shift.is_generated)

    def test_missing_or_invalid_date_does_not_modify_shifts(self):
        existing = Shift.objects.create(
            user=self.staff,
            membership=self.membership,
            store=self.store,
            work_date=self.work_date,
            start_time=time(9),
            end_time=time(18),
            is_generated=True,
        )

        for value in [None, "", "invalid", "2026-02-30"]:
            with self.subTest(work_date=value):
                data = {} if value is None else {"work_date": value}
                response = self.client.post(reverse("generate_shift"), data)
                self.assertRedirects(response, reverse("manager_shift_list"))
                self.assertEqual(list(Shift.objects.values_list("pk", flat=True)), [existing.pk])

    def test_no_requirements_displays_warning(self):
        self.requirement.delete()

        response = self.client.post(
            reverse("generate_shift"), {"work_date": "2026-07-12"}, follow=True
        )

        self.assertRedirects(response, self.calendar_url)
        self.assertContains(response, "この日の必要人数が設定されていません。")
        self.assertFalse(Shift.objects.exists())

    def test_staff_shortage_is_visible_on_selected_day(self):
        self.requirement.required_staff_count = 2
        self.requirement.save()

        response = self.client.post(
            reverse("generate_shift"), {"work_date": "2026-07-12"}, follow=True
        )

        self.assertRedirects(response, self.calendar_url)
        self.assertContains(response, "合計1人分不足しています。")
        self.assertEqual(Shift.objects.count(), 1)

    def test_non_manager_and_anonymous_user_cannot_generate(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("generate_shift"), {"work_date": "2026-07-12"}
        )
        self.assertRedirects(
            response, reverse("manager_dashboard"), fetch_redirect_response=False
        )
        self.assertFalse(Shift.objects.exists())

        self.client.logout()
        response = self.client.post(
            reverse("generate_shift"), {"work_date": "2026-07-12"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))
        self.assertFalse(Shift.objects.exists())

    def test_get_does_not_generate_shifts(self):
        response = self.client.get(reverse("generate_shift"))

        self.assertRedirects(response, reverse("manager_shift_list"))
        self.assertFalse(Shift.objects.exists())

    def test_manual_shift_is_saved_with_store_and_selected_day(self):
        response = self.client.post(
            self.calendar_url,
            {
                "membership": self.membership.pk,
                "work_date": "2026-07-12",
                "start_time": "09:00",
                "end_time": "18:00",
            },
            follow=True,
        )

        self.assertRedirects(response, self.calendar_url)
        self.assertContains(response, "手動でシフトを追加しました。")
        shift = Shift.objects.get()
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.user, self.staff)
        self.assertEqual(shift.membership, self.membership)
        self.assertEqual(shift.work_date, self.work_date)
        self.assertFalse(shift.is_generated)

    def test_names_are_shown_in_manager_availabilities_and_staff_shifts(self):
        response = self.client.get(reverse("manager_availability_list"))
        self.assertContains(response, "柴田 一翔")
        self.assertNotContains(response, "staff_login")

        Shift.objects.create(
            user=self.staff,
            membership=None,
            store=self.store,
            work_date=self.work_date,
            start_time=time(9),
            end_time=time(18),
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("shift_list"))
        self.assertContains(response, "柴田 一翔")
        self.assertNotContains(response, "staff_login")
