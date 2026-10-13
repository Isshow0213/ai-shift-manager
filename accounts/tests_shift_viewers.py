from datetime import date, time
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.messages import ERROR, get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape

from accounts.models import Company, CompanyMembership, Store, StoreMembership, StoreShiftViewingPermission
from shifts.models import Availability, Shift, ShiftDisplayOrder


class ViewerPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "form":
            self.forms.append(attributes)


class StoreShiftViewerAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.password = "viewer-test-password-2026"
        cls.company = Company.objects.create(name="AA会社")
        cls.other_company = Company.objects.create(name="BB会社")
        cls.store = Store.objects.create(company=cls.company, name="B本店")
        cls.other_store = Store.objects.create(company=cls.company, name="A分店")
        cls.foreign_store = Store.objects.create(company=cls.other_company, name="A他社店")
        cls.viewer = user_model.objects.create_user(
            username="shift_viewer", password=cls.password, email="viewer-private@example.com"
        )
        cls.company_membership = CompanyMembership.objects.create(
            user=cls.viewer, company=cls.company, role="owner"
        )
        cls.manager = user_model.objects.create_user(username="viewer_store_manager", password=cls.password)
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff = user_model.objects.create_user(
            username="viewer_store_staff", password=cls.password, last_name="田中", first_name="花子",
            email="staff-private@example.com", rank="A",
        )
        cls.staff_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="A"
        )
        coworker = user_model.objects.create_user(username="viewer_coworker", last_name="鈴木", first_name="太郎")
        cls.coworker_membership = StoreMembership.objects.create(
            user=coworker, store=cls.store, role="staff", rank="B"
        )
        cls.permission = StoreShiftViewingPermission.objects.create(
            company_membership=cls.company_membership, store=cls.store,
            is_active=True, created_by=cls.manager,
        )
        cls.work_date = date(2026, 11, 12)
        cls.note = '<script>alert("viewer-note")</script>ヘルプ先のメモ'
        cls.shift = Shift.objects.create(
            user=cls.staff, membership=cls.staff_membership, store=cls.store,
            work_date=cls.work_date, start_time=time(9), end_time=time(17),
            note=cls.note, display_color="green", is_generated=False,
        )
        Shift.objects.create(
            user=coworker, membership=cls.coworker_membership, store=cls.other_store,
            work_date=cls.work_date, start_time=time(8), end_time=time(22), note="別店舗の非公開シフト",
        )
        Shift.objects.create(
            user=cls.staff, membership=cls.staff_membership, store=cls.store,
            work_date=date(2026, 12, 1), start_time=time(8), end_time=time(22), note="翌月の確定シフト",
        )
        cls.legacy_availability = Availability.objects.create(
            user=cls.viewer, membership=None, work_date=cls.work_date,
            start_time=time(3, 11), end_time=time(4, 22),
        )
        Availability.objects.create(
            user=cls.staff, membership=cls.staff_membership, work_date=cls.work_date,
            start_time=time(5, 33), end_time=time(6, 44),
        )
        cls.ordered_users = [coworker.pk, cls.staff.pk, cls.manager.pk]
        ShiftDisplayOrder.objects.bulk_create([
            ShiftDisplayOrder(store=cls.store, user_id=user_id, position=position)
            for position, user_id in enumerate(cls.ordered_users)
        ])

    def setUp(self):
        self.client.force_login(self.viewer)
        localdate_patch = patch(
            "shifts.calendar_utils.timezone.localdate", return_value=date(2026, 10, 13)
        )
        localdate_patch.start()
        self.addCleanup(localdate_patch.stop)
        self.url = reverse("viewer_shift_overview")
        self.query = {"store": self.store.pk, "year": 2026, "month": 11}

    def test_viewer_can_read_only_the_granted_store_and_month(self):
        response = self.client.get(self.url, self.query)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["viewer_mode"])
        self.assertEqual(response.context["store"], self.store)
        self.assertEqual((response.context["year"], response.context["month"]), (2026, 11))
        self.assertEqual(response.context["total_shifts"], 1)
        self.assertEqual([row["user"].pk for row in response.context["rows"]], self.ordered_users)
        self.assertNotIn(self.viewer.pk, [row["user"].pk for row in response.context["rows"]])
        self.assertContains(response, "09:00")
        self.assertContains(response, "17:00")
        self.assertContains(response, escape(self.note))
        self.assertContains(response, "shift-color-green")
        self.assertNotContains(response, self.note)
        self.assertNotContains(response, "別店舗の非公開シフト")
        self.assertNotContains(response, "翌月の確定シフト")

    def test_viewer_page_has_no_private_wishes_email_rank_or_edit_controls(self):
        response = self.client.get(self.url, self.query)
        parser = ViewerPageParser()
        parser.feed(response.content.decode())
        forbidden = {
            reverse("availability_list"), reverse("availability_create"), reverse("shift_list"),
            reverse("manager_shift_list"), reverse("manager_shift_order_update"),
            reverse("manager_shift_edit", args=[self.shift.pk]),
            reverse("manager_shift_delete", args=[self.shift.pk]),
        }

        self.assertFalse(forbidden.intersection({urlsplit(link).path for link in parser.links}))
        self.assertTrue(all(
            form.get("method", "get").lower() == "get"
            or urlsplit(form.get("action", "")).path == reverse("logout")
            for form in parser.forms
        ))
        for private_value in ["03:11", "04:22", "05:33", "06:44", "staff-private@example.com", "ランク"]:
            with self.subTest(private_value=private_value):
                self.assertNotContains(response, private_value)
        self.assertNotContains(response, "data-shift-order-form")

    def test_missing_store_selects_first_allowed_store_by_company_and_store_name(self):
        StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=self.other_store,
            created_by=self.manager, is_active=True,
        )
        other_company_membership = CompanyMembership.objects.create(
            user=self.viewer, company=self.other_company, role="member"
        )
        StoreShiftViewingPermission.objects.create(
            company_membership=other_company_membership, store=self.foreign_store,
            created_by=self.manager, is_active=True,
        )

        response = self.client.get(self.url, {"year": 2026, "month": 11})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.other_store)
        self.assertEqual(list(response.context["accessible_stores"]), [self.other_store, self.store, self.foreign_store])

    def test_invalid_ungranted_and_other_company_store_ids_are_404(self):
        for store_id in ["not-an-id", "", 999999, self.other_store.pk, self.foreign_store.pk, "9" * 500]:
            with self.subTest(store_id=store_id):
                response = self.client.get(self.url, {**self.query, "store": store_id})
                self.assertEqual(response.status_code, 404)

    def test_cross_company_permission_is_rejected_by_model_validation(self):
        invalid_permission = StoreShiftViewingPermission(
            company_membership=self.company_membership, store=self.foreign_store,
            created_by=self.manager, is_active=True,
        )

        with self.assertRaises(ValidationError):
            invalid_permission.full_clean()

    def test_legacy_cross_company_permission_does_not_grant_access(self):
        StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=self.foreign_store,
            created_by=self.manager, is_active=True,
        )

        response = self.client.get(self.url, {**self.query, "store": self.foreign_store.pk})

        self.assertEqual(response.status_code, 404)
        default_response = self.client.get(self.url, {"year": 2026, "month": 11})
        self.assertEqual(list(default_response.context["accessible_stores"]), [self.store])

    def test_revoked_permission_is_not_available_and_no_permissions_returns_403(self):
        self.permission.is_active = False
        self.permission.save(update_fields=["is_active"])

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url, self.query).status_code, 404)

    def test_permission_for_another_company_member_is_not_inherited(self):
        other_user = get_user_model().objects.create_user(username="viewer_unauthorized_company_owner")
        CompanyMembership.objects.create(user=other_user, company=self.company, role="owner")
        self.client.force_login(other_user)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url, self.query).status_code, 404)

    def test_viewer_get_does_not_write_and_post_is_rejected(self):
        before = list(StoreShiftViewingPermission.objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, self.query)
            self.assertEqual(response.status_code, 200)

        self.assertEqual([query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        )], [])
        self.assertEqual(self.client.post(self.url, self.query).status_code, 405)
        self.assertEqual(list(StoreShiftViewingPermission.objects.order_by("pk").values()), before)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def test_month_navigation_retains_the_authorized_store(self):
        response = self.client.get(self.url, {**self.query, "year": 2027, "month": 1})
        parser = ViewerPageParser()
        parser.feed(response.content.decode())
        queries = [parse_qs(urlsplit(link).query) for link in parser.links]

        self.assertTrue(any(query == {"store": [str(self.store.pk)], "year": ["2026"], "month": ["12"]} for query in queries))
        self.assertTrue(any(query == {"store": [str(self.store.pk)], "year": ["2027"], "month": ["2"]} for query in queries))

    def test_viewer_only_login_and_index_default_to_viewer_overview(self):
        self.client.logout()

        response = self.client.post(reverse("login"), {"username": self.viewer.username, "password": self.password})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("index")), self.url, fetch_redirect_response=False)

    def test_viewer_only_login_next_cannot_open_personal_views(self):
        targets = [
            reverse("availability_list"), reverse("availability_create"), reverse("shift_list"),
            reverse("availability_delete", args=[self.legacy_availability.pk]),
        ]
        for target in targets:
            with self.subTest(target=target):
                self.client.logout()
                response = self.client.post(reverse("login"), {
                    "username": self.viewer.username, "password": self.password, "next": target,
                })
                self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_viewer_only_direct_personal_gets_and_posts_redirect_without_mutation(self):
        targets = [
            reverse("availability_list"), reverse("availability_create"), reverse("shift_list"),
            reverse("availability_delete", args=[self.legacy_availability.pk]),
        ]
        before = list(Availability.objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())
        for target in targets:
            for method in [self.client.get, self.client.post]:
                with self.subTest(target=target, method=method.__name__):
                    response = method(target, {
                        "work_date": "2026-11-13", "start_time": "10:00", "end_time": "11:00",
                        "membership": self.staff_membership.pk,
                    })
                    self.assertRedirects(response, self.url, fetch_redirect_response=False)
                    self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
                    self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def test_last_revoked_grant_keeps_login_next_and_old_personal_records_locked(self):
        self.permission.is_active = False
        self.permission.save(update_fields=["is_active"])
        Shift.objects.create(
            user=self.viewer, membership=None, store=self.store, work_date=self.work_date,
            start_time=time(7), end_time=time(8), note="閲覧許可前の本人シフト",
        )
        targets = [
            reverse("availability_list"), reverse("availability_create"), reverse("shift_list"),
            reverse("availability_delete", args=[self.legacy_availability.pk]),
        ]
        for next_url in [None, *targets]:
            with self.subTest(next_url=next_url):
                self.client.logout()
                data = {"username": self.viewer.username, "password": self.password}
                if next_url is not None:
                    data["next"] = next_url
                response = self.client.post(reverse("login"), data)
                self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertRedirects(self.client.get(reverse("index")), self.url, fetch_redirect_response=False)
        availabilities_before = list(Availability.objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())
        for target in targets:
            for method in [self.client.get, self.client.post]:
                with self.subTest(target=target, method=method.__name__):
                    response = method(target, {
                        "work_date": "2026-11-13", "start_time": "10:00", "end_time": "11:00",
                    })
                    self.assertRedirects(response, self.url, fetch_redirect_response=False)
                    self.assertEqual(list(Availability.objects.order_by("pk").values()), availabilities_before)
                    self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def test_inactive_store_membership_does_not_restore_personal_access_after_revoke(self):
        StoreMembership.objects.create(
            user=self.viewer, store=self.store, role="staff", is_active=False
        )
        self.permission.is_active = False
        self.permission.save(update_fields=["is_active"])
        before = list(Availability.objects.order_by("pk").values())

        response = self.client.post(reverse("availability_delete", args=[self.legacy_availability.pk]))

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), before)
        self.assertRedirects(
            self.client.get(reverse("shift_list")), self.url, fetch_redirect_response=False
        )

    def test_company_owner_viewer_has_no_manager_operation_permissions(self):
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())
        response = self.client.post(reverse("manager_staff_rank_update", args=[self.staff_membership.pk]), {"rank": "C"})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)
        response = self.client.post(reverse("manager_shift_edit", args=[self.shift.pk]), {
            "membership": self.staff_membership.pk, "work_date": "2026-11-12",
            "start_time": "10:00", "end_time": "12:00",
        })
        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def grant_to_staff(self):
        company_membership = CompanyMembership.objects.create(user=self.staff, company=self.company, role="member")
        return StoreShiftViewingPermission.objects.create(
            company_membership=company_membership, store=self.other_store,
            created_by=self.manager, is_active=True,
        )

    def test_existing_staff_login_and_personal_submission_remain_independent(self):
        self.grant_to_staff()
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": self.staff.username, "password": self.password})
        self.assertRedirects(response, reverse("availability_list"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("availability_list"), self.query).status_code, 200)

        response = self.client.post(reverse("availability_create"), {
            "work_date": "2026-11-13", "start_time": "10:00", "end_time": "11:00",
        })

        self.assertEqual(response.status_code, 302)
        submitted = Availability.objects.get(user=self.staff, work_date=date(2026, 11, 13))
        self.assertEqual(submitted.membership, self.staff_membership)
        self.assertEqual(self.client.get(self.url, {**self.query, "store": self.other_store.pk}).status_code, 200)
        self.assertEqual(self.client.get(self.url, self.query).status_code, 404)

    def test_staff_keep_personal_views_and_submission_when_their_viewing_grant_is_revoked(self):
        permission = self.grant_to_staff()
        permission.is_active = False
        permission.save(update_fields=["is_active"])
        self.client.logout()

        response = self.client.post(reverse("login"), {
            "username": self.staff.username, "password": self.password,
            "next": reverse("availability_list"),
        })

        self.assertRedirects(response, reverse("availability_list"), fetch_redirect_response=False)
        for name in ["availability_list", "availability_create", "shift_list", "store_shift_overview"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name), {"year": 2026, "month": 11}).status_code, 200)
        self.assertEqual(self.client.get(self.url, {**self.query, "store": self.other_store.pk}).status_code, 404)
        response = self.client.post(reverse("availability_create"), {
            "work_date": "2026-11-13", "start_time": "10:00", "end_time": "11:00",
        })
        self.assertEqual(response.status_code, 302)
        submitted = Availability.objects.get(user=self.staff, work_date=date(2026, 11, 13))
        self.assertEqual(submitted.membership, self.staff_membership)
        response = self.client.post(reverse("availability_delete", args=[submitted.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Availability.objects.filter(pk=submitted.pk).exists())

    def test_viewing_grant_does_not_allow_deleting_wishes_in_that_store(self):
        self.grant_to_staff()
        inactive_membership = StoreMembership.objects.create(
            user=self.staff, store=self.other_store, role="staff", is_active=False
        )
        availability = Availability.objects.create(
            user=self.staff, membership=inactive_membership, work_date=date(2026, 11, 13),
            start_time=time(10), end_time=time(11),
        )
        self.client.force_login(self.staff)

        response = self.client.post(reverse("availability_delete", args=[availability.pk]))

        self.assertRedirects(response, (
            f"{reverse('availability_list')}?year=2026&month=11&date=2026-11-13"
        ), fetch_redirect_response=False)
        self.assertTrue(any(message.level == ERROR for message in get_messages(response.wsgi_request)))
        self.assertTrue(Availability.objects.filter(pk=availability.pk).exists())

    def test_manager_login_destination_still_has_priority_over_a_viewing_grant(self):
        company_membership = CompanyMembership.objects.create(user=self.manager, company=self.company, role="member")
        StoreShiftViewingPermission.objects.create(
            company_membership=company_membership, store=self.other_store, created_by=self.manager,
        )
        self.client.logout()

        response = self.client.post(reverse("login"), {"username": self.manager.username, "password": self.password})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)

    def test_anonymous_and_disabled_users_cannot_open_viewer_overview(self):
        self.client.logout()
        response = self.client.get(self.url, self.query)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))
        self.viewer.is_active = False
        self.viewer.save(update_fields=["is_active"])
        self.client.force_login(self.viewer)
        response = self.client.get(self.url, self.query)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))

    def test_permission_is_unique_per_company_member_and_store(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreShiftViewingPermission.objects.create(
                company_membership=self.company_membership, store=self.store, created_by=self.manager,
            )

        StoreShiftViewingPermission.objects.create(
            company_membership=self.company_membership, store=self.other_store, created_by=self.manager,
        )
        self.assertEqual(StoreShiftViewingPermission.objects.filter(company_membership=self.company_membership).count(), 2)
