from datetime import timedelta
from unittest.mock import PropertyMock, patch
from uuid import uuid4

from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import EMAIL_EXISTS_ERROR, StaffInvitationRegistrationForm
from .models import Company, StaffInvitation, Store, StoreMembership


User = get_user_model()


class StaffInvitationAcceptanceTests(TestCase):
    password = "M7!cW9r#uT4sL2qZ"

    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="招待店舗")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = User.objects.create_user(
            username="invitation_manager", email="manager@example.com"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.existing_user = User.objects.create_user(
            username="existing_employee", email="existing@example.com"
        )

    def setUp(self):
        self.invitation = StaffInvitation.objects.create(store=self.store, created_by=self.manager)
        self.url = reverse("staff_invitation_accept", args=[self.invitation.token])

    def registration_data(self, **overrides):
        data = {
            "last_name": "田中",
            "first_name": "花子",
            "email": "new.person@example.com",
            "password1": self.password,
            "password2": self.password,
        }
        data.update(overrides)
        return data

    def assert_invitation_unused(self):
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.used_at)
        self.assertIsNone(self.invitation.used_by_id)

    def assert_no_registration(self):
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(StoreMembership.objects.count(), 1)
        self.assert_invitation_unused()

    def test_get_shows_store_and_registration_fields_without_consuming_invitation(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/invitation_register.html")
        self.assertContains(response, "所属店舗：招待店舗")
        for field in ["last_name", "first_name", "email", "password1", "password2"]:
            self.assertContains(response, f'name="{field}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assert_no_registration()

    def test_registration_ignores_posted_permissions_and_logs_in_the_new_staff(self):
        response = self.client.post(
            self.url,
            self.registration_data(
                email="  New.Person@EXAMPLE.COM  ", username="chosen_username",
                store=self.other_store.pk, role="manager", rank="A",
                desired_shifts_per_week="99", is_staff="on", is_superuser="on",
            ),
            follow=True,
        )

        self.assertRedirects(response, reverse("availability_list"))
        user = User.objects.get(email="new.person@example.com")
        self.assertTrue(user.username.startswith("staff_"))
        self.assertNotEqual(user.username, "chosen_username")
        self.assertEqual((user.last_name, user.first_name), ("田中", "花子"))
        self.assertEqual(user.role, "staff")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(self.password))
        self.assertNotEqual(user.password, self.password)
        membership = StoreMembership.objects.get(user=user)
        self.assertEqual(membership.store, self.store)
        self.assertEqual((membership.role, membership.rank), ("staff", "C"))
        self.assertEqual(membership.desired_shifts_per_week, 0)
        self.assertTrue(membership.is_active)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.used_by, user)
        self.assertIsNotNone(self.invitation.used_at)
        self.assertEqual(int(self.client.session[SESSION_KEY]), user.pk)
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], "accounts.backends.EmailOrUsernameBackend")
        self.assertContains(response, "従業員アカウントを登録しました。")

    def test_registered_staff_can_log_out_and_log_back_in_using_email(self):
        self.client.post(self.url, self.registration_data())
        user = User.objects.get(email="new.person@example.com")
        self.client.post(reverse("logout"))

        response = self.client.post(
            reverse("login"),
            {"username": "NEW.PERSON@example.com", "password": self.password},
            follow=True,
        )

        self.assertRedirects(response, reverse("availability_list"))
        self.assertEqual(int(self.client.session[SESSION_KEY]), user.pk)
        self.assertContains(response, "田中 花子")

    def test_required_identity_and_password_fields_cannot_be_blank(self):
        for field in ["last_name", "first_name", "email", "password1", "password2"]:
            with self.subTest(field=field):
                response = self.client.post(self.url, self.registration_data(**{field: ""}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assert_no_registration()

    def test_invalid_email_is_rejected_without_consuming_invitation(self):
        response = self.client.post(self.url, self.registration_data(email="invalid-email"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assert_no_registration()

    def test_standard_weak_passwords_and_mismatch_are_rejected(self):
        for password in ["short", "12345678", "password"]:
            with self.subTest(password=password):
                response = self.client.post(
                    self.url, self.registration_data(password1=password, password2=password)
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("password1", response.context["form"].errors)
                self.assert_no_registration()
        response = self.client.post(self.url, self.registration_data(password2="does-not-match"))
        self.assertIn("password2", response.context["form"].errors)
        self.assert_no_registration()

    def test_password_validation_checks_the_candidate_email(self):
        email = "new.person@example.com"
        form = StaffInvitationRegistrationForm(
            self.registration_data(password1=email, password2=email)
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "password_too_similar",
            [error.code for error in form.errors.as_data()["password1"]],
        )
        self.assert_no_registration()

    def test_email_duplicates_are_case_insensitive(self):
        response = self.client.post(self.url, self.registration_data(email="EXISTING@EXAMPLE.COM"))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", EMAIL_EXISTS_ERROR)
        self.assert_no_registration()

    def test_email_cannot_shadow_a_legacy_username(self):
        self.existing_user.username = "legacy@example.com"
        self.existing_user.save(update_fields=["username"])

        response = self.client.post(self.url, self.registration_data(email="LEGACY@EXAMPLE.COM"))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", EMAIL_EXISTS_ERROR)
        self.assert_no_registration()

    def test_expired_revoked_used_and_unknown_links_cannot_register(self):
        for changes in [
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"revoked_at": timezone.now()},
            {"used_at": timezone.now(), "used_by": self.existing_user},
        ]:
            with self.subTest(changes=changes):
                invitation = StaffInvitation.objects.create(
                    store=self.store, created_by=self.manager, **changes
                )
                url = reverse("staff_invitation_accept", args=[invitation.token])
                for method in [self.client.get, self.client.post]:
                    response = method(url, self.registration_data() if method.__name__ == "post" else {})
                    self.assertEqual(response.status_code, 410)
                    self.assertTemplateUsed(response, "accounts/invitation_invalid.html")
                self.assertEqual(User.objects.count(), 2)
                self.assertEqual(StoreMembership.objects.count(), 1)
        unknown_url = reverse("staff_invitation_accept", args=[uuid4()])
        self.assertEqual(self.client.get(unknown_url).status_code, 404)
        self.assertEqual(self.client.post(unknown_url, self.registration_data()).status_code, 404)

    def test_invitation_is_unavailable_when_issuer_loses_manager_access(self):
        self.manager_membership.role = "staff"
        self.manager_membership.save(update_fields=["role"])

        self.assertEqual(self.client.get(self.url).status_code, 410)
        self.assertEqual(self.client.post(self.url, self.registration_data()).status_code, 410)
        self.assert_no_registration()

    def test_invitation_is_unavailable_when_issuer_is_inactive(self):
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])

        self.assertEqual(self.client.post(self.url, self.registration_data()).status_code, 410)
        self.assert_no_registration()

    def test_authenticated_user_cannot_register_and_can_return_after_post_logout(self):
        self.client.force_login(self.existing_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ログアウトして登録へ進む")
        self.assertContains(response, f'name="next" value="{self.url}"')
        self.assertNotContains(response, 'name="password1"')
        self.assertEqual(self.client.post(self.url, self.registration_data()).status_code, 409)
        self.assert_no_registration()

        response = self.client.post(reverse("logout"), {"next": self.url}, follow=True)

        self.assertRedirects(response, self.url)
        self.assertContains(response, 'name="password1"')
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assert_no_registration()

    def test_invitation_can_only_be_consumed_once(self):
        self.client.post(self.url, self.registration_data())
        registered = User.objects.get(email="new.person@example.com")
        self.client.logout()

        response = self.client.post(self.url, self.registration_data(email="another.person@example.com"))

        self.assertEqual(response.status_code, 410)
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(StoreMembership.objects.count(), 2)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.used_by_id, registered.pk)

    def test_usability_is_checked_again_inside_the_registration_transaction(self):
        with patch(
            "accounts.invitation_views.StaffInvitation.is_usable",
            new_callable=PropertyMock, side_effect=[True, False],
        ):
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 410)
        self.assert_no_registration()

    def test_failed_conditional_claim_does_not_create_a_user(self):
        with patch("accounts.invitation_views.StaffInvitation.objects.filter") as invitation_filter:
            invitation_filter.return_value.update.return_value = 0
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 410)
        self.assert_no_registration()

    def test_membership_creation_failure_rolls_back_user_and_invitation_claim(self):
        with patch(
            "accounts.invitation_views.StoreMembership.objects.create",
            side_effect=IntegrityError("simulated membership failure"),
        ):
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assert_no_registration()

    def test_final_invitation_save_failure_rolls_back_user_membership_and_claim(self):
        with patch(
            "accounts.invitation_views.StaffInvitation.save",
            side_effect=IntegrityError("simulated final invitation save failure"),
        ):
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 200)
        self.assert_no_registration()

    def test_identity_taken_after_validation_rejects_registration_without_consumption(self):
        with patch("accounts.invitation_views.email_is_in_use", return_value=True):
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", EMAIL_EXISTS_ERROR)
        self.assert_no_registration()

    def test_database_email_conflict_rolls_back_claim_and_returns_field_error(self):
        self.existing_user.email = "new.person@example.com"
        self.existing_user.save(update_fields=["email"])
        # 入力検証と保存の間に、同じメールの登録が先に完了した状態を再現する。
        with patch("accounts.forms.email_is_in_use", return_value=False), patch(
            "accounts.invitation_views.email_is_in_use", side_effect=[False, True]
        ):
            response = self.client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", EMAIL_EXISTS_ERROR)
        self.assert_no_registration()

    def test_csrf_is_required_for_registration(self):
        client = Client(enforce_csrf_checks=True)
        client.get(self.url)

        response = client.post(self.url, self.registration_data())

        self.assertEqual(response.status_code, 403)
        self.assert_no_registration()
        response = client.post(
            self.url, self.registration_data(),
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), 3)
