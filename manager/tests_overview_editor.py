"""Manager overview creation/editing, validation and store isolation."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift, ShiftSubmission


class OverviewEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        User = get_user_model()
        cls.manager = User.objects.create_user(username="overview_editor_manager")
        cls.manager_member = StoreMembership.objects.create(
            store=cls.store, user=cls.manager, role="manager",
        )
        cls.staff = User.objects.create_user(username="overview_editor_staff")
        cls.member = StoreMembership.objects.create(
            store=cls.store, user=cls.staff, role="staff",
        )
        cls.other_staff = User.objects.create_user(username="overview_editor_other")
        cls.other_member = StoreMembership.objects.create(
            store=cls.store, user=cls.other_staff, role="staff",
        )
        cls.foreign_staff = User.objects.create_user(username="overview_editor_foreign")
        cls.foreign_member = StoreMembership.objects.create(
            store=cls.other_store, user=cls.foreign_staff, role="staff",
        )
        cls.url = reverse("manager_shift_overview") + "?year=2026&month=10"

    def setUp(self):
        self.client.force_login(self.manager)
        self.data = {
            "membership": self.member.pk,
            "work_date": "2026-10-02",
            "start_time": "09:00",
            "end_time": "17:00",
            "display_color": "blue",
            "note": "開店準備\nレジ担当",
        }

    def make_shift(self, *, membership=None, **overrides):
        membership = membership or self.member
        values = {
            "membership": membership, "user": membership.user,
            "store": membership.store, "work_date": date(2026, 10, 2),
            "start_time": time(9), "end_time": time(17),
            "is_generated": True, "display_color": "purple", "note": "元のメモ",
        }
        values.update(overrides)
        return Shift.objects.create(**values)

    def snapshot(self):
        return list(Shift.objects.order_by("pk").values())

    def confirm_and_post(self, data):
        response = self.client.post(self.url, {**data, "confirm_outside": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["overview_show_confirmation"])
        self.assertTrue(response.context["overview_confirmation_message"])
        return self.client.post(self.url, {
            **data, "confirm_outside": "on",
            "outside_confirmation_token": response.context["overview_form"]["outside_confirmation_token"].value(),
        })

    def test_outside_requires_confirmation_then_creates_manual_shift(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["overview_form_open"])
        self.assertIn("confirm_outside", response.context["overview_form"].errors)
        self.assertFalse(Shift.objects.exists())

        response = self.client.post(self.url, {
            **self.data, "confirm_outside": "on",
            "outside_confirmation_token": response.context["overview_form"]["outside_confirmation_token"].value(),
        })
        self.assertRedirects(response, self.url)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, self.member)
        self.assertEqual(shift.user, self.staff)
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.note, self.data["note"])
        self.assertEqual(shift.display_color, "blue")
        self.assertFalse(shift.is_generated)

    def test_matching_submitted_availability_saves_without_confirmation(self):
        submission = ShiftSubmission.objects.create(
            membership=self.member, target_month=date(2026, 10, 1),
            submission_mode="availability", is_submitted=True,
        )
        Availability.objects.create(
            user=self.staff, membership=self.member, submission=submission,
            work_date=date(2026, 10, 2), start_time=time(9), end_time=time(17),
        )
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, self.url)
        self.assertEqual(Shift.objects.get().membership, self.member)

    def test_unsubmitted_availability_does_not_bypass_confirmation(self):
        submission = ShiftSubmission.objects.create(
            membership=self.member, target_month=date(2026, 10, 1),
            submission_mode="availability", is_submitted=False,
        )
        Availability.objects.create(
            user=self.staff, membership=self.member, submission=submission,
            work_date=date(2026, 10, 2), start_time=time(9), end_time=time(17),
        )
        response = self.client.post(self.url, self.data)
        self.assertIn("confirm_outside", response.context["overview_form"].errors)
        self.assertFalse(Shift.objects.exists())

    def test_edit_all_fields_and_reassign_employee_returns_displayed_month(self):
        shift = self.make_shift()
        response = self.confirm_and_post({
            **self.data, "shift_id": shift.pk, "membership": self.other_member.pk,
            "work_date": "2026-11-05", "start_time": "10:30", "end_time": "18:45",
            "display_color": "green", "note": "変更後のメモ", "confirm_outside": "on",
        })
        self.assertRedirects(response, self.url)
        shift.refresh_from_db()
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(shift.membership, self.other_member)
        self.assertEqual(shift.user, self.other_staff)
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.work_date, date(2026, 11, 5))
        self.assertEqual((shift.start_time, shift.end_time), (time(10, 30), time(18, 45)))
        self.assertEqual(shift.display_color, "green")
        self.assertEqual(shift.note, "変更後のメモ")
        self.assertFalse(shift.is_generated)

    def test_edit_unchanged_times_excludes_own_shift_from_overlap(self):
        shift = self.make_shift()
        response = self.confirm_and_post({
            **self.data, "shift_id": shift.pk, "confirm_outside": "on",
        })
        self.assertRedirects(response, self.url)
        self.assertEqual(Shift.objects.count(), 1)

    def test_can_add_nonoverlapping_shift_to_occupied_day(self):
        self.make_shift(start_time=time(9), end_time=time(12))
        response = self.confirm_and_post({
            **self.data, "start_time": "12:00", "end_time": "17:00",
            "confirm_outside": "on",
        })
        self.assertRedirects(response, self.url)
        self.assertEqual(Shift.objects.count(), 2)

    def test_overlapping_create_does_not_change_database(self):
        self.make_shift()
        before = self.snapshot()
        response = self.client.post(self.url, {**self.data, "confirm_outside": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("__all__", response.context["overview_form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_overlapping_edit_does_not_change_database(self):
        shift = self.make_shift(start_time=time(9), end_time=time(12))
        self.make_shift(start_time=time(13), end_time=time(17))
        before = self.snapshot()
        response = self.client.post(self.url, {
            **self.data, "shift_id": shift.pk, "end_time": "14:00",
            "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("__all__", response.context["overview_form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_overlap_in_another_store_is_rejected(self):
        other_membership = StoreMembership.objects.create(
            store=self.other_store, user=self.staff, role="staff",
        )
        self.make_shift(membership=other_membership)
        before = self.snapshot()
        response = self.client.post(self.url, {**self.data, "confirm_outside": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("__all__", response.context["overview_form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_foreign_membership_is_rejected(self):
        response = self.client.post(self.url, {
            **self.data, "membership": self.foreign_member.pk, "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("membership", response.context["overview_form"].errors)
        self.assertFalse(Shift.objects.exists())

    def test_foreign_shift_cannot_be_changed(self):
        shift = self.make_shift(membership=self.foreign_member)
        before = self.snapshot()
        response = self.client.post(self.url, {
            **self.data, "shift_id": shift.pk, "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_shift_ids_return_controlled_error(self):
        for shift_id in ("abc", "-1", "0", "9223372036854775808"):
            with self.subTest(shift_id=shift_id):
                response = self.client.post(self.url, {
                    **self.data, "shift_id": shift_id, "confirm_outside": "on",
                })
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Shift.objects.exists())

    def test_missing_shift_returns_not_found(self):
        response = self.client.post(self.url, {
            **self.data, "shift_id": "999999", "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Shift.objects.exists())

    def test_unsupported_dates_and_invalid_time_order_do_not_save(self):
        for changes, error in (
            ({"work_date": "0001-01-01"}, "work_date"),
            ({"work_date": "9999-12-31"}, "work_date"),
            ({"work_date": "2026-02-30"}, "work_date"),
            ({"end_time": "09:00"}, "end_time"),
            ({"end_time": "08:00"}, "end_time"),
        ):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, {
                    **self.data, **changes, "confirm_outside": "on",
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn(error, response.context["overview_form"].errors)
        self.assertFalse(Shift.objects.exists())

    def test_validation_errors_keep_fields_confirmation_and_edit_id(self):
        shift = self.make_shift()
        before = self.snapshot()
        data = {
            **self.data, "shift_id": str(shift.pk), "membership": str(self.other_member.pk),
            "work_date": "2026-11-05", "start_time": "16:30", "end_time": "10:15",
            "display_color": "orange", "note": "エラー時も保持\nするメモ",
            "confirm_outside": "on",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        form = response.context["overview_form"]
        for name in ("membership", "work_date", "start_time", "end_time", "display_color", "note"):
            self.assertEqual(form[name].value(), data[name], name)
        self.assertTrue(form["confirm_outside"].value())
        self.assertTrue(response.context["overview_form_open"])
        self.assertEqual(response.context["overview_shift_id"], str(shift.pk))
        self.assertEqual(self.snapshot(), before)

    def test_staff_and_anonymous_users_cannot_save(self):
        self.make_shift()
        before = self.snapshot()
        for user in (self.staff, None):
            with self.subTest(user=user):
                if user:
                    self.client.force_login(user)
                else:
                    self.client.logout()
                response = self.client.post(self.url, {**self.data, "confirm_outside": "on"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.snapshot(), before)

    def test_inactive_manager_cannot_save(self):
        self.manager_member.is_active = False
        self.manager_member.save(update_fields=["is_active"])
        response = self.client.post(self.url, {**self.data, "confirm_outside": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Shift.objects.exists())

    def test_can_edit_original_inactive_membership_but_cannot_create_for_it(self):
        shift = self.make_shift()
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        response = self.confirm_and_post({
            **self.data, "shift_id": shift.pk, "note": "無効化後も修正可能", "confirm_outside": "on",
        })
        self.assertRedirects(response, self.url)
        shift.refresh_from_db()
        self.assertEqual(shift.note, "無効化後も修正可能")
        before = self.snapshot()
        response = self.client.post(self.url, {
            **self.data, "work_date": "2026-10-03", "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("membership", response.context["overview_form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_cannot_reassign_shift_to_another_inactive_membership(self):
        shift = self.make_shift()
        self.other_member.is_active = False
        self.other_member.save(update_fields=["is_active"])
        before = self.snapshot()
        response = self.client.post(self.url, {
            **self.data, "shift_id": shift.pk, "membership": self.other_member.pk,
            "confirm_outside": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("membership", response.context["overview_form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_normal_editing_does_not_request_outside_confirmation(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context["overview_show_confirmation"])
        self.assertEqual(response.context["overview_confirmation_message"], "")
        self.assertEqual(response.context["overview_form"]["outside_confirmation_token"].value(), None)

    def test_checkbox_alone_does_not_approve_outside_registration(self):
        response = self.client.post(self.url, {**self.data, "confirm_outside": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["overview_show_confirmation"])
        self.assertFalse(response.context["overview_form"]["confirm_outside"].value())
        self.assertTrue(response.context["overview_form"]["outside_confirmation_token"].value())
        self.assertFalse(Shift.objects.exists())

    def test_confirmation_is_bound_to_employee_date_and_time(self):
        response = self.client.post(self.url, self.data)
        token = response.context["overview_form"]["outside_confirmation_token"].value()
        for changes in (
            {"membership": self.other_member.pk},
            {"work_date": "2026-10-03"},
            {"start_time": "08:00"},
            {"end_time": "18:00"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, {
                    **self.data, **changes, "confirm_outside": "on", "outside_confirmation_token": token,
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["overview_show_confirmation"])
                self.assertFalse(response.context["overview_form"]["confirm_outside"].value())
                self.assertFalse(Shift.objects.exists())

    def test_confirmation_does_not_override_ordinary_validation(self):
        response = self.client.post(self.url, self.data)
        token = response.context["overview_form"]["outside_confirmation_token"].value()
        self.make_shift()
        before = self.snapshot()
        response = self.client.post(self.url, {
            **self.data, "confirm_outside": "on", "outside_confirmation_token": token,
        })
        self.assertIn("__all__", response.context["overview_form"].errors)
        self.assertFalse(response.context["overview_show_confirmation"])
        self.assertEqual(self.snapshot(), before)

    def test_outside_confirmation_is_not_shown_for_invalid_fields(self):
        for changes in ({"end_time": "08:00"}, {"note": "a" * 501}, {"membership": self.foreign_member.pk}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, {**self.data, **changes})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["overview_show_confirmation"])
                self.assertFalse(Shift.objects.exists())

    def test_confirmation_token_cannot_be_forged_or_reused_for_edit(self):
        response = self.client.post(self.url, self.data)
        token = response.context["overview_form"]["outside_confirmation_token"].value()
        shift = self.make_shift(start_time=time(18), end_time=time(20))
        for changes in (
            {"outside_confirmation_token": "invalid-token"},
            {"shift_id": shift.pk, "outside_confirmation_token": token},
        ):
            with self.subTest(changes=changes):
                before = self.snapshot()
                response = self.client.post(self.url, {**self.data, **changes, "confirm_outside": "on"})
                self.assertTrue(response.context["overview_show_confirmation"])
                self.assertEqual(self.snapshot(), before)
