from datetime import date, time
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Company, CompanyMembership, Store, StoreMembership
from accounts.views import get_login_destination
from shifts.models import Availability, Shift


class StoreStaffPermissionTests(TestCase):
    """店舗所属の権限は、ログイン先と実際の操作の両方に適用される。"""

    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="店舗権限テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.password = "test-store-permission-password"
        users = get_user_model()
        cls.manager = users.objects.create_user(username="permission-manager", password=cls.password)
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager",
        )
        cls.staff = users.objects.create_user(
            username="permission-staff", password=cls.password, last_name="田中", first_name="花子",
        )
        cls.membership = StoreMembership.objects.create(user=cls.staff, store=cls.store)
        cls.coworker = users.objects.create_user(
            username="permission-coworker", last_name="鈴木", first_name="太郎",
        )
        cls.coworker_membership = StoreMembership.objects.create(user=cls.coworker, store=cls.store)
        cls.company_member = users.objects.create_user(username="permission-company-only")
        CompanyMembership.objects.create(user=cls.company_member, company=company)
        cls.work_date = date(2030, 1, 10)
        cls.own_shift = Shift.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store,
            work_date=cls.work_date, start_time=time(9), end_time=time(17), note="本人にだけ残す確定シフトメモ",
        )
        cls.coworker_shift = Shift.objects.create(
            user=cls.coworker, membership=cls.coworker_membership, store=cls.store,
            work_date=cls.work_date, start_time=time(12), end_time=time(18), note="同僚の確定シフト",
        )
        cls.availability = Availability.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store,
            work_date=cls.work_date, start_time=time(9), end_time=time(17),
        )
        cls.query = {"year": 2030, "month": 1, "date": cls.work_date.isoformat()}

    def setUp(self):
        self.client.force_login(self.staff)

    def set_permissions(self, *, membership=None, **permissions):
        membership = membership or self.membership
        StoreMembership.objects.filter(pk=membership.pk).update(**permissions)
        membership.refresh_from_db()

    def overview(self, *, store=None, manager=False):
        url = reverse("manager_shift_overview" if manager else "store_shift_overview")
        return self.client.get(url, {**self.query, **({"store": store.pk} if store else {})})

    def submit(self, *, target_store=None, **extra):
        url = reverse("availability_create")
        if target_store:
            url += f"?store={target_store.pk}"
        return self.client.post(url, {
            "work_date": "2030-01-11", "start_time": "09:00", "end_time": "17:00", **extra,
        })

    def test_existing_staff_default_permissions_preserve_all_three_features(self):
        for field in ("can_view_shifts", "can_submit_availability", "show_in_shift_overview"):
            self.assertTrue(getattr(self.membership, field))
        self.assertEqual(self.overview().status_code, 200)
        self.assertEqual(get_login_destination(self.staff), "availability_list")
        self.assertEqual(self.submit().status_code, 302)

    def test_view_permission_denies_other_staff_table_but_preserves_personal_confirmed_shifts(self):
        self.set_permissions(can_view_shifts=False)

        response = self.overview()

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.coworker_shift.note, status_code=403)
        personal = self.client.get(reverse("shift_list"), self.query)
        self.assertEqual(personal.status_code, 200)
        self.assertEqual(list(personal.context["shifts"]), [self.own_shift])
        self.assertContains(personal, self.own_shift.note)
        self.assertNotContains(personal, self.coworker_shift.note)

    def test_submission_disabled_keeps_existing_wishes_readable_and_blocks_new_post(self):
        self.set_permissions(can_submit_availability=False)
        before = list(Availability.objects.order_by("pk").values())

        calendar = self.client.get(reverse("availability_list"), self.query)
        response = self.submit(can_submit_availability="on", role="manager")

        self.assertEqual(calendar.status_code, 200)
        self.assertEqual(list(calendar.context["selected_availabilities"]), [self.availability])
        self.assertFalse(calendar.context["can_submit_for_selected_store"])
        self.assertEqual(self.client.get(reverse("availability_create")).status_code, 403)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)

    def test_submission_disabled_blocks_deleting_existing_own_wish(self):
        self.set_permissions(can_submit_availability=False)

        response = self.client.post(reverse("availability_delete", args=[self.availability.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Availability.objects.filter(pk=self.availability.pk).exists())

    def test_enabling_submission_restores_creation_and_deletion_without_view_permission(self):
        self.set_permissions(can_view_shifts=False, can_submit_availability=False)
        self.assertEqual(self.submit().status_code, 403)
        self.set_permissions(can_submit_availability=True)

        response = self.submit()
        deletion = self.client.post(reverse("availability_delete", args=[self.availability.pk]))

        self.assertEqual(response.status_code, 302)
        created = Availability.objects.get(work_date=date(2030, 1, 11))
        self.assertEqual(created.membership, self.membership)
        self.assertEqual(created.store, self.store)
        self.assertEqual(deletion.status_code, 302)
        self.assertFalse(Availability.objects.filter(pk=self.availability.pk).exists())
        self.assertEqual(self.overview().status_code, 403)

    def test_hiding_staff_excludes_rows_and_totals_from_manager_and_staff_tables(self):
        self.set_permissions(show_in_shift_overview=False)
        before = list(Shift.objects.order_by("pk").values())

        staff_response = self.overview()
        self.client.force_login(self.manager)
        manager_response = self.overview(manager=True)

        for response in (staff_response, manager_response):
            with self.subTest(template=response.templates[0].name):
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.staff.pk, {row["user"].pk for row in response.context["rows"]})
                self.assertIn(self.coworker.pk, {row["user"].pk for row in response.context["rows"]})
                self.assertEqual(response.context["total_shifts"], 1)
                self.assertEqual(response.context["scheduled_staff_count"], 1)
                self.assertEqual(response.context["total_working_days"], 1)
                self.assertEqual(response.context["total_hours_display"], "6:00")
                self.assertEqual(response.context["day_staff_counts"][9], 1)
                self.assertNotContains(response, self.own_shift.note)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before)
        self.client.force_login(self.staff)
        personal = self.client.get(reverse("shift_list"), self.query)
        self.assertContains(personal, self.own_shift.note)

    def test_display_permission_is_independent_from_view_and_submission_permissions(self):
        self.set_permissions(can_view_shifts=False, can_submit_availability=False, show_in_shift_overview=True)
        self.client.force_login(self.manager)

        response = self.overview(manager=True)

        self.assertIn(self.staff.pk, {row["user"].pk for row in response.context["rows"]})
        self.assertEqual(response.context["total_shifts"], 2)
        self.assertEqual(response.context["total_hours_display"], "14:00")

    def test_manager_capabilities_are_preserved_even_if_raw_flags_are_false(self):
        self.set_permissions(membership=self.manager_membership,
                             can_view_shifts=False, can_submit_availability=False, show_in_shift_overview=False)
        self.client.force_login(self.manager)

        self.assertEqual(self.overview().status_code, 200)
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(get_login_destination(self.manager), "manager_dashboard")
        manager_response = self.overview(manager=True)
        self.assertIn(self.manager.pk, {row["user"].pk for row in manager_response.context["rows"]})

    def test_permission_flags_are_scoped_to_the_selected_membership(self):
        self.set_permissions(can_view_shifts=False, can_submit_availability=False, show_in_shift_overview=False)
        other_membership = StoreMembership.objects.create(user=self.staff, store=self.other_store)
        other_shift = Shift.objects.create(
            user=self.staff, membership=other_membership, store=self.other_store,
            work_date=self.work_date, start_time=time(10), end_time=time(14), note="別店舗シフト",
        )

        self.assertEqual(self.overview(store=self.store).status_code, 403)
        allowed_table = self.overview(store=self.other_store)
        self.assertEqual(allowed_table.status_code, 200)
        self.assertEqual(allowed_table.context["store"], self.other_store)
        self.assertContains(allowed_table, other_shift.note)
        self.assertNotContains(allowed_table, self.coworker_shift.note)
        self.assertEqual(self.submit(target_store=self.store).status_code, 403)
        allowed_submit = self.submit(target_store=self.other_store)
        self.assertEqual(allowed_submit.status_code, 302)
        saved = Availability.objects.get(work_date=date(2030, 1, 11))
        self.assertEqual(saved.membership, other_membership)
        self.assertEqual(saved.store, self.other_store)
        self.assertEqual(parse_qs(urlsplit(allowed_submit.url).query)["store"], [str(self.other_store.pk)])

    def test_own_wish_deletion_uses_its_membership_permission_instead_of_first_store(self):
        other_membership = StoreMembership.objects.create(
            user=self.staff, store=self.other_store, can_submit_availability=False,
        )
        other_wish = Availability.objects.create(
            user=self.staff, membership=other_membership, store=self.other_store,
            work_date=date(2030, 1, 12), start_time=time(10), end_time=time(14),
        )

        response = self.client.post(reverse("availability_delete", args=[other_wish.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Availability.objects.filter(pk=other_wish.pk).exists())
        self.assertEqual(self.client.post(reverse("availability_delete", args=[self.availability.pk])).status_code, 302)

    def test_store_parameter_cannot_target_a_store_without_active_membership(self):
        before = Availability.objects.count()
        for url_name in ("availability_list", "availability_create", "store_shift_overview"):
            with self.subTest(view=url_name):
                response = self.client.get(reverse(url_name), {"store": self.other_store.pk})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.submit(target_store=self.other_store).status_code, 404)
        self.assertEqual(Availability.objects.count(), before)

    def test_tampering_with_posted_user_membership_and_store_keeps_authorized_membership(self):
        response = self.submit(
            user=self.coworker.pk, membership=self.coworker_membership.pk, store=self.other_store.pk,
        )

        self.assertEqual(response.status_code, 302)
        saved = Availability.objects.get(work_date=date(2030, 1, 11))
        self.assertEqual(saved.user, self.staff)
        self.assertEqual(saved.membership, self.membership)
        self.assertEqual(saved.store, self.store)

    def test_company_membership_alone_no_longer_grants_store_access_or_submission(self):
        self.client.force_login(self.company_member)
        before = Availability.objects.count()

        response = self.submit()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Availability.objects.count(), before)
        self.assertEqual(self.overview(store=self.store).status_code, 404)
        self.assertFalse(StoreMembership.objects.filter(user=self.company_member).exists())

    def test_removed_company_permission_routes_are_unavailable(self):
        self.client.force_login(self.manager)
        for path in ("/manager/shifts/viewers/", "/manager/shifts/viewers/grant/", "/viewer/shifts/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_dashboard_submission_counts_include_only_staff_allowed_to_submit(self):
        self.set_permissions(can_submit_availability=False)
        Availability.objects.create(
            user=self.coworker, membership=self.coworker_membership, store=self.store,
            work_date=self.work_date, start_time=time(10), end_time=time(18),
        )
        self.client.force_login(self.manager)

        response = self.client.get(reverse("manager_dashboard"), self.query)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_staff_count"], 2)
        self.assertEqual(response.context["submission_staff_count"], 1)
        self.assertEqual(response.context["submitted_count"], 1)
        self.assertEqual(response.context["unsubmitted_count"], 0)

    def test_login_and_index_route_by_active_store_capabilities(self):
        for can_view, can_submit, destination in (
            (True, True, "availability_list"),
            (False, True, "availability_list"),
            (True, False, "store_shift_overview"),
            (False, False, "shift_list"),
        ):
            with self.subTest(view=can_view, submit=can_submit):
                self.set_permissions(can_view_shifts=can_view, can_submit_availability=can_submit)
                self.assertEqual(get_login_destination(self.staff), destination)
                self.client.logout()
                login = self.client.post(reverse("login"), {
                    "username": self.staff.username, "password": self.password,
                })
                self.assertRedirects(login, reverse(destination), fetch_redirect_response=False)
                self.assertRedirects(self.client.get(reverse("index")), reverse(destination), fetch_redirect_response=False)

    def test_login_uses_any_active_allowed_staff_membership_and_manager_takes_priority(self):
        self.set_permissions(can_view_shifts=False, can_submit_availability=False)
        other_membership = StoreMembership.objects.create(
            user=self.staff, store=self.other_store, can_submit_availability=False,
        )
        self.assertEqual(get_login_destination(self.staff), "store_shift_overview")
        other_membership.can_submit_availability = True
        other_membership.save(update_fields=["can_submit_availability"])
        self.assertEqual(get_login_destination(self.staff), "availability_list")
        other_membership.role = "manager"
        other_membership.save(update_fields=["role"])
        self.assertEqual(get_login_destination(self.staff), "manager_dashboard")
