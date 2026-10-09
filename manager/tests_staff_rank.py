from datetime import date, time
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from shifts.models import Shift


class RankFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current_form = None
        self.current_select = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current_form = {**attributes, "inputs": [], "selects": []}
            self.forms.append(self.current_form)
        elif self.current_form is not None:
            if tag == "input":
                self.current_form["inputs"].append(attributes)
            elif tag == "select":
                self.current_select = {**attributes, "options": []}
                self.current_form["selects"].append(self.current_select)
            elif tag == "option" and self.current_select is not None:
                self.current_select["options"].append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None
            self.current_select = None
        elif tag == "select":
            self.current_select = None


class StaffRankUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = User.objects.create_user(
            username="rank_manager", last_name="柴田", first_name="一翔", rank="A"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager", rank="C"
        )
        cls.staff = User.objects.create_user(
            username="rank_staff", last_name="田中", first_name="花子", rank="C"
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="B",
            desired_shifts_per_week=3,
        )
        cls.other_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="manager", rank="A",
            desired_shifts_per_week=5, is_active=False,
        )
        cls.inactive_staff = User.objects.create_user(
            username="rank_inactive_staff", last_name="鈴木", first_name="太郎", rank="B"
        )
        cls.inactive_membership = StoreMembership.objects.create(
            user=cls.inactive_staff, store=cls.store, role="staff", rank="C", is_active=False
        )
        cls.shift = Shift.objects.create(
            user=cls.staff, membership=cls.membership, store=cls.store,
            work_date=date(2030, 1, 10), start_time=time(9), end_time=time(17),
            is_generated=True,
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.list_url = reverse("manager_staff_list")
        self.url = self.rank_url(self.membership)

    def rank_url(self, membership):
        return reverse("manager_staff_rank_update", args=[membership.pk])

    def membership_snapshot(self):
        return list(StoreMembership.objects.order_by("pk").values())

    def test_list_contains_individual_forms_with_current_rank_and_csrf(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        memberships = list(response.context["memberships"])
        self.assertCountEqual(
            [membership.pk for membership in memberships],
            [self.manager_membership.pk, self.membership.pk, self.inactive_membership.pk],
        )
        parser = RankFormParser()
        parser.feed(response.content.decode())
        forms_by_action = {form.get("action"): form for form in parser.forms}
        for membership in memberships:
            with self.subTest(membership=membership.pk):
                self.assertEqual(membership.rank_form["rank"].value(), membership.rank)
                form = forms_by_action[self.rank_url(membership)]
                self.assertEqual(form["method"].lower(), "post")
                self.assertTrue(any(
                    field.get("name") == "csrfmiddlewaretoken" and field.get("value")
                    for field in form["inputs"]
                ))
                rank_select = next(select for select in form["selects"] if select.get("name") == "rank")
                self.assertEqual(
                    {option.get("value") for option in rank_select["options"] if option.get("value")},
                    {"A", "B", "C"},
                )
                self.assertEqual(
                    [option.get("value") for option in rank_select["options"] if "selected" in option],
                    [membership.rank],
                )
        self.assertNotIn(self.rank_url(self.other_membership), forms_by_action)

    def test_all_rank_choices_are_saved_and_success_returns_to_list(self):
        for rank in ["A", "B", "C"]:
            with self.subTest(rank=rank):
                response = self.client.post(self.url, {"rank": rank}, follow=True)

                self.assertRedirects(response, self.list_url)
                self.membership.refresh_from_db()
                self.assertEqual(self.membership.rank, rank)
                self.assertContains(response, f"田中 花子さんのランクを{rank}に変更しました。")

    def test_only_target_membership_rank_changes_and_posted_identity_fields_are_ignored(self):
        before_memberships = self.membership_snapshot()
        before_users = list(get_user_model().objects.order_by("pk").values())
        before_shifts = list(Shift.objects.order_by("pk").values())

        response = self.client.post(self.url, {
            "rank": "A", "store": self.other_store.pk, "user": self.manager.pk,
            "role": "manager", "is_active": "", "desired_shifts_per_week": 99,
            "membership": self.other_membership.pk,
        })

        self.assertRedirects(response, self.list_url)
        expected_memberships = [
            {**row, "rank": "A"} if row["id"] == self.membership.pk else row
            for row in before_memberships
        ]
        self.assertEqual(self.membership_snapshot(), expected_memberships)
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), before_users)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before_shifts)

    def test_manager_can_change_own_store_membership_rank(self):
        response = self.client.post(self.rank_url(self.manager_membership), {"rank": "B"})

        self.assertRedirects(response, self.list_url)
        self.manager_membership.refresh_from_db()
        self.assertEqual(self.manager_membership.rank, "B")
        self.assertEqual(self.manager_membership.role, "manager")

    def test_inactive_store_membership_rank_can_be_changed_without_reactivation(self):
        response = self.client.post(self.rank_url(self.inactive_membership), {"rank": "A"})

        self.assertRedirects(response, self.list_url)
        self.inactive_membership.refresh_from_db()
        self.assertEqual(self.inactive_membership.rank, "A")
        self.assertFalse(self.inactive_membership.is_active)

    def test_invalid_empty_and_missing_rank_show_error_on_only_the_target_row(self):
        before = self.membership_snapshot()
        for data in [{"rank": "X"}, {"rank": "a"}, {"rank": ""}, {}]:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "manager/staff_list.html")
                rows = {membership.pk: membership for membership in response.context["memberships"]}
                self.assertTrue(rows[self.membership.pk].rank_form.is_bound)
                self.assertIn("rank", rows[self.membership.pk].rank_form.errors)
                for pk, membership in rows.items():
                    if pk != self.membership.pk:
                        self.assertFalse(membership.rank_form.errors)
                self.assertEqual(self.membership_snapshot(), before)

    def test_other_store_and_nonexistent_memberships_return_404_without_changes(self):
        before = self.membership_snapshot()
        nonexistent_pk = StoreMembership.objects.order_by("-pk").first().pk + 1
        for url in [
            self.rank_url(self.other_membership),
            reverse("manager_staff_rank_update", args=[nonexistent_pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"rank": "C"}).status_code, 404)
                self.assertEqual(self.membership_snapshot(), before)

    def test_multiple_manager_stores_still_use_first_active_manager_membership(self):
        StoreMembership.objects.create(user=self.manager, store=self.other_store, role="manager")

        response = self.client.post(self.rank_url(self.other_membership), {"rank": "C"})

        self.assertEqual(response.status_code, 404)
        self.other_membership.refresh_from_db()
        self.assertEqual(self.other_membership.rank, "A")

    def test_staff_cannot_update_rank(self):
        before = self.membership_snapshot()
        self.client.force_login(self.staff)

        response = self.client.post(self.url, {"rank": "A"})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.membership_snapshot(), before)

    def test_inactive_manager_cannot_update_rank(self):
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        before = self.membership_snapshot()

        response = self.client.post(self.url, {"rank": "A"})

        self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.membership_snapshot(), before)

    def test_anonymous_user_is_redirected_to_login(self):
        before = self.membership_snapshot()
        self.client.logout()

        response = self.client.post(self.url, {"rank": "A"})

        self.assertRedirects(
            response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )
        self.assertEqual(self.membership_snapshot(), before)

    def test_get_and_put_are_rejected_without_changing_rank(self):
        before = self.membership_snapshot()
        for method in [self.client.get, self.client.put]:
            with self.subTest(method=method.__name__):
                self.assertEqual(method(self.url).status_code, 405)
                self.assertEqual(self.membership_snapshot(), before)

    def test_csrf_is_required_and_valid_csrf_allows_rank_update(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        client.get(self.list_url)

        response = client.post(self.url, {"rank": "A"})

        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.rank, "B")
        response = client.post(
            self.url, {"rank": "A"}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
        )
        self.assertRedirects(response, self.list_url)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.rank, "A")
