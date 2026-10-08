from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, StaffInvitation, Store, StoreMembership


User = get_user_model()


class ManagerStaffInvitationTests(TestCase):
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
        StoreMembership.objects.create(store=cls.store, user=cls.staff, role="staff")

    def setUp(self):
        self.client.force_login(self.manager)
        self.list_url = reverse("manager_staff_list")
        self.create_url = reverse("manager_staff_invite_create")

    def invitation(self, **overrides):
        fields = {"store": self.store, "created_by": self.manager}
        fields.update(overrides)
        return StaffInvitation.objects.create(**fields)

    def revoke_url(self, invitation):
        return reverse("manager_staff_invite_revoke", args=[invitation.pk])

    def test_issue_uses_managers_store_and_seven_day_expiry(self):
        before = timezone.now()
        response = self.client.post(
            self.create_url,
            {"store": self.other_store.pk, "role": "manager", "user": self.staff.pk},
            follow=True,
        )
        after = timezone.now()

        self.assertRedirects(response, self.list_url)
        self.assertContains(response, "従業員の招待リンクを発行しました。")
        invitation = StaffInvitation.objects.get()
        self.assertEqual(invitation.store, self.store)
        self.assertEqual(invitation.created_by, self.manager)
        self.assertGreaterEqual(invitation.expires_at, before + timedelta(days=7))
        self.assertLessEqual(invitation.expires_at, after + timedelta(days=7))
        self.assertTrue(invitation.is_usable)
        self.assertIsNone(invitation.used_at)
        self.assertIsNone(invitation.used_by)
        self.assertIsNone(invitation.revoked_at)
        self.assertEqual(StoreMembership.objects.count(), 2)
        join_url = "http://testserver" + reverse(
            "staff_invitation_accept", args=[invitation.token]
        )
        self.assertContains(response, join_url)
        self.assertContains(response, "data-copy-invitation")

    def test_each_issued_link_has_a_unique_token(self):
        self.client.post(self.create_url)
        self.client.post(self.create_url)

        tokens = list(StaffInvitation.objects.values_list("token", flat=True))
        self.assertEqual(len(tokens), 2)
        self.assertNotEqual(tokens[0], tokens[1])

    def test_get_cannot_issue_or_revoke_links(self):
        invitation = self.invitation()

        for url in [self.create_url, self.revoke_url(invitation)]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 405)
        self.assertEqual(StaffInvitation.objects.count(), 1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.revoked_at)

    def test_list_is_store_scoped_and_newest_first_with_lifecycle_states(self):
        available = self.invitation()
        used = self.invitation(used_at=timezone.now(), used_by=self.staff)
        expired = self.invitation(expires_at=timezone.now() - timedelta(days=1))
        revoked = self.invitation(revoked_at=timezone.now())
        foreign = self.invitation(store=self.other_store)

        response = self.client.get(self.list_url)

        self.assertEqual(
            [invitation.pk for invitation in response.context["invitations"]],
            [revoked.pk, expired.pk, used.pk, available.pk],
        )
        for text in ["未使用", "使用済み", "期限切れ", "無効化", "田中 花子"]:
            self.assertContains(response, text)
        self.assertContains(response, str(available.token))
        for invitation in [used, expired, revoked, foreign]:
            self.assertNotContains(response, str(invitation.token))

    def test_unused_link_can_be_revoked(self):
        invitation = self.invitation()

        response = self.client.post(self.revoke_url(invitation), follow=True)

        self.assertRedirects(response, self.list_url)
        self.assertContains(response, "招待リンクを無効化しました。")
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)
        self.assertFalse(invitation.is_usable)

    def test_expired_unused_link_can_be_revoked(self):
        invitation = self.invitation(expires_at=timezone.now() - timedelta(days=1))

        response = self.client.post(self.revoke_url(invitation))

        self.assertRedirects(response, self.list_url)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)

    def test_used_or_already_revoked_links_are_not_changed(self):
        used = self.invitation(used_at=timezone.now(), used_by=self.staff)
        revoked_at = timezone.now() - timedelta(hours=1)
        revoked = self.invitation(revoked_at=revoked_at)

        for invitation in [used, revoked]:
            response = self.client.post(self.revoke_url(invitation), follow=True)
            self.assertRedirects(response, self.list_url)
            self.assertContains(response, "すでに使用済み、または無効化されています。")
        used.refresh_from_db()
        revoked.refresh_from_db()
        self.assertIsNone(used.revoked_at)
        self.assertEqual(used.used_by, self.staff)
        self.assertEqual(revoked.revoked_at, revoked_at)

    def test_other_store_invitation_returns_404_without_revocation(self):
        invitation = self.invitation(store=self.other_store)

        response = self.client.post(self.revoke_url(invitation))

        self.assertEqual(response.status_code, 404)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.revoked_at)

    def test_staff_and_inactive_manager_cannot_issue_or_revoke(self):
        invitation = self.invitation()
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        for user in [self.staff, self.manager]:
            self.client.force_login(user)
            for url in [self.create_url, self.revoke_url(invitation)]:
                with self.subTest(user=user.pk, url=url):
                    response = self.client.post(url)
                    self.assertRedirects(
                        response, reverse("manager_dashboard"),
                        fetch_redirect_response=False,
                    )
        self.assertEqual(StaffInvitation.objects.count(), 1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.revoked_at)

    def test_anonymous_user_cannot_issue_or_revoke(self):
        invitation = self.invitation()
        self.client.logout()

        for url in [self.create_url, self.revoke_url(invitation)]:
            response = self.client.post(url)
            self.assertRedirects(
                response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
            )
        self.assertEqual(StaffInvitation.objects.count(), 1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.revoked_at)

    def test_create_and_revoke_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        invitation = self.invitation()

        for url in [self.create_url, self.revoke_url(invitation)]:
            response = client.post(url)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(StaffInvitation.objects.count(), 1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.revoked_at)

        client.get(self.list_url)
        token = client.cookies["csrftoken"].value
        response = client.post(self.create_url, {"csrfmiddlewaretoken": token})
        self.assertRedirects(response, self.list_url)
        response = client.post(
            self.revoke_url(invitation), {"csrfmiddlewaretoken": token}
        )
        self.assertRedirects(response, self.list_url)
        self.assertEqual(StaffInvitation.objects.count(), 2)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)

    def test_link_from_inactive_issuer_is_not_offered_for_copy(self):
        another_manager = User.objects.create_user(username="other-manager")
        membership = StoreMembership.objects.create(
            user=another_manager, store=self.store, role="manager"
        )
        invitation = self.invitation(created_by=another_manager)
        membership.is_active = False
        membership.save(update_fields=["is_active"])

        response = self.client.get(self.list_url)

        self.assertContains(response, "無効化")
        self.assertNotContains(response, str(invitation.token))
