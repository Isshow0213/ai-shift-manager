from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse


class EmailLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "shift-test-password-2026"
        cls.user = get_user_model().objects.create_user(
            username="existing_username", email="Staff@example.com", password=cls.password,
        )

    def test_login_supports_email_case_insensitively_and_existing_username(self):
        for identifier in ["existing_username", "Staff@example.com", "STAFF@EXAMPLE.COM"]:
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self.client.post(reverse("login"), {"username": identifier, "password": self.password})
                self.assertRedirects(response, reverse("availability_list"))
                self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_incorrect_password_and_inactive_users_cannot_login(self):
        self.assertIsNone(authenticate(username=self.user.email, password="wrong-password"))
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertIsNone(authenticate(username=self.user.email, password=self.password))

    def test_long_email_address_does_not_require_a_long_username(self):
        email = "staff@" + ".".join(["a" * 55] * 3) + ".com"
        user = get_user_model().objects.create_user(
            username="generated_internal_username", email=email, password=self.password,
        )
        response = self.client.post(reverse("login"), {"username": email, "password": self.password})
        self.assertRedirects(response, reverse("availability_list"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_email_uniqueness_is_enforced_for_registration_races(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            get_user_model().objects.create_user(username="other_user", email="STAFF@EXAMPLE.COM")
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_legacy_users_can_have_empty_email_addresses(self):
        get_user_model().objects.create_user(username="without_email_one")
        get_user_model().objects.create_user(username="without_email_two")
        self.assertEqual(get_user_model().objects.filter(email="").count(), 2)

    def test_existing_authenticated_session_backend_remains_valid(self):
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("availability_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.user, self.user)
