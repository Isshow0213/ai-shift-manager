from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, CompanyMembership, Store, StoreMembership, StoreShiftViewingPermission


class ShiftViewerPermissionManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.company = Company.objects.create(name="閲覧許可テスト会社")
        cls.other_company = Company.objects.create(name="別会社")
        cls.store = Store.objects.create(company=cls.company, name="本店")
        cls.other_store = Store.objects.create(company=cls.company, name="別店舗")
        cls.foreign_store = Store.objects.create(company=cls.other_company, name="他社店舗")
        cls.manager = user_model.objects.create_user(username="permission_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.viewer = user_model.objects.create_user(username="permission_viewer", last_name="田中", first_name="閲覧")
        cls.company_membership = CompanyMembership.objects.create(
            user=cls.viewer, company=cls.company, role="member"
        )
        cls.owner = user_model.objects.create_user(username="permission_owner")
        cls.owner_membership = CompanyMembership.objects.create(
            user=cls.owner, company=cls.company, role="owner"
        )
        disabled = user_model.objects.create_user(username="permission_disabled", is_active=False)
        cls.disabled_membership = CompanyMembership.objects.create(user=disabled, company=cls.company, role="member")
        foreign = user_model.objects.create_user(username="permission_foreign")
        cls.foreign_membership = CompanyMembership.objects.create(user=foreign, company=cls.other_company, role="owner")
        cls.staff = user_model.objects.create_user(username="permission_staff")
        StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")

    def setUp(self):
        self.client.force_login(self.manager)
        self.list_url = reverse("manager_shift_viewers")
        self.grant_url = reverse("manager_shift_viewer_grant")

    def revoke_url(self, permission):
        return reverse("manager_shift_viewer_revoke", args=[permission.pk])

    def make_permission(self, *, store=None, is_active=True):
        return StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=store or self.store,
            created_by=self.manager, is_active=is_active,
        )

    def permissions_snapshot(self):
        return list(StoreShiftViewingPermission.objects.order_by("pk").values())

    def test_management_choices_are_active_users_in_the_managers_company(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.store)
        self.assertEqual(response.context["company"], self.company)
        self.assertEqual(
            set(response.context["form"].fields["company_membership"].queryset),
            {self.company_membership, self.owner_membership},
        )
        self.assertFalse(response.context["form"].is_bound)
        self.assertNotContains(response, "permission_foreign")

    def test_management_list_hides_legacy_permission_for_another_company_member(self):
        own_permission = self.make_permission()
        foreign_user = self.foreign_membership.user
        foreign_user.last_name = "他社"
        foreign_user.first_name = "閲覧者"
        foreign_user.save(update_fields=["last_name", "first_name"])
        StoreShiftViewingPermission.objects.create(
            company_membership=self.foreign_membership, store=self.store,
            created_by=self.manager, is_active=True,
        )

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["permissions"]), [own_permission])
        self.assertNotIn(
            self.foreign_membership, response.context["form"].fields["company_membership"].queryset
        )
        self.assertNotContains(response, foreign_user.full_name_japanese)

    def test_grant_saves_only_fixed_store_permission_without_creating_staff_membership(self):
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        users_before = list(get_user_model().objects.order_by("pk").values())

        response = self.client.post(self.grant_url, {
            "company_membership": self.company_membership.pk, "store": self.other_store.pk,
            "company": self.other_company.pk, "user": self.owner.pk, "created_by": self.owner.pk,
            "is_active": "", "role": "staff",
        })

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission = StoreShiftViewingPermission.objects.get()
        self.assertEqual(permission.company_membership, self.company_membership)
        self.assertEqual(permission.store, self.store)
        self.assertEqual(permission.created_by, self.manager)
        self.assertTrue(permission.is_active)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), users_before)

    def test_company_owner_can_receive_a_viewing_grant_without_manager_permissions(self):
        response = self.client.post(self.grant_url, {"company_membership": self.owner_membership.pk})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertTrue(StoreShiftViewingPermission.objects.filter(company_membership=self.owner_membership, store=self.store).exists())
        self.assertFalse(StoreMembership.objects.filter(user=self.owner).exists())
        self.client.force_login(self.owner)
        response = self.client.get(self.list_url)
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)

    def test_repeated_grant_and_revival_preserve_one_record_and_creation_time(self):
        permission = self.make_permission(is_active=False)
        original_created_at = permission.created_at
        for _ in range(2):
            response = self.client.post(self.grant_url, {"company_membership": self.company_membership.pk})

            self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
            self.assertEqual(StoreShiftViewingPermission.objects.count(), 1)
            permission.refresh_from_db()
            self.assertTrue(permission.is_active)
            self.assertEqual(permission.created_at, original_created_at)
            self.assertEqual(permission.created_by, self.manager)

    def test_foreign_disabled_missing_and_invalid_company_memberships_are_rejected(self):
        before = self.permissions_snapshot()
        for value in [self.foreign_membership.pk, self.disabled_membership.pk, "invalid", "", None, 999999, "9" * 500]:
            with self.subTest(value=value):
                data = {} if value is None else {"company_membership": value}
                response = self.client.post(self.grant_url, data)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].is_bound)
                self.assertTrue(response.context["form"].errors.get("company_membership"))
                self.assertEqual(self.permissions_snapshot(), before)

    def test_revoke_only_deactivates_the_target_and_immediately_denies_viewer_access(self):
        permission = self.make_permission()
        before = self.permissions_snapshot()

        response = self.client.post(self.revoke_url(permission), {
            "store": self.other_store.pk, "company_membership": self.owner_membership.pk,
        })

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertEqual(self.permissions_snapshot(), [
            {**row, "is_active": False} if row["id"] == permission.pk else row for row in before
        ])
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("viewer_shift_overview")).status_code, 403)
        self.assertEqual(self.client.get(reverse("viewer_shift_overview"), {"store": self.store.pk}).status_code, 404)

    def test_repeated_revoke_keeps_the_same_inactive_record(self):
        permission = self.make_permission(is_active=False)
        before = self.permissions_snapshot()

        response = self.client.post(self.revoke_url(permission))

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertEqual(self.permissions_snapshot(), before)

    def test_regrant_after_revocation_restores_viewing_without_personal_staff_permissions(self):
        permission = self.make_permission()
        original_created_at = permission.created_at
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        self.client.post(self.revoke_url(permission))
        self.client.force_login(self.viewer)
        viewer_url = reverse("viewer_shift_overview")
        self.assertEqual(self.client.get(viewer_url).status_code, 403)
        self.assertRedirects(
            self.client.get(reverse("availability_list")), viewer_url, fetch_redirect_response=False
        )
        self.client.force_login(self.manager)

        response = self.client.post(self.grant_url, {"company_membership": self.company_membership.pk})

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission.refresh_from_db()
        self.assertTrue(permission.is_active)
        self.assertEqual(permission.created_at, original_created_at)
        self.assertEqual(StoreShiftViewingPermission.objects.count(), 1)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)
        self.client.force_login(self.viewer)
        response = self.client.get(viewer_url, {"store": self.store.pk, "year": 2026, "month": 11})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.store)
        self.assertRedirects(
            self.client.get(reverse("availability_create")), viewer_url, fetch_redirect_response=False
        )

    def test_other_store_and_nonexistent_permissions_cannot_be_revoked(self):
        other_permission = self.make_permission(store=self.other_store)
        before = self.permissions_snapshot()
        urls = [self.revoke_url(other_permission), reverse("manager_shift_viewer_revoke", args=[999999])]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.post(url)

                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.permissions_snapshot(), before)

    def test_multiple_manager_stores_keep_the_usual_first_store_scope(self):
        StoreMembership.objects.create(user=self.manager, store=self.other_store, role="manager")
        other_permission = self.make_permission(store=self.other_store)

        response = self.client.post(self.revoke_url(other_permission))

        self.assertEqual(response.status_code, 404)
        other_permission.refresh_from_db()
        self.assertTrue(other_permission.is_active)
        response = self.client.get(self.list_url, {"store": self.other_store.pk})
        self.assertEqual(response.context["store"], self.store)
        self.assertEqual(list(response.context["permissions"]), [])

    def test_staff_company_owner_and_inactive_manager_cannot_manage_grants(self):
        permission = self.make_permission()
        for user in [self.staff, self.owner, self.manager]:
            with self.subTest(user=user.pk):
                if user == self.manager:
                    self.manager_membership.is_active = False
                    self.manager_membership.save(update_fields=["is_active"])
                self.client.force_login(user)
                before = self.permissions_snapshot()
                requests = [
                    self.client.get(self.list_url),
                    self.client.post(self.grant_url, {"company_membership": self.owner_membership.pk}),
                    self.client.post(self.revoke_url(permission)),
                ]
                for response in requests:
                    self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
                self.assertEqual(self.permissions_snapshot(), before)

    def test_anonymous_users_cannot_manage_grants(self):
        permission = self.make_permission()
        self.client.logout()
        before = self.permissions_snapshot()
        for method, url in [
            (self.client.get, self.list_url), (self.client.post, self.grant_url),
            (self.client.post, self.revoke_url(permission)),
        ]:
            with self.subTest(url=url):
                response = method(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
                self.assertEqual(self.permissions_snapshot(), before)

    def test_management_get_is_read_only_and_method_restrictions_are_enforced(self):
        permission = self.make_permission()
        before = self.permissions_snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.list_url)
            self.assertEqual(response.status_code, 200)

        self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        )], [])
        for method, url in [
            (self.client.post, self.list_url), (self.client.get, self.grant_url),
            (self.client.put, self.grant_url), (self.client.get, self.revoke_url(permission)),
            (self.client.put, self.revoke_url(permission)),
        ]:
            with self.subTest(method=method.__name__, url=url):
                self.assertEqual(method(url).status_code, 405)
                self.assertEqual(self.permissions_snapshot(), before)

    def test_grant_and_revoke_require_csrf_and_accept_valid_tokens(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        csrf_client.get(self.list_url)

        response = csrf_client.post(self.grant_url, {"company_membership": self.company_membership.pk})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(StoreShiftViewingPermission.objects.exists())
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(self.grant_url, {"company_membership": self.company_membership.pk}, HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission = StoreShiftViewingPermission.objects.get()
        self.assertEqual(csrf_client.post(self.revoke_url(permission)).status_code, 403)
        permission.refresh_from_db()
        self.assertTrue(permission.is_active)
        response = csrf_client.post(self.revoke_url(permission), HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        permission.refresh_from_db()
        self.assertFalse(permission.is_active)
