from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Company, CompanyMembership, Store, StoreMembership, StoreShiftViewingPermission
from shifts.models import Shift


class CompanyCapabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.company = Company.objects.create(name="会社権限テスト会社")
        cls.other_company = Company.objects.create(name="他社")
        cls.store = Store.objects.create(company=cls.company, name="本店")
        cls.other_store = Store.objects.create(company=cls.company, name="分店")
        cls.manager = user_model.objects.create_user(username="capability_manager")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.company_user = user_model.objects.create_user(username="capability_company_user")
        cls.company_membership = CompanyMembership.objects.create(
            user=cls.company_user, company=cls.company, role="member"
        )
        cls.staff = user_model.objects.create_user(username="capability_staff")
        cls.staff_membership = StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        cls.staff_company_membership = CompanyMembership.objects.create(
            user=cls.staff, company=cls.company, role="member"
        )
        cls.work_date = date(2030, 1, 12)
        cls.staff_shift = Shift.objects.create(
            user=cls.staff, membership=cls.staff_membership, store=cls.store,
            work_date=cls.work_date, start_time=time(9), end_time=time(17),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.grant_url = reverse("manager_shift_viewer_grant")
        self.list_url = reverse("manager_shift_viewers")
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 13)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)

    def payload(self, *, view=False, submit=False, **extra):
        data = {"company_membership": self.company_membership.pk, "permissions_form": "1"}
        if view:
            data["can_view_shifts"] = "on"
        if submit:
            data["can_submit_availability"] = "on"
        data.update(extra)
        return data

    def make_permission(self, *, view=True, submit=False, active=True, company_membership=None):
        return StoreShiftViewingPermission.objects.create(
            company_membership=company_membership or self.company_membership, store=self.store,
            created_by=self.manager, is_active=active,
            can_view_shifts=view, can_submit_availability=submit,
        )

    def test_legacy_grant_payload_still_creates_read_only_permission(self):
        response = self.client.post(self.grant_url, {"company_membership": self.company_membership.pk})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission = StoreShiftViewingPermission.objects.get()
        self.assertTrue(permission.can_view_shifts)
        self.assertFalse(permission.can_submit_availability)
        self.assertTrue(permission.is_active)

    def test_capability_combinations_are_independent_and_update_one_record(self):
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        for view, submit in [(True, False), (False, True), (True, True)]:
            with self.subTest(view=view, submit=submit):
                response = self.client.post(self.grant_url, self.payload(
                    view=view, submit=submit, store=self.other_store.pk,
                ))
                self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
                permission = StoreShiftViewingPermission.objects.get()
                self.assertEqual(permission.store, self.store)
                self.assertEqual(permission.can_view_shifts, view)
                self.assertEqual(permission.can_submit_availability, submit)
                self.assertTrue(permission.is_active)
                self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)

    def test_unchecking_both_capabilities_is_invalid_and_preserves_existing_permission(self):
        permission = self.make_permission(view=True, submit=True)
        before = list(StoreShiftViewingPermission.objects.values())

        response = self.client.post(self.grant_url, self.payload())

        self.assertEqual(response.status_code, 200)
        rows = {row.pk: row for row in response.context["permissions"]}
        self.assertTrue(rows[permission.pk].permissions_form.is_bound)
        self.assertTrue(rows[permission.pk].permissions_form.non_field_errors())
        self.assertFalse(rows[permission.pk].permissions_form["can_view_shifts"].value())
        self.assertFalse(rows[permission.pk].permissions_form["can_submit_availability"].value())
        self.assertEqual(list(StoreShiftViewingPermission.objects.values()), before)

    def test_regrant_revives_permission_with_the_new_capabilities(self):
        permission = self.make_permission(view=True, submit=False, active=False)
        original_created_at = permission.created_at

        response = self.client.post(self.grant_url, self.payload(view=False, submit=True))

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission.refresh_from_db()
        self.assertTrue(permission.is_active)
        self.assertFalse(permission.can_view_shifts)
        self.assertTrue(permission.can_submit_availability)
        self.assertEqual(permission.created_at, original_created_at)
        self.assertEqual(StoreShiftViewingPermission.objects.count(), 1)

    def test_revoke_disables_both_capabilities_without_restoring_personal_shift_access(self):
        permission = self.make_permission(view=True, submit=True)

        response = self.client.post(reverse("manager_shift_viewer_revoke", args=[permission.pk]))

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission.refresh_from_db()
        self.assertFalse(permission.is_active)
        self.client.force_login(self.company_user)
        self.assertEqual(self.client.get(reverse("viewer_shift_overview")).status_code, 403)
        self.assertRedirects(
            self.client.get(reverse("shift_list")), reverse("viewer_shift_overview"), fetch_redirect_response=False
        )

    def test_flag_update_requires_csrf_and_accepts_a_valid_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        csrf_client.get(self.list_url)

        response = csrf_client.post(self.grant_url, self.payload(view=False, submit=True))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(StoreShiftViewingPermission.objects.exists())
        response = csrf_client.post(self.grant_url, self.payload(view=False, submit=True),
                                    HTTP_X_CSRFTOKEN=csrf_client.cookies["csrftoken"].value)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission = StoreShiftViewingPermission.objects.get()
        self.assertFalse(permission.can_view_shifts)
        self.assertTrue(permission.can_submit_availability)

    def test_viewing_capability_is_required_for_viewer_overview(self):
        permission = self.make_permission(view=False, submit=True)
        self.client.force_login(self.company_user)
        url = reverse("viewer_shift_overview")

        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, {"store": self.store.pk}).status_code, 404)
        permission.can_view_shifts = True
        permission.save(update_fields=["can_view_shifts"])
        self.assertEqual(self.client.get(url, {"store": self.store.pk, "year": 2030, "month": 1}).status_code, 200)

    def test_company_permission_holder_with_historical_shift_is_hidden_from_every_table_and_totals(self):
        self.make_permission(view=True, submit=True)
        hidden_shift = Shift.objects.create(
            user=self.company_user, membership=None, store=self.store,
            work_date=self.work_date, start_time=time(8), end_time=time(22), note="会社所属者の過去シフト",
        )
        before = list(Shift.objects.order_by("pk").values())
        for user, name in [
            (self.manager, "manager_shift_overview"), (self.staff, "store_shift_overview"),
            (self.company_user, "viewer_shift_overview"),
        ]:
            with self.subTest(name=name):
                self.client.force_login(user)
                response = self.client.get(reverse(name), {"store": self.store.pk, "year": 2030, "month": 1})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.company_user.pk, [row["user"].pk for row in response.context["rows"]])
                self.assertEqual(response.context["total_shifts"], 1)
                self.assertEqual(response.context["scheduled_staff_count"], 1)
                self.assertEqual(response.context["total_hours_display"], "8:00")
                self.assertNotContains(response, hidden_shift.note)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before)

    def test_inactive_permission_still_hides_nonstaff_history_from_the_table(self):
        self.make_permission(view=False, submit=True, active=False)
        Shift.objects.create(
            user=self.company_user, store=self.store, work_date=self.work_date,
            start_time=time(8), end_time=time(22),
        )

        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})

        self.assertNotIn(self.company_user.pk, [row["user"].pk for row in response.context["rows"]])
        self.assertEqual(response.context["total_shifts"], 1)

    def test_actual_store_staff_with_extra_permission_remains_in_the_table(self):
        self.make_permission(view=True, submit=True, company_membership=self.staff_company_membership)

        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})

        self.assertIn(self.staff.pk, [row["user"].pk for row in response.context["rows"]])
        self.assertEqual(response.context["total_shifts"], 1)
        self.assertEqual(response.context["total_hours_display"], "8:00")

    def test_staff_membership_in_another_store_does_not_keep_a_company_person_in_this_table(self):
        self.make_permission(view=True, submit=True)
        StoreMembership.objects.create(user=self.company_user, store=self.other_store, role="staff")
        Shift.objects.create(
            user=self.company_user, store=self.store, work_date=self.work_date,
            start_time=time(8), end_time=time(22),
        )

        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})

        self.assertNotIn(self.company_user.pk, [row["user"].pk for row in response.context["rows"]])
        self.assertEqual(response.context["total_shifts"], 1)

    def test_cross_company_legacy_permission_does_not_hide_a_historical_person(self):
        foreign_user = get_user_model().objects.create_user(username="capability_foreign_history")
        foreign_membership = CompanyMembership.objects.create(user=foreign_user, company=self.other_company, role="member")
        self.make_permission(view=True, submit=True, company_membership=foreign_membership)
        Shift.objects.create(
            user=foreign_user, store=self.store, work_date=self.work_date,
            start_time=time(7), end_time=time(8),
        )

        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})

        self.assertIn(foreign_user.pk, [row["user"].pk for row in response.context["rows"]])
        self.assertEqual(response.context["total_shifts"], 2)
        self.assertEqual(response.context["total_hours_display"], "9:00")

    def test_overview_visibility_is_independent_and_toggles_all_shared_tables(self):
        historical_shift = Shift.objects.create(
            user=self.company_user, store=self.store, work_date=self.work_date,
            start_time=time(8), end_time=time(22), note="会社メンバーの保存済み勤務",
        )
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        for shown in (True, False, True):
            self.client.force_login(self.manager)
            payload = self.payload(view=True, submit=True)
            if shown:
                payload["show_in_shift_overview"] = "on"
            self.assertRedirects(self.client.post(self.grant_url, payload), self.list_url, fetch_redirect_response=False)
            permission = StoreShiftViewingPermission.objects.get(company_membership=self.company_membership)
            self.assertEqual(permission.show_in_shift_overview, shown)
            self.assertTrue(permission.can_view_shifts)
            self.assertTrue(permission.can_submit_availability)
            for user, name in ((self.manager, "manager_shift_overview"), (self.staff, "store_shift_overview"),
                               (self.company_user, "viewer_shift_overview")):
                self.client.force_login(user)
                response = self.client.get(reverse(name), {"store": self.store.pk, "year": 2030, "month": 1})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.company_user.pk in [row["user"].pk for row in response.context["rows"]], shown)
                self.assertEqual(response.context["total_shifts"], 2 if shown else 1)
                self.assertEqual(response.context["total_hours_display"], "22:00" if shown else "8:00")
                if shown:
                    self.assertContains(response, historical_shift.note)
                else:
                    self.assertNotContains(response, historical_shift.note)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)
        self.assertTrue(Shift.objects.filter(pk=historical_shift.pk).exists())

    def test_display_only_member_without_shifts_can_be_reordered_and_has_no_login_capabilities(self):
        from shifts.models import ShiftDisplayOrder
        from shifts.overview import save_overview_order

        self.assertRedirects(self.client.post(self.grant_url, self.payload(show_in_shift_overview="on")),
                             self.list_url, fetch_redirect_response=False)
        permission = StoreShiftViewingPermission.objects.get(company_membership=self.company_membership)
        self.assertFalse(permission.can_view_shifts)
        self.assertFalse(permission.can_submit_availability)
        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})
        visible_ids = [row["user"].pk for row in response.context["rows"]]
        self.assertIn(self.company_user.pk, visible_ids)
        company_row = next(row for row in response.context["rows"] if row["user"].pk == self.company_user.pk)
        self.assertEqual(company_row["shift_count"], 0)
        self.assertTrue(company_row["is_company_member"])
        self.assertContains(response, "会社メンバー")
        wanted_order = [self.company_user.pk] + [pk for pk in visible_ids if pk != self.company_user.pk]
        save_overview_order(self.store, wanted_order)
        self.assertEqual(ShiftDisplayOrder.objects.get(store=self.store, user=self.company_user).position, 0)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("store_shift_overview"), {"year": 2030, "month": 1})
        self.assertEqual([row["user"].pk for row in response.context["rows"]], wanted_order)
        self.client.force_login(self.company_user)
        self.assertEqual(self.client.get(reverse("viewer_shift_overview")).status_code, 403)
        self.assertRedirects(self.client.post(reverse("availability_create"), {
            "work_date": "2030-01-12", "start_time": "09:00", "end_time": "18:00",
        }), reverse("viewer_shift_overview"), fetch_redirect_response=False)
        self.client.force_login(self.manager)
        self.client.post(reverse("manager_shift_viewer_revoke", args=[permission.pk]))
        response = self.client.get(reverse("manager_shift_overview"), {"year": 2030, "month": 1})
        self.assertNotIn(self.company_user.pk, [row["user"].pk for row in response.context["rows"]])
