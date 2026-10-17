from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift, ShiftSubmission
from shifts.testing import submitted_availability


class ShiftCreationModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="追加モードのテスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(
            username="creation_manager", last_name="山田", first_name="店長"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        second_manager = user_model.objects.create_user(
            username="creation_second_manager", last_name="佐藤", first_name="店長"
        )
        cls.second_manager_membership = StoreMembership.objects.create(
            user=second_manager, store=cls.store, role="manager"
        )
        inactive_manager = user_model.objects.create_user(username="creation_inactive_manager")
        cls.inactive_manager_membership = StoreMembership.objects.create(
            user=inactive_manager, store=cls.store, role="manager", is_active=False
        )
        disabled_manager = user_model.objects.create_user(
            username="creation_disabled_manager", is_active=False
        )
        cls.disabled_manager_membership = StoreMembership.objects.create(
            user=disabled_manager, store=cls.store, role="manager"
        )
        foreign_manager = user_model.objects.create_user(username="creation_foreign_manager")
        cls.foreign_manager_membership = StoreMembership.objects.create(
            user=foreign_manager, store=cls.other_store, role="manager"
        )
        cls.available_staff = user_model.objects.create_user(
            username="creation_available", last_name="田中", first_name="花子"
        )
        cls.available_membership = StoreMembership.objects.create(
            user=cls.available_staff, store=cls.store, role="staff"
        )
        cls.staff_without_availability = user_model.objects.create_user(
            username="creation_no_availability", last_name="鈴木", first_name="太郎"
        )
        cls.no_availability_membership = StoreMembership.objects.create(
            user=cls.staff_without_availability, store=cls.store, role="staff"
        )
        inactive_staff = user_model.objects.create_user(username="creation_inactive")
        cls.inactive_membership = StoreMembership.objects.create(
            user=inactive_staff, store=cls.store, role="staff", is_active=False
        )
        disabled_staff = user_model.objects.create_user(
            username="creation_disabled", is_active=False
        )
        cls.disabled_membership = StoreMembership.objects.create(
            user=disabled_staff, store=cls.store, role="staff"
        )
        foreign_staff = user_model.objects.create_user(username="creation_foreign")
        cls.foreign_membership = StoreMembership.objects.create(
            user=foreign_staff, store=cls.other_store, role="staff"
        )
        cls.other_store_membership = StoreMembership.objects.create(
            user=cls.available_staff, store=cls.other_store, role="staff"
        )
        cls.work_date = date(2026, 11, 12)
        for membership in [
            cls.available_membership, cls.inactive_membership,
            cls.disabled_membership, cls.foreign_membership,
        ]:
            submitted_availability(
                user=membership.user, membership=membership,
                work_date=cls.work_date, start_time=time(9), end_time=time(17),
            )
        # The same employee's wider wish in another store must not grant permission here.
        submitted_availability(
            user=cls.available_staff, membership=cls.other_store_membership,
            work_date=cls.work_date, start_time=time(8), end_time=time(20),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.calendar_url = self.calendar_for(self.work_date)

    def calendar_for(self, work_date):
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def wish_data(self, **overrides):
        data = {
            "shift_mode": "availability",
            "membership": self.available_membership.pk,
            "work_date": self.work_date.isoformat(),
            "start_time": "09:00",
            "end_time": "17:00",
            "note": "希望通りの追加",
            "display_color": "green",
        }
        data.update(overrides)
        return data

    def submit_all_day(self, membership):
        return ShiftSubmission.objects.create(
            membership=membership, target_month=self.work_date.replace(day=1),
            submission_mode="day_off", is_submitted=True, submitted_at=timezone.now(),
        )

    def outside_data(self, **overrides):
        fields = {
            "membership": self.available_membership.pk,
            "work_date": self.work_date.isoformat(),
            "start_time": "08:00",
            "end_time": "18:00",
            "note": "ヘルプを依頼済み",
            "display_color": "orange",
            "confirm_outside_availability": "True",
        }
        fields.update(overrides)
        return {"shift_mode": "outside", **{f"outside-{key}": value for key, value in fields.items()}}

    def assert_creation_rejected(self, response, form_name, field=None):
        self.assertEqual(response.status_code, 200)
        form = response.context[form_name]
        self.assertTrue(form.is_bound)
        if field is None:
            self.assertTrue(form.errors)
        else:
            self.assertTrue(form.errors.get(field), form.errors)
        self.assertFalse(Shift.objects.exists())

    def test_creation_modes_show_wish_candidates_and_all_active_store_members(self):
        response = self.client.get(self.calendar_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "希望通りのシフトを追加")
        self.assertContains(response, "希望外のシフトを追加")
        wish_form = response.context["form"]
        outside_form = response.context["outside_form"]
        self.assertEqual(
            set(wish_form.fields["membership"].queryset),
            {self.available_membership},
        )
        self.assertEqual(
            set(outside_form.fields["membership"].queryset),
            {
                self.available_membership, self.no_availability_membership,
                self.manager_membership, self.second_manager_membership,
            },
        )
        self.assertEqual(
            outside_form.fields["membership"].label_from_instance(self.manager_membership),
            "山田 店長",
        )
        self.assertEqual(wish_form["work_date"].value(), self.work_date)
        self.assertEqual(outside_form["work_date"].value(), self.work_date)
        self.assertFalse(wish_form.is_bound)
        self.assertFalse(outside_form.is_bound)

    def test_only_wish_candidates_are_empty_when_nobody_is_available_on_the_day(self):
        response = self.client.get(self.calendar_for(self.work_date + timedelta(days=1)))

        self.assertEqual(list(response.context["form"].fields["membership"].queryset), [])
        self.assertEqual(
            set(response.context["outside_form"].fields["membership"].queryset),
            {
                self.available_membership, self.no_availability_membership,
                self.manager_membership, self.second_manager_membership,
            },
        )

    def test_exact_and_shorter_wish_shifts_are_saved_with_consistent_staff_details(self):
        for start_time, end_time in [("09:00", "17:00"), ("10:00", "16:00")]:
            with self.subTest(start_time=start_time, end_time=end_time):
                response = self.client.post(self.calendar_url, self.wish_data(
                    start_time=start_time, end_time=end_time,
                    user=self.foreign_membership.user_id, store=self.other_store.pk,
                    is_generated="on",
                ))

                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                shift = Shift.objects.get()
                self.assertEqual(shift.membership, self.available_membership)
                self.assertEqual(shift.user, self.available_staff)
                self.assertEqual(shift.store, self.store)
                self.assertEqual(shift.note, "希望通りの追加")
                self.assertEqual(shift.display_color, "green")
                self.assertFalse(shift.is_generated)
                shift.delete()

    def test_wish_shift_rejects_start_or_end_outside_this_stores_wish(self):
        for start_time, end_time in [("08:59", "17:00"), ("09:00", "17:01")]:
            with self.subTest(start_time=start_time, end_time=end_time):
                response = self.client.post(self.calendar_url, self.wish_data(
                    start_time=start_time, end_time=end_time,
                ))

                self.assert_creation_rejected(response, "form")
                self.assertFalse(response.context["outside_form"].is_bound)
                self.assertEqual(response.context["form"]["note"].value(), "希望通りの追加")

    def test_wish_shift_rejects_an_unavailable_gap_between_separate_wishes(self):
        Availability.objects.filter(membership=self.available_membership).delete()
        for start_time, end_time in [(time(9), time(12)), (time(13), time(17))]:
            submitted_availability(
                user=self.available_staff, membership=self.available_membership,
                work_date=self.work_date, start_time=start_time, end_time=end_time,
            )

        response = self.client.post(self.calendar_url, self.wish_data())

        self.assert_creation_rejected(response, "form")

    def test_wish_shift_accepts_continuous_coverage_from_adjacent_wishes(self):
        Availability.objects.filter(membership=self.available_membership).delete()
        for start_time, end_time in [(time(9), time(12)), (time(12), time(17))]:
            submitted_availability(
                user=self.available_staff, membership=self.available_membership,
                work_date=self.work_date, start_time=start_time, end_time=end_time,
            )

        response = self.client.post(self.calendar_url, self.wish_data())

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(Shift.objects.count(), 1)

    def test_wish_shift_rejects_staff_without_a_wish_for_the_selected_day(self):
        submitted_availability(
            user=self.staff_without_availability, membership=self.no_availability_membership,
            work_date=self.work_date + timedelta(days=1),
            start_time=time(9), end_time=time(17),
        )

        response = self.client.post(self.calendar_url, self.wish_data(
            membership=self.no_availability_membership.pk,
        ))

        self.assert_creation_rejected(response, "form", "membership")

    def test_confirmed_outside_shift_accepts_unsubmitted_staff_and_fixes_owner_server_side(self):
        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.no_availability_membership.pk,
            user=self.foreign_membership.user_id, store=self.other_store.pk,
            is_generated="on",
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, self.no_availability_membership)
        self.assertEqual(shift.user, self.staff_without_availability)
        self.assertEqual(shift.store, self.store)
        self.assertEqual((shift.start_time, shift.end_time), (time(8), time(18)))
        self.assertEqual(shift.note, "ヘルプを依頼済み")
        self.assertEqual(shift.display_color, "orange")
        self.assertFalse(shift.is_generated)
        self.assertFalse(ShiftSubmission.objects.filter(membership=self.no_availability_membership).exists())
        self.assertFalse(Availability.objects.filter(membership=self.no_availability_membership).exists())

    def test_confirmed_outside_shift_can_extend_beyond_an_existing_wish(self):
        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.available_membership.pk,
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, self.available_membership)
        self.assertEqual(shift.start_time, time(8))
        self.assertEqual(shift.end_time, time(18))

    def test_confirmed_outside_shift_accepts_unsubmitted_manager_and_remains_editable(self):
        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.manager_membership.pk,
            user=self.foreign_membership.user_id, store=self.other_store.pk,
            is_generated="on",
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, self.manager_membership)
        self.assertEqual(shift.user, self.manager)
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.start_time, time(8))
        self.assertEqual(shift.end_time, time(18))
        self.assertEqual(shift.note, "ヘルプを依頼済み")
        self.assertEqual(shift.display_color, "orange")
        self.assertFalse(shift.is_generated)
        self.assertFalse(Availability.objects.filter(user=self.manager).exists())
        self.assertFalse(ShiftSubmission.objects.filter(membership=self.manager_membership).exists())

        edit_url = reverse("manager_shift_edit", args=[shift.pk])
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.manager_membership, response.context["form"].fields["membership"].queryset
        )
        self.assertEqual(response.context["form"]["membership"].value(), self.manager_membership.pk)

        response = self.client.post(edit_url, {
            "membership": self.manager_membership.pk,
            "work_date": self.work_date.isoformat(),
            "start_time": "10:00", "end_time": "19:00",
            "note": "店長の時間変更", "display_color": "green",
        })
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift.refresh_from_db()
        self.assertEqual(shift.membership, self.manager_membership)
        self.assertEqual(shift.user, self.manager)
        self.assertEqual(shift.start_time, time(10))
        self.assertEqual(shift.end_time, time(19))
        self.assertEqual(shift.note, "店長の時間変更")
        self.assertFalse(shift.is_generated)

    def test_outside_shift_can_assign_another_unsubmitted_manager_of_the_same_store(self):
        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.second_manager_membership.pk,
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, self.second_manager_membership)
        self.assertEqual(shift.user, self.second_manager_membership.user)
        self.assertEqual(shift.store, self.store)
        self.assertFalse(shift.is_generated)

    def test_manager_outside_shift_still_requires_confirmation(self):
        submitted_availability(
            user=self.manager, membership=self.manager_membership, work_date=self.work_date,
            start_time=time(9), end_time=time(17),
        )
        data = self.outside_data(membership=self.manager_membership.pk)
        data.pop("outside-confirm_outside_availability")

        response = self.client.post(self.calendar_url, data)

        self.assert_creation_rejected(
            response, "outside_form"
        )

    def test_manager_with_a_submitted_wish_is_included_in_the_wish_based_form(self):
        submitted_availability(
            user=self.manager, membership=self.manager_membership,
            work_date=self.work_date, start_time=time(9), end_time=time(17),
        )

        response = self.client.get(self.calendar_url)
        self.assertIn(
            self.manager_membership, response.context["form"].fields["membership"].queryset
        )
        response = self.client.post(self.calendar_url, self.wish_data(
            membership=self.manager_membership.pk,
        ))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(Shift.objects.get().membership, self.manager_membership)

    def test_outside_manager_shift_rejects_overlap_in_another_store(self):
        self.submit_all_day(self.manager_membership)
        other_membership = StoreMembership.objects.create(
            user=self.manager, store=self.other_store, role="manager"
        )
        existing = Shift.objects.create(
            user=self.manager, membership=other_membership, store=self.other_store,
            work_date=self.work_date, start_time=time(10), end_time=time(12),
        )

        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.manager_membership.pk,
        ))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["outside_form"].non_field_errors())
        self.assertEqual(list(Shift.objects.values_list("pk", flat=True)), [existing.pk])

    def test_outside_shift_requires_affirmative_confirmation(self):
        for confirmation in [None, "", "False"]:
            with self.subTest(confirmation=confirmation):
                data = self.outside_data(confirm_outside_availability=confirmation)
                if confirmation is None:
                    data.pop("outside-confirm_outside_availability")
                response = self.client.post(self.calendar_url, data)

                self.assert_creation_rejected(
                    response, "outside_form", "confirm_outside_availability"
                )
                self.assertFalse(response.context["form"].is_bound)
                self.assertEqual(
                    response.context["outside_form"]["note"].value(), "ヘルプを依頼済み"
                )

    def test_outside_shift_requires_confirmation_even_within_a_submitted_wish(self):
        response = self.client.post(self.calendar_url, self.outside_data(
            start_time="09:00", end_time="17:00", confirm_outside_availability="False",
        ))

        self.assert_creation_rejected(response, "outside_form", "confirm_outside_availability")

    def test_outside_shift_requires_confirmation_for_an_all_day_candidate(self):
        self.submit_all_day(self.no_availability_membership)
        response = self.client.post(self.calendar_url, self.outside_data(
            membership=self.no_availability_membership.pk, confirm_outside_availability="False",
        ))

        self.assert_creation_rejected(response, "outside_form", "confirm_outside_availability")

    def test_both_modes_reject_other_store_and_inactive_staff_or_manager_memberships(self):
        for data_builder, form_name in [
            (self.wish_data, "form"), (self.outside_data, "outside_form"),
        ]:
            for membership in [
                self.foreign_membership, self.inactive_membership,
                self.disabled_membership, self.foreign_manager_membership,
                self.inactive_manager_membership, self.disabled_manager_membership,
            ]:
                with self.subTest(form_name=form_name, membership=membership.pk):
                    response = self.client.post(
                        self.calendar_url, data_builder(membership=membership.pk)
                    )

                    self.assert_creation_rejected(response, form_name, "membership")

    def test_both_modes_reject_a_posted_date_different_from_the_selected_day(self):
        other_date = self.work_date + timedelta(days=1)
        submitted_availability(
            user=self.available_staff, membership=self.available_membership,
            work_date=other_date, start_time=time(9), end_time=time(17),
        )
        for data_builder, form_name in [
            (self.wish_data, "form"), (self.outside_data, "outside_form"),
        ]:
            with self.subTest(form_name=form_name):
                response = self.client.post(
                    self.calendar_url, data_builder(work_date=other_date.isoformat())
                )

                self.assert_creation_rejected(response, form_name, "work_date")

    def test_both_modes_keep_end_after_start_validation(self):
        for data_builder, form_name in [
            (self.wish_data, "form"), (self.outside_data, "outside_form"),
        ]:
            for start_time, end_time in [("10:00", "09:00"), ("09:00", "09:00")]:
                with self.subTest(form_name=form_name, start_time=start_time, end_time=end_time):
                    response = self.client.post(self.calendar_url, data_builder(
                        start_time=start_time, end_time=end_time,
                    ))

                    self.assert_creation_rejected(response, form_name, "end_time")

    def test_both_modes_reject_overlap_with_the_same_employee_in_another_store(self):
        existing_shift = Shift.objects.create(
            user=self.available_staff, membership=self.other_store_membership,
            store=self.other_store, work_date=self.work_date,
            start_time=time(10), end_time=time(12),
        )
        for data_builder, form_name in [
            (self.wish_data, "form"), (self.outside_data, "outside_form"),
        ]:
            with self.subTest(form_name=form_name):
                response = self.client.post(self.calendar_url, data_builder(
                    membership=self.available_membership.pk,
                ))

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context[form_name].non_field_errors())
                self.assertEqual(list(Shift.objects.values_list("pk", flat=True)), [existing_shift.pk])

    def test_outside_shift_allows_adjacency_to_an_existing_shift(self):
        Shift.objects.create(
            user=self.available_staff, membership=self.available_membership,
            store=self.store, work_date=self.work_date,
            start_time=time(7), end_time=time(8),
        )

        response = self.client.post(self.calendar_url, self.outside_data())

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(Shift.objects.count(), 2)

    def test_legacy_post_without_mode_still_adds_a_shift_within_the_wish(self):
        data = self.wish_data()
        data.pop("shift_mode")

        response = self.client.post(self.calendar_url, data)

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(Shift.objects.count(), 1)

    def test_unknown_creation_mode_never_saves_a_shift(self):
        response = self.client.post(self.calendar_url, self.wish_data(shift_mode="unexpected"))

        self.assertIn(response.status_code, [200, 400])
        self.assertFalse(Shift.objects.exists())

    def test_staff_cannot_create_a_shift_using_either_mode(self):
        self.client.force_login(self.available_staff)
        for data_builder in [self.wish_data, self.outside_data]:
            with self.subTest(data_builder=data_builder.__name__):
                response = self.client.post(self.calendar_url, data_builder())

                self.assertRedirects(
                    response, reverse("manager_dashboard"), fetch_redirect_response=False
                )
                self.assertFalse(Shift.objects.exists())
