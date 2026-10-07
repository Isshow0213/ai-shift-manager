from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership


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
