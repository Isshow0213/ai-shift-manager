from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.messages import ERROR, get_messages
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Shift, ShiftDisplayOrder


class ShiftDisplayOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="表示順テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(
            username="order_manager", last_name="ZZ", first_name="管理者"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.other_manager = user_model.objects.create_user(
            username="order_other_manager", last_name="ZZ", first_name="別店管理者"
        )
        StoreMembership.objects.create(
            user=cls.other_manager, store=cls.other_store, role="manager"
        )
        cls.staff = []
        cls.memberships = []
        for label in ["AA", "BB", "CC"]:
            user = user_model.objects.create_user(
                username=f"order_{label.lower()}", last_name=label, first_name="従業員"
            )
            cls.staff.append(user)
            cls.memberships.append(StoreMembership.objects.create(
                user=user, store=cls.store, role="staff"
            ))
        cls.foreign_staff = user_model.objects.create_user(
            username="order_foreign", last_name="DD", first_name="別店舗専属"
        )
        cls.foreign_membership = StoreMembership.objects.create(
            user=cls.foreign_staff, store=cls.other_store, role="staff"
        )
        cls.shared_membership = StoreMembership.objects.create(
            user=cls.staff[0], store=cls.other_store, role="staff"
        )
        cls.work_date = date(2026, 11, 12)

    def setUp(self):
        self.client.force_login(self.manager)
        self.overview_url = self.overview_for()
        self.update_url = self.update_for()
        self.default_user_ids = [user.pk for user in self.staff] + [self.manager.pk]

    def overview_for(self, year=2026, month=11, staff=False):
        name = "store_shift_overview" if staff else "manager_shift_overview"
        return f"{reverse(name)}?year={year}&month={month}"

    def update_for(self, year=2026, month=11):
        return f"{reverse('manager_shift_order_update')}?year={year}&month={month}"

    def row_ids(self, response):
        self.assertEqual(response.status_code, 200)
        return [row["user"].pk for row in response.context["rows"]]

    def saved_ids(self, store=None):
        return list(ShiftDisplayOrder.objects.filter(
            store=store or self.store
        ).order_by("position", "user_id").values_list("user_id", flat=True))

    def save_order(self, user_ids, **extra):
        return self.client.post(self.update_url, {"user_ids": user_ids, **extra})

    def seed_order(self, user_ids, store=None):
        ShiftDisplayOrder.objects.bulk_create([
            ShiftDisplayOrder(store=store or self.store, user_id=user_id, position=position)
            for position, user_id in enumerate(user_ids)
        ])

    def make_shift(self, user, membership=None, store=None, work_date=None):
        return Shift.objects.create(
            user=user, membership=membership, store=store or self.store,
            work_date=work_date or self.work_date,
            start_time=time(9), end_time=time(17), note="並べ替えでも保持するメモ",
            display_color="green", is_generated=False,
        )

    def assert_invalid_order(self, response, before):
        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertTrue(any(message.level == ERROR for message in get_messages(response.wsgi_request)))
        self.assertEqual(
            list(ShiftDisplayOrder.objects.order_by("pk").values()), before
        )

    def test_unconfigured_order_preserves_previous_name_order(self):
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), self.default_user_ids)
        self.assertFalse(ShiftDisplayOrder.objects.exists())

    def test_valid_complete_permutation_is_saved_and_rendered(self):
        desired = [self.staff[2].pk, self.staff[0].pk, self.manager.pk, self.staff[1].pk]

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(), desired)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)
        self.assertEqual(ShiftDisplayOrder.objects.filter(store=self.store).count(), len(desired))

    def test_saved_order_is_shared_with_staff_and_other_months(self):
        desired = list(reversed(self.default_user_ids))
        self.save_order(desired)

        for year, month in [(2026, 11), (2026, 12), (2027, 1)]:
            with self.subTest(view="manager", year=year, month=month):
                self.assertEqual(self.row_ids(self.client.get(self.overview_for(year, month))), desired)
        self.client.force_login(self.staff[0])
        for year, month in [(2026, 11), (2026, 12), (2027, 1)]:
            with self.subTest(view="staff", year=year, month=month):
                self.assertEqual(self.row_ids(self.client.get(self.overview_for(year, month, staff=True))), desired)

    def test_later_reordering_replaces_the_order_without_duplicate_records(self):
        first = list(reversed(self.default_user_ids))
        second = [self.staff[1].pk, self.manager.pk, self.staff[0].pk, self.staff[2].pk]
        self.save_order(first)

        response = self.save_order(second)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(), second)
        self.assertEqual(ShiftDisplayOrder.objects.filter(store=self.store).count(), len(second))

    def test_new_staff_are_appended_after_saved_people_in_name_order(self):
        desired = list(reversed(self.default_user_ids))
        self.save_order(desired)
        new_ids = []
        for username, last_name in [("order_new_b", "AB"), ("order_new_a", "AAA")]:
            user = get_user_model().objects.create_user(username=username, last_name=last_name)
            StoreMembership.objects.create(user=user, store=self.store, role="staff")
            new_ids.append(user.pk)
        before = list(ShiftDisplayOrder.objects.order_by("pk").values())

        response = self.client.get(self.overview_url)

        self.assertEqual(self.row_ids(response), desired + list(reversed(new_ids)))
        self.assertEqual(list(ShiftDisplayOrder.objects.order_by("pk").values()), before)

    def test_store_orders_are_independent_even_for_a_shared_employee(self):
        desired = list(reversed(self.default_user_ids))
        self.save_order(desired)
        other_default = [self.staff[0].pk, self.foreign_staff.pk, self.other_manager.pk]
        self.client.force_login(self.other_manager)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), other_default)
        other_desired = list(reversed(other_default))

        response = self.save_order(other_desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(self.other_store), other_desired)
        self.assertEqual(self.saved_ids(self.store), desired)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), other_desired)
        self.client.force_login(self.manager)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)

    def test_posted_store_id_cannot_select_another_managers_store(self):
        other_saved = [self.other_manager.pk, self.foreign_staff.pk, self.staff[0].pk]
        self.seed_order(other_saved, store=self.other_store)
        desired = list(reversed(self.default_user_ids))

        response = self.save_order(desired, store=self.other_store.pk)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(self.store), desired)
        self.assertEqual(self.saved_ids(self.other_store), other_saved)

    def test_invalid_duplicate_missing_foreign_and_malformed_ids_save_nothing(self):
        self.seed_order(list(reversed(self.default_user_ids)))
        before = list(ShiftDisplayOrder.objects.order_by("pk").values())
        invalid_orders = [
            [],
            self.default_user_ids[:-1],
            self.default_user_ids + [self.default_user_ids[0]],
            [self.default_user_ids[0]] * len(self.default_user_ids),
            self.default_user_ids[:-1] + [self.foreign_staff.pk],
            self.default_user_ids + [self.foreign_staff.pk],
            self.default_user_ids[:-1] + ["invalid"],
            self.default_user_ids[:-1] + [""],
            self.default_user_ids[:-1] + ["999999999999999999999999"],
        ]
        for user_ids in invalid_orders:
            with self.subTest(user_ids=user_ids):
                self.assert_invalid_order(self.save_order(user_ids), before)

    def test_visible_people_with_inactive_or_missing_memberships_can_be_reordered(self):
        inactive = get_user_model().objects.create_user(
            username="order_visible_inactive", last_name="AB"
        )
        inactive_membership = StoreMembership.objects.create(
            user=inactive, store=self.store, role="staff", is_active=False
        )
        legacy = get_user_model().objects.create_user(username="order_visible_legacy", last_name="BC")
        self.make_shift(inactive, membership=inactive_membership)
        self.make_shift(legacy)
        desired = [legacy.pk, self.manager.pk, inactive.pk] + [user.pk for user in reversed(self.staff)]

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(), desired)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)
        self.assertEqual(
            self.row_ids(self.client.get(self.overview_for(month=12))),
            [user_id for user_id in desired if user_id not in [inactive.pk, legacy.pk]],
        )

    def make_hidden_people(self):
        inactive = get_user_model().objects.create_user(username="order_hidden_inactive", last_name="AB")
        StoreMembership.objects.create(user=inactive, store=self.store, role="staff", is_active=False)
        legacy = get_user_model().objects.create_user(username="order_hidden_legacy", last_name="BC")
        self.make_shift(legacy, work_date=date(2026, 10, 12))
        return inactive, legacy

    def test_reordering_visible_rows_preserves_saved_hidden_people_in_their_slots(self):
        inactive, legacy = self.make_hidden_people()
        original = [self.staff[0].pk, inactive.pk, self.staff[1].pk, legacy.pk, self.staff[2].pk, self.manager.pk]
        self.seed_order(original)
        desired = list(reversed(self.default_user_ids))

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(
            self.saved_ids(),
            [self.manager.pk, inactive.pk, self.staff[2].pk, legacy.pk, self.staff[1].pk, self.staff[0].pk],
        )
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)
        self.assertEqual(
            self.row_ids(self.client.get(self.overview_for(month=10))),
            [self.manager.pk, self.staff[2].pk, legacy.pk, self.staff[1].pk, self.staff[0].pk],
        )

    def test_first_save_also_preserves_unconfigured_hidden_people_in_name_sorted_slots(self):
        inactive, legacy = self.make_hidden_people()
        desired = list(reversed(self.default_user_ids))

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(
            self.saved_ids(),
            [self.manager.pk, inactive.pk, self.staff[2].pk, legacy.pk, self.staff[1].pk, self.staff[0].pk],
        )

    def test_hidden_person_with_only_a_saved_order_keeps_their_position(self):
        former_staff = get_user_model().objects.create_user(
            username="order_saved_only", last_name="AB"
        )
        self.seed_order([
            self.staff[0].pk, former_staff.pk, self.staff[1].pk,
            self.staff[2].pk, self.manager.pk,
        ])
        desired = list(reversed(self.default_user_ids))

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(), [
            self.manager.pk, former_staff.pk, self.staff[2].pk, self.staff[1].pk, self.staff[0].pk,
        ])
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)

    def test_hidden_id_cannot_be_injected_into_a_months_visible_order(self):
        inactive, legacy = self.make_hidden_people()
        before = list(ShiftDisplayOrder.objects.order_by("pk").values())
        for hidden_user in [inactive, legacy]:
            with self.subTest(hidden_user=hidden_user.pk):
                self.assert_invalid_order(
                    self.save_order(self.default_user_ids + [hidden_user.pk]), before
                )

    def test_inactive_user_with_active_membership_remains_a_valid_visible_row(self):
        disabled = get_user_model().objects.create_user(
            username="order_disabled", last_name="DD", is_active=False
        )
        StoreMembership.objects.create(user=disabled, store=self.store, role="staff")
        desired = [disabled.pk] + list(reversed(self.default_user_ids))

        response = self.save_order(desired)

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.row_ids(self.client.get(self.overview_url)), desired)

    def test_order_changes_do_not_modify_shifts_or_memberships(self):
        self.make_shift(self.staff[0], membership=self.memberships[0])
        shifts_before = list(Shift.objects.order_by("pk").values())
        memberships_before = list(StoreMembership.objects.order_by("pk").values())

        response = self.save_order(list(reversed(self.default_user_ids)))

        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)

    def test_manager_and_staff_overview_gets_do_not_write_to_the_database(self):
        self.seed_order(list(reversed(self.default_user_ids)))
        for user, url in [
            (self.manager, self.overview_url),
            (self.staff[0], self.overview_for(staff=True)),
        ]:
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url)
                    self.row_ids(response)
                writes = [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE")
                )]
                self.assertEqual(writes, [])

    def test_update_endpoint_rejects_get_without_changing_the_order(self):
        self.seed_order(self.default_user_ids)
        before = list(ShiftDisplayOrder.objects.order_by("pk").values())

        response = self.client.get(self.update_url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(list(ShiftDisplayOrder.objects.order_by("pk").values()), before)

    def test_staff_cannot_save_an_order(self):
        self.seed_order(self.default_user_ids)
        before = list(ShiftDisplayOrder.objects.order_by("pk").values())
        self.client.force_login(self.staff[0])

        response = self.save_order(list(reversed(self.default_user_ids)))

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(list(ShiftDisplayOrder.objects.order_by("pk").values()), before)

    def test_anonymous_user_cannot_save_an_order(self):
        self.client.logout()

        response = self.save_order(list(reversed(self.default_user_ids)))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
        self.assertFalse(ShiftDisplayOrder.objects.exists())

    def test_order_update_requires_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        desired = list(reversed(self.default_user_ids))

        response = csrf_client.post(self.update_url, {"user_ids": desired})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ShiftDisplayOrder.objects.exists())
        csrf_client.get(self.overview_url)
        response = csrf_client.post(self.update_url, {
            "user_ids": desired,
            "csrfmiddlewaretoken": csrf_client.cookies["csrftoken"].value,
        })
        self.assertRedirects(response, self.overview_url, fetch_redirect_response=False)
        self.assertEqual(self.saved_ids(), desired)

    def test_user_can_have_one_saved_position_per_store(self):
        ShiftDisplayOrder.objects.create(store=self.store, user=self.staff[0], position=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftDisplayOrder.objects.create(store=self.store, user=self.staff[0], position=1)

        ShiftDisplayOrder.objects.create(store=self.other_store, user=self.staff[0], position=0)

        self.assertEqual(ShiftDisplayOrder.objects.filter(user=self.staff[0]).count(), 2)
