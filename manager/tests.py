from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift


User = get_user_model()


class StaffManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=cls.company, name="本店")
        cls.other_store = Store.objects.create(company=cls.company, name="別店舗")
        cls.manager = User.objects.create_user(
            username="manager-login", last_name="柴田", first_name="一翔"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff = User.objects.create_user(
            username="staff-login", last_name="田中", first_name="花子"
        )
        cls.staff_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="A",
            desired_shifts_per_week=3,
        )
        cls.inactive_staff = User.objects.create_user(
            username="inactive-login", last_name="鈴木", first_name="太郎"
        )
        StoreMembership.objects.create(
            user=cls.inactive_staff, store=cls.store, role="staff", is_active=False
        )
        cls.new_staff = User.objects.create_user(
            username="new-login", last_name="山田", first_name="次郎"
        )
        cls.other_staff = User.objects.create_user(
            username="other-login", last_name="佐藤", first_name="美咲"
        )
        StoreMembership.objects.create(
            user=cls.other_staff, store=cls.other_store, role="staff"
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("manager_staff_list")

    def membership_data(self, user, **overrides):
        data = {
            "user": user.pk,
            "role": "staff",
            "rank": "B",
            "desired_shifts_per_week": 2,
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_page_shows_named_choices_and_all_store_memberships(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "店舗：本店")
        self.assertContains(response, reverse("manager_dashboard"))
        self.assertContains(response, "柴田 一翔")
        self.assertContains(response, "田中 花子")
        self.assertContains(response, "鈴木 太郎")
        self.assertContains(response, "有効")
        self.assertContains(response, "無効")
        self.assertNotContains(response, "staff-login")
        self.assertNotContains(response, "manager-login")
        self.assertEqual(
            set(response.context["memberships"].values_list("user_id", flat=True)),
            {self.manager.pk, self.staff.pk, self.inactive_staff.pk},
        )
        self.assertEqual(
            response.context["form"].fields["user"].label_from_instance(self.new_staff),
            "山田 次郎",
        )

    def test_add_membership_uses_managers_store_and_displays_success(self):
        response = self.client.post(
            self.url,
            self.membership_data(self.new_staff, store=self.other_store.pk),
            follow=True,
        )

        self.assertRedirects(response, self.url)
        membership = StoreMembership.objects.get(user=self.new_staff, store=self.store)
        self.assertEqual(membership.role, "staff")
        self.assertEqual(membership.rank, "B")
        self.assertEqual(membership.desired_shifts_per_week, 2)
        self.assertTrue(membership.is_active)
        self.assertFalse(
            StoreMembership.objects.filter(user=self.new_staff, store=self.other_store).exists()
        )
        self.assertContains(response, "従業員を店舗に追加しました。")

    def test_duplicate_membership_displays_error_instead_of_saving(self):
        response = self.client.post(self.url, self.membership_data(self.staff))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "user", "この従業員はすでに店舗に所属しています。"
        )
        self.assertContains(response, "この従業員はすでに店舗に所属しています。")
        self.assertEqual(
            StoreMembership.objects.filter(user=self.staff, store=self.store).count(), 1
        )
        self.staff_membership.refresh_from_db()
        self.assertEqual(self.staff_membership.rank, "A")

    def test_inactive_membership_is_also_protected_against_duplicates(self):
        response = self.client.post(self.url, self.membership_data(self.inactive_staff))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "user", "この従業員はすでに店舗に所属しています。"
        )
        self.assertEqual(
            StoreMembership.objects.filter(user=self.inactive_staff, store=self.store).count(), 1
        )

    def test_user_in_another_store_can_also_join_managers_store(self):
        response = self.client.post(self.url, self.membership_data(self.other_staff))

        self.assertRedirects(response, self.url)
        self.assertEqual(StoreMembership.objects.filter(user=self.other_staff).count(), 2)

    def test_manager_and_inactive_options_are_saved(self):
        data = self.membership_data(self.new_staff, role="manager", rank="A")
        data.pop("is_active")
        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url)
        membership = StoreMembership.objects.get(user=self.new_staff, store=self.store)
        self.assertEqual(membership.role, "manager")
        self.assertEqual(membership.rank, "A")
        self.assertFalse(membership.is_active)

    def test_staff_cannot_view_or_add_memberships(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)

        response = self.client.post(self.url, self.membership_data(self.new_staff))
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertFalse(StoreMembership.objects.filter(user=self.new_staff).exists())

    def test_inactive_manager_cannot_add_memberships(self):
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])

        response = self.client.post(self.url, self.membership_data(self.new_staff))

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertFalse(StoreMembership.objects.filter(user=self.new_staff).exists())

    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )


class ShiftManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = User.objects.create_user(username="manager")
        cls.manager_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.manager, role="manager"
        )
        cls.staff = User.objects.create_user(
            username="staff", last_name="田中", first_name="花子"
        )
        cls.membership = StoreMembership.objects.create(
            store=cls.store, user=cls.staff, role="staff"
        )
        cls.alternate_staff = User.objects.create_user(
            username="alternate", last_name="鈴木", first_name="太郎"
        )
        cls.alternate_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.alternate_staff, role="staff"
        )
        cls.inactive_staff = User.objects.create_user(username="inactive")
        cls.inactive_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.inactive_staff, role="staff", is_active=False
        )
        cls.disabled_user = User.objects.create_user(username="disabled", is_active=False)
        cls.disabled_membership = StoreMembership.objects.create(
            store=cls.store, user=cls.disabled_user, role="staff"
        )
        cls.foreign_staff = User.objects.create_user(username="foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            store=cls.other_store, user=cls.foreign_staff, role="staff"
        )
        cls.work_date = date(2026, 7, 12)
        cls.shift = Shift.objects.create(
            store=cls.store, user=cls.staff, membership=cls.membership,
            work_date=cls.work_date, start_time=time(9), end_time=time(18),
            is_generated=True,
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.edit_url = reverse("manager_shift_edit", args=[self.shift.pk])
        self.delete_url = reverse("manager_shift_delete", args=[self.shift.pk])
        self.calendar_url = self.calendar_for(self.work_date)

    def calendar_for(self, work_date):
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def edit_data(self, **overrides):
        data = {
            "membership": self.membership.pk,
            "work_date": self.work_date.isoformat(),
            "start_time": "09:00",
            "end_time": "18:00",
        }
        data.update(overrides)
        return data

    def test_calendar_links_to_edit_and_delete(self):
        response = self.client.get(self.calendar_url)

        self.assertContains(response, self.edit_url)
        self.assertContains(response, self.delete_url)

    def test_edit_get_does_not_change_shift_and_needs_no_availability(self):
        response = self.client.get(self.edit_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "manager/shift_form.html")
        self.assertContains(response, "田中 花子")
        self.assertContains(response, "鈴木 太郎")
        self.assertEqual(
            set(response.context["form"].fields["membership"].queryset),
            {self.membership, self.alternate_membership},
        )
        self.assertFalse(Availability.objects.exists())
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_generated)
        self.assertEqual(self.shift.start_time, time(9))

    def test_edit_saves_staff_and_date_consistently_as_manual(self):
        new_date = date(2026, 7, 13)
        response = self.client.post(
            self.edit_url,
            self.edit_data(
                membership=self.alternate_membership.pk,
                work_date=new_date.isoformat(), start_time="10:00", end_time="17:00",
                store=self.other_store.pk, user=self.foreign_staff.pk,
            ),
            follow=True,
        )

        self.assertRedirects(response, self.calendar_for(new_date))
        self.assertContains(response, "シフトを変更しました。")
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.store, self.store)
        self.assertEqual(self.shift.membership, self.alternate_membership)
        self.assertEqual(self.shift.user, self.alternate_staff)
        self.assertEqual(self.shift.work_date, new_date)
        self.assertEqual(self.shift.start_time, time(10))
        self.assertEqual(self.shift.end_time, time(17))
        self.assertFalse(self.shift.is_generated)
        self.assertEqual(Shift.objects.count(), 1)

    def test_unchanged_shift_can_be_saved_without_conflicting_with_itself(self):
        response = self.client.post(self.edit_url, self.edit_data())

        self.assertRedirects(response, self.calendar_url)
        self.shift.refresh_from_db()
        self.assertFalse(self.shift.is_generated)

    def test_invalid_time_ranges_do_not_change_saved_shift(self):
        for start, end in [("18:00", "09:00"), ("09:00", "09:00")]:
            with self.subTest(start=start, end=end):
                response = self.client.post(
                    self.edit_url, self.edit_data(start_time=start, end_time=end)
                )

                self.assertEqual(response.status_code, 200)
                self.assertFormError(
                    response.context["form"], "end_time",
                    "終了時刻は開始時刻より後にしてください。",
                )
                self.shift.refresh_from_db()
                self.assertEqual(self.shift.start_time, time(9))
                self.assertEqual(self.shift.end_time, time(18))
                self.assertTrue(self.shift.is_generated)

    def test_invalid_date_keeps_return_link_on_saved_date(self):
        response = self.client.post(
            self.edit_url, self.edit_data(work_date="2026-02-30")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("work_date"))
        self.assertEqual(response.context["calendar_url"], self.calendar_url)
        self.assertEqual(response.context["selected_date"], self.work_date)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.work_date, self.work_date)

    def test_only_active_staff_from_managers_store_can_be_assigned(self):
        for membership in [
            self.manager_membership, self.inactive_membership,
            self.disabled_membership, self.foreign_membership,
        ]:
            with self.subTest(membership=membership.pk):
                response = self.client.post(
                    self.edit_url, self.edit_data(membership=membership.pk)
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors.get("membership"))
                self.shift.refresh_from_db()
                self.assertEqual(self.shift.membership, self.membership)
                self.assertEqual(self.shift.user, self.staff)
                self.assertTrue(self.shift.is_generated)

    def test_overlapping_shift_for_same_user_in_another_store_is_rejected(self):
        other_membership = StoreMembership.objects.create(
            store=self.other_store, user=self.staff, role="staff"
        )
        Shift.objects.create(
            store=self.other_store, user=self.staff, membership=other_membership,
            work_date=self.work_date, start_time=time(17), end_time=time(20),
        )

        response = self.client.post(self.edit_url, self.edit_data())

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], None,
            "この従業員には、同じ時間帯に重なるシフトが登録されています。",
        )
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_generated)

    def test_adjacent_shifts_are_allowed(self):
        Shift.objects.create(
            store=self.store, user=self.staff, membership=self.membership,
            work_date=self.work_date, start_time=time(18), end_time=time(20),
        )

        response = self.client.post(self.edit_url, self.edit_data())

        self.assertRedirects(response, self.calendar_url)

    def test_manual_addition_uses_same_overlap_and_time_validation(self):
        Availability.objects.create(
            user=self.staff, membership=self.membership, work_date=self.work_date,
            start_time=time(8), end_time=time(20),
        )
        response = self.client.post(self.calendar_url, self.edit_data())
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], None,
            "この従業員には、同じ時間帯に重なるシフトが登録されています。",
        )
        response = self.client.post(
            self.calendar_url, self.edit_data(start_time="20:00", end_time="19:00")
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("end_time"))
        self.assertEqual(Shift.objects.count(), 1)

    def test_legacy_shift_without_membership_can_be_edited(self):
        self.shift.membership = None
        self.shift.save(update_fields=["membership"])
        response = self.client.get(self.edit_url)
        self.assertEqual(response.context["form"].initial["membership"], self.membership)

        response = self.client.post(self.edit_url, self.edit_data())
        self.assertRedirects(response, self.calendar_url)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.membership, self.membership)

    def test_delete_get_only_confirms_and_post_deletes(self):
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "manager/shift_confirm_delete.html")
        self.assertContains(response, "田中 花子")
        self.assertContains(response, "09:00〜18:00")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(Shift.objects.filter(pk=self.shift.pk).exists())

        response = self.client.post(self.delete_url, follow=True)
        self.assertRedirects(response, self.calendar_url)
        self.assertContains(response, "シフトを削除しました。")
        self.assertFalse(Shift.objects.filter(pk=self.shift.pk).exists())

    def test_delete_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        response = client.post(self.delete_url)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Shift.objects.filter(pk=self.shift.pk).exists())

        client.get(self.delete_url)
        response = client.post(
            self.delete_url, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
        )
        self.assertRedirects(response, self.calendar_url)
        self.assertFalse(Shift.objects.filter(pk=self.shift.pk).exists())

    def test_edit_and_delete_of_other_store_shift_return_404(self):
        foreign_shift = Shift.objects.create(
            store=self.other_store, user=self.foreign_staff,
            membership=self.foreign_membership, work_date=self.work_date,
            start_time=time(9), end_time=time(18),
        )
        for name in ["manager_shift_edit", "manager_shift_delete"]:
            url = reverse(name, args=[foreign_shift.pk])
            for method in ["get", "post"]:
                with self.subTest(view=name, method=method):
                    response = getattr(self.client, method)(url, self.edit_data())
                    self.assertEqual(response.status_code, 404)
        self.assertTrue(Shift.objects.filter(pk=foreign_shift.pk).exists())

    def test_staff_and_inactive_manager_cannot_edit_or_delete(self):
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        for user in [self.staff, self.manager]:
            self.client.force_login(user)
            for url in [self.edit_url, self.delete_url]:
                for method in ["get", "post"]:
                    with self.subTest(user=user.pk, url=url, method=method):
                        response = getattr(self.client, method)(url, self.edit_data())
                        self.assertRedirects(
                            response, reverse("manager_dashboard"),
                            fetch_redirect_response=False,
                        )
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_generated)
        self.assertEqual(self.shift.start_time, time(9))

    def test_anonymous_user_and_unsupported_methods_do_not_change_shifts(self):
        for url in [self.edit_url, self.delete_url]:
            response = self.client.put(url, {})
            self.assertEqual(response.status_code, 405)
        self.client.logout()
        for url in [self.edit_url, self.delete_url]:
            response = self.client.post(url, self.edit_data())
            self.assertRedirects(
                response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
            )
        self.assertTrue(Shift.objects.filter(pk=self.shift.pk).exists())

    def test_invalid_calendar_parameters_still_render(self):
        response = self.client.get(
            reverse("manager_shift_list"),
            {"year": "invalid", "month": "13", "date": "2026-02-30"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(1 <= response.context["month"] <= 12)
