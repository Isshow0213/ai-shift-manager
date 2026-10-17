from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, DayOffRequest, Shift, ShiftSubmission, StoreOperatingHours


class ManagerSubmissionModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="提出方式の管理会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="submission_manager")
        cls.manager_membership = StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.work_date = date(2030, 11, 12)
        cls.month = cls.work_date.replace(day=1)

    def setUp(self):
        self.client.force_login(self.manager)
        self.calendar_url = (f"{reverse('manager_shift_list')}?year=2030&month=11"
                             "&date=2030-11-12")

    def member(self, username, *, mode="availability", store=None, role="staff", active=True):
        return StoreMembership.objects.create(
            user=get_user_model().objects.create_user(username=username),
            store=store or self.store, role=role, is_active=active, submission_mode=mode,
        )

    def submission(self, membership, *, submitted=True):
        return ShiftSubmission.objects.create(
            membership=membership, target_month=self.month, submission_mode=membership.submission_mode,
            is_submitted=submitted, submitted_at=timezone.now() if submitted else None,
        )

    def availability(self, membership, submission, *, start=time(17), end=time(22)):
        return Availability.objects.create(
            user=membership.user, membership=membership, store=membership.store,
            submission=submission, work_date=self.work_date, start_time=start, end_time=end,
        )

    def data(self, membership, *, mode="availability", start="17:00", end="22:00", confirm=False):
        values = {
            "membership": membership.pk, "work_date": self.work_date.isoformat(),
            "start_time": start, "end_time": end, "note": "本人に確認済み", "display_color": "blue",
        }
        if confirm:
            values["confirm_outside_availability"] = "on"
        if mode == "outside":
            values = {f"outside-{key}": value for key, value in values.items()}
        return {"shift_mode": mode, **values}

    def test_only_wish_form_excludes_unsubmitted_day_off_and_unavailable_people(self):
        unsubmitted = self.member("manager_unsubmitted")
        self.availability(unsubmitted, None)
        empty_attendance = self.member("manager_empty_attendance")
        self.submission(empty_attendance)
        draft = self.member("manager_draft", mode="day_off")
        self.submission(draft, submitted=False)
        day_off = self.member("manager_day_off", mode="day_off")
        DayOffRequest.objects.create(submission=self.submission(day_off), work_date=self.work_date)
        limited = self.member("manager_limited", mode="day_off")
        self.availability(limited, self.submission(limited))
        all_day = self.member("manager_all_day", mode="day_off")
        self.submission(all_day)
        attendance = self.member("manager_attendance")
        self.availability(attendance, self.submission(attendance))
        foreign = self.member("manager_foreign", mode="day_off", store=self.other_store)
        self.submission(foreign)
        inactive = self.member("manager_inactive", mode="day_off", active=False)
        self.submission(inactive)

        response = self.client.get(self.calendar_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.context["form"].fields["membership"].queryset),
            {limited, all_day, attendance},
        )
        self.assertEqual(
            set(response.context["outside_form"].fields["membership"].queryset),
            {
                self.manager_membership, unsubmitted, empty_attendance, draft,
                day_off, limited, all_day, attendance,
            },
        )

    def test_wish_form_rejects_unsubmitted_or_day_off_even_with_confirmation(self):
        unsubmitted = self.member("manager_forged_unsubmitted")
        day_off = self.member("manager_forged_day_off", mode="day_off")
        DayOffRequest.objects.create(submission=self.submission(day_off), work_date=self.work_date)
        for membership in [unsubmitted, day_off]:
            with self.subTest(member=membership.pk):
                response = self.client.post(self.calendar_url, self.data(membership, confirm=True))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors.get("membership"))
                self.assertFalse(Shift.objects.exists())

    def test_confirmed_outside_shift_accepts_unsubmitted_day_off_empty_and_draft_without_changing_requests(self):
        unsubmitted = self.member("outside_unsubmitted")
        empty_attendance = self.member("outside_empty_attendance")
        self.submission(empty_attendance)
        draft = self.member("outside_draft", mode="day_off")
        self.availability(draft, self.submission(draft, submitted=False))
        day_off = self.member("outside_day_off", mode="day_off")
        day_off_request = DayOffRequest.objects.create(
            submission=self.submission(day_off), work_date=self.work_date,
        )
        original_submissions = list(ShiftSubmission.objects.order_by("pk").values_list(
            "pk", "submission_mode", "is_submitted", "submitted_at",
        ))
        original_availability_ids = list(Availability.objects.order_by("pk").values_list("pk", flat=True))

        for membership in [unsubmitted, empty_attendance, draft, day_off]:
            with self.subTest(member=membership.pk):
                response = self.client.post(
                    self.calendar_url, self.data(membership, mode="outside", start="08:00", confirm=True),
                )

                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                shift = Shift.objects.get()
                self.assertEqual(shift.membership, membership)
                self.assertEqual(shift.user_id, membership.user_id)
                self.assertEqual(shift.store, self.store)
                self.assertEqual((shift.start_time, shift.end_time), (time(8), time(22)))
                self.assertEqual(shift.note, "本人に確認済み")
                self.assertEqual(shift.display_color, "blue")
                self.assertFalse(shift.is_generated)
                shift.delete()

        self.assertEqual(
            list(ShiftSubmission.objects.order_by("pk").values_list(
                "pk", "submission_mode", "is_submitted", "submitted_at",
            )), original_submissions,
        )
        self.assertEqual(
            list(Availability.objects.order_by("pk").values_list("pk", flat=True)),
            original_availability_ids,
        )
        self.assertTrue(DayOffRequest.objects.filter(pk=day_off_request.pk).exists())

    def test_all_day_person_can_be_added_without_an_availability_record(self):
        membership = self.member("manager_empty_day_off", mode="day_off")
        self.submission(membership)

        response = self.client.post(self.calendar_url, self.data(membership, start="07:00", end="23:00"))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.membership, membership)
        self.assertEqual((shift.start_time, shift.end_time), (time(7), time(23)))
        self.assertFalse(shift.is_generated)

    def test_outside_time_needs_confirmation_in_both_manual_forms(self):
        for mode in ["availability", "outside"]:
            for submission_mode in ["availability", "day_off"]:
                with self.subTest(form=mode, submission_mode=submission_mode):
                    membership = self.member(f"manager_warning_{mode}_{submission_mode}", mode=submission_mode)
                    self.availability(membership, self.submission(membership))
                    response = self.client.post(self.calendar_url, self.data(membership, mode=mode, start="16:00"))
                    self.assertEqual(response.status_code, 200)
                    form_name = "form" if mode == "availability" else "outside_form"
                    self.assertTrue(response.context[form_name].errors)
                    self.assertFalse(Shift.objects.exists())
                    response = self.client.post(self.calendar_url, self.data(membership, mode=mode, start="16:00", confirm=True))
                    self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                    shift = Shift.objects.get()
                    self.assertEqual(shift.membership, membership)
                    self.assertEqual(shift.start_time, time(16))
                    self.assertEqual(shift.note, "本人に確認済み")
                    shift.delete()

    def test_confirmation_does_not_override_existing_shift_overlap(self):
        membership = self.member("manager_overlap", mode="day_off")
        self.submission(membership)
        existing = Shift.objects.create(
            user=membership.user, membership=membership, store=self.store, work_date=self.work_date,
            start_time=time(18), end_time=time(20), is_generated=False,
        )
        response = self.client.post(self.calendar_url, self.data(membership, mode="outside", confirm=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["outside_form"].errors)
        self.assertEqual(list(Shift.objects.values_list("pk", flat=True)), [existing.pk])

    def test_active_manager_is_a_candidate_when_month_is_submitted(self):
        self.manager_membership.submission_mode = "day_off"
        self.manager_membership.save(update_fields=["submission_mode"])
        self.submission(self.manager_membership)
        response = self.client.get(self.calendar_url)
        for form_name in ["form", "outside_form"]:
            self.assertIn(self.manager_membership, response.context[form_name].fields["membership"].queryset)

    def test_store_and_user_post_tampering_cannot_change_saved_shift_owner(self):
        membership = self.member("manager_owner", mode="day_off")
        self.submission(membership)
        foreign = self.member("manager_forged_owner", store=self.other_store)
        data = self.data(membership)
        data.update({"user": foreign.user_id, "store": self.other_store.pk, "is_generated": "on"})
        response = self.client.post(self.calendar_url, data)
        self.assertEqual(response.status_code, 302)
        saved = Shift.objects.get()
        self.assertEqual(saved.user_id, membership.user_id)
        self.assertEqual(saved.store_id, self.store.pk)
        self.assertFalse(saved.is_generated)

    def test_staff_user_cannot_bypass_manager_authorization(self):
        membership = self.member("manager_staff_access", mode="day_off")
        self.submission(membership)
        self.client.force_login(membership.user)
        response = self.client.post(self.calendar_url, self.data(membership, confirm=True))
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(Shift.objects.exists())

    def test_all_day_candidates_have_autofill_data_even_without_timed_wishes(self):
        membership = self.member("manager_all_day_autofill", mode="day_off")
        self.submission(membership)
        StoreOperatingHours.objects.create(store=self.store, start_time=time(9), end_time=time(23))

        response = self.client.get(self.calendar_url)

        data = response.context["availability_autofill"]
        self.assertEqual(data["all_day_membership_ids"], [str(membership.pk)])
        self.assertEqual(data["memberships"][str(membership.pk)], [])
        self.assertEqual((data["all_day_start_time"], data["all_day_end_time"]), ("09:00", "23:00"))
        self.assertContains(response, "全時間帯OK")
