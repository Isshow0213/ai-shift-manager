from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership

from .models import Requirement, Shift
from .requirement_bulk import apply_plan, build_plan, day_type
from .requirement_bulk_forms import BulkRequirementForm, RequirementTimeSlotFormSet


def slot(start=time(9), end=time(10), count=3):
    return {"start_time": start, "end_time": end, "required_staff_count": count}


def bulk_data(**overrides):
    data = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-04",
        "weekend_policy": "sat_sun",
        "mode": "update",
        "categories": ["weekday"],
        "action": "apply",
        "weekday-TOTAL_FORMS": "1",
        "weekday-INITIAL_FORMS": "0",
        "weekday-0-start_time": "09:00",
        "weekday-0-end_time": "10:00",
        "weekday-0-required_staff_count": "3",
    }
    data.update(overrides)
    return data


class RequirementDayClassificationTests(SimpleTestCase):
    def test_substitute_and_citizens_holidays_are_days_off(self):
        # 国立天文台の2026年暦要項に記載された休日。
        # https://eco.mtk.nao.ac.jp/koyomi/yoko/2026/rekiyou261.html
        for policy in ["sat_sun", "sun"]:
            for work_date in [date(2026, 5, 6), date(2026, 9, 22)]:
                with self.subTest(policy=policy, work_date=work_date):
                    self.assertEqual(day_type(work_date, policy), "holiday")
        self.assertEqual(day_type(date(2026, 5, 5)), "holiday")

    def test_saturday_policy_changes_friday_and_saturday_classification(self):
        for work_date, sat_sun, sun in [
            (date(2026, 10, 2), "holiday_eve", "weekday"),
            (date(2026, 10, 3), "holiday", "holiday_eve"),
            (date(2026, 10, 4), "holiday", "holiday"),
        ]:
            with self.subTest(work_date=work_date):
                self.assertEqual(day_type(work_date, "sat_sun"), sat_sun)
                self.assertEqual(day_type(work_date, "sun"), sun)

    def test_year_boundary_plan_includes_new_year_holiday_and_its_eve(self):
        plan = build_plan(
            {
                "start_date": date(2026, 12, 31),
                "end_date": date(2027, 1, 1),
                "weekend_policy": "sun",
                "categories": ["weekday", "holiday_eve", "holiday"],
            },
            {key: [slot()] for key in ["weekday", "holiday_eve", "holiday"]},
        )

        dates_by_category = {group["key"]: group["dates"] for group in plan}
        self.assertEqual(dates_by_category["weekday"], [])
        self.assertEqual(dates_by_category["holiday_eve"], [date(2026, 12, 31)])
        self.assertEqual(dates_by_category["holiday"], [date(2027, 1, 1)])


class BulkRequirementValidationTests(SimpleTestCase):
    def test_range_and_category_validation(self):
        invalid_examples = [
            {"end_date": "2026-09-30"},
            {"start_date": "2026-01-01", "end_date": "2027-01-02"},
            {"start_date": "1948-12-31", "end_date": "1949-01-01"},
            {"start_date": "2099-12-31", "end_date": "2100-01-01"},
            {"start_date": "2026-02-30"},
            {"categories": []},
            {"categories": ["unknown"]},
            {"weekend_policy": "unknown"},
            {"mode": "unknown"},
        ]
        for changes in invalid_examples:
            with self.subTest(changes=changes):
                self.assertFalse(BulkRequirementForm(bulk_data(**changes)).is_valid())

    def test_time_slots_reject_reversed_times_and_invalid_counts(self):
        invalid_examples = [
            {"weekday-0-end_time": "09:00"},
            {"weekday-0-start_time": "11:00"},
            {"weekday-0-required_staff_count": "0"},
            {"weekday-0-required_staff_count": "-1"},
            {"weekday-0-required_staff_count": "2147483648"},
            {"weekday-0-start_time": "", "weekday-0-end_time": "", "weekday-0-required_staff_count": ""},
        ]
        for changes in invalid_examples:
            with self.subTest(changes=changes):
                formset = RequirementTimeSlotFormSet(bulk_data(**changes), prefix="weekday")
                self.assertFalse(formset.is_valid())

    def test_overlapping_containing_identical_and_adjacent_time_slots_are_valid(self):
        for start, end in [
            ("09:30", "11:00"), ("08:00", "11:00"), ("09:15", "09:45"),
            ("09:00", "10:00"), ("10:00", "11:00"),
        ]:
            with self.subTest(start=start, end=end):
                data = bulk_data(**{
                    "weekday-TOTAL_FORMS": "2",
                    "weekday-1-start_time": start,
                    "weekday-1-end_time": end,
                    "weekday-1-required_staff_count": "2",
                })
                formset = RequirementTimeSlotFormSet(data, prefix="weekday")
                self.assertTrue(formset.is_valid(), formset.errors)


class BulkRequirementPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="bulk_manager")
        cls.staff = get_user_model().objects.create_user(username="bulk_staff")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager"
        )
        cls.staff_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff"
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("manager_requirement_bulk")

    def requirement(self, work_date=date(2026, 10, 1), *, store=None, times=None, count=1):
        start, end = times or (time(9), time(10))
        return Requirement.objects.create(
            store=store or self.store,
            work_date=work_date,
            start_time=start,
            end_time=end,
            required_staff_count=count,
        )

    def snapshot(self):
        return list(Requirement.objects.order_by("pk").values())

    def protected_requirements(self):
        return [
            self.requirement(store=self.other_store),
            self.requirement(date(2026, 10, 2)),
            self.requirement(date(2026, 10, 3)),
            self.requirement(date(2026, 9, 30)),
            self.requirement(times=(time(13), time(14))),
        ]

    def plan_for(self, start, end):
        return build_plan(
            {
                "start_date": start,
                "end_date": end,
                "weekend_policy": "sat_sun",
                "categories": ["weekday"],
            },
            {"weekday": [slot()]},
        )

    def test_get_initial_range_is_the_displayed_month(self):
        response = self.client.get(self.url, {"year": "2026", "month": "11"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["start_date"], date(2026, 11, 1))
        self.assertEqual(response.context["form"].initial["end_date"], date(2026, 11, 30))
        self.assertIsNone(response.context["plan"])

    def test_single_day_registration_validates_times_and_positive_count(self):
        url = reverse("manager_requirement_list")
        data = {"work_date": "2026-10-01", "start_time": "09:00", "end_time": "10:00", "required_staff_count": "3"}
        for changes in [
            {"end_time": "09:00"},
            {"end_time": "08:00"},
            {"required_staff_count": "0"},
            {"required_staff_count": "2147483648"},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post(url, {**data, **changes})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(Requirement.objects.exists())
        response = self.client.post(url, data)
        self.assertRedirects(response, url)
        requirement = Requirement.objects.get()
        self.assertEqual(requirement.store, self.store)
        self.assertEqual(requirement.required_staff_count, 3)

    def test_preview_does_not_modify_existing_data(self):
        self.requirement(times=(time(8), time(11)))
        self.protected_requirements()
        before = self.snapshot()

        response = self.client.post(self.url, bulk_data(action="preview", **{
            "weekday-TOTAL_FORMS": "2",
            "weekday-1-start_time": "09:30",
            "weekday-1-end_time": "11:00",
            "weekday-1-required_staff_count": "2",
        }))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(response.context["dates_count"], 1)
        self.assertEqual(response.context["slots_count"], 2)
        self.assertEqual(response.context["plan"][0]["dates"], [date(2026, 10, 1)])

    def test_update_preserves_other_stores_categories_dates_and_time_slots(self):
        existing = self.requirement()
        protected = self.protected_requirements()
        protected_ids = [item.pk for item in protected]
        before = list(Requirement.objects.filter(pk__in=protected_ids).order_by("pk").values())
        shift = Shift.objects.create(
            user=self.staff,
            membership=self.staff_membership,
            store=self.store,
            work_date=date(2026, 10, 1),
            start_time=time(9),
            end_time=time(10),
        )

        response = self.client.post(self.url, bulk_data(store=self.other_store.pk))

        self.assertRedirects(response, reverse("manager_requirement_list"))
        existing.refresh_from_db()
        self.assertEqual(existing.required_staff_count, 3)
        self.assertEqual(
            list(Requirement.objects.filter(pk__in=protected_ids).order_by("pk").values()),
            before,
        )
        self.assertTrue(Shift.objects.filter(pk=shift.pk).exists())

    def test_repeated_update_collapses_matching_duplicates_and_keeps_same_row(self):
        existing = self.requirement()
        duplicate = self.requirement(count=5)

        for _ in range(2):
            response = self.client.post(self.url, bulk_data())
            self.assertEqual(response.status_code, 302)

        requirements = Requirement.objects.filter(store=self.store)
        self.assertEqual(requirements.count(), 1)
        self.assertEqual(requirements.get().pk, existing.pk)
        self.assertEqual(requirements.get().required_staff_count, 3)
        self.assertFalse(Requirement.objects.filter(pk=duplicate.pk).exists())

    def test_replace_only_replaces_selected_dates_in_the_authorized_store(self):
        selected_slot = self.requirement(times=(time(8), time(12)))
        second_slot = self.requirement(times=(time(13), time(14)))
        protected = [
            self.requirement(store=self.other_store),
            self.requirement(date(2026, 10, 2)),
            self.requirement(date(2026, 10, 3)),
            self.requirement(date(2026, 9, 30)),
        ]
        protected_ids = [item.pk for item in protected]
        before = list(Requirement.objects.filter(pk__in=protected_ids).order_by("pk").values())

        response = self.client.post(self.url, bulk_data(mode="replace"))

        self.assertRedirects(response, reverse("manager_requirement_list"))
        self.assertFalse(Requirement.objects.filter(pk__in=[selected_slot.pk, second_slot.pk]).exists())
        self.assertEqual(
            list(Requirement.objects.filter(pk__in=protected_ids).order_by("pk").values()),
            before,
        )
        replacement = Requirement.objects.get(store=self.store, work_date=date(2026, 10, 1))
        self.assertEqual((replacement.start_time, replacement.end_time), (time(9), time(10)))
        self.assertEqual(replacement.required_staff_count, 3)

    def test_update_keeps_existing_overlapping_slots_and_adds_new_slot(self):
        containing = self.requirement(times=(time(8), time(11)), count=4)
        partial = self.requirement(times=(time(9, 30), time(10, 30)), count=5)
        existing_ids = [containing.pk, partial.pk]
        before = list(Requirement.objects.filter(pk__in=existing_ids).order_by("pk").values())

        response = self.client.post(self.url, bulk_data())

        self.assertRedirects(response, reverse("manager_requirement_list"))
        self.assertEqual(
            list(Requirement.objects.filter(pk__in=existing_ids).order_by("pk").values()), before,
        )
        created = Requirement.objects.exclude(pk__in=existing_ids).get()
        self.assertEqual(created.store, self.store)
        self.assertEqual(created.work_date, date(2026, 10, 1))
        self.assertEqual((created.start_time, created.end_time), (time(9), time(10)))
        self.assertEqual(created.required_staff_count, 3)

    def test_overlapping_submitted_slots_are_saved_in_update_and_replace_modes(self):
        old = self.requirement(times=(time(8), time(18)), count=8)
        for mode in ["update", "replace"]:
            with self.subTest(mode=mode):
                response = self.client.post(self.url, bulk_data(mode=mode, **{
                    "weekday-0-end_time": "12:00",
                    "weekday-TOTAL_FORMS": "3",
                    "weekday-1-start_time": "10:00",
                    "weekday-1-end_time": "13:00",
                    "weekday-1-required_staff_count": "2",
                    "weekday-2-start_time": "10:15",
                    "weekday-2-end_time": "10:45",
                    "weekday-2-required_staff_count": "1",
                }))

                self.assertRedirects(response, reverse("manager_requirement_list"))
                expected = [
                    (time(9), time(12), 3), (time(10), time(13), 2),
                    (time(10, 15), time(10, 45), 1),
                ]
                if mode == "update":
                    expected.append((time(8), time(18), 8))
                self.assertCountEqual(
                    Requirement.objects.filter(store=self.store).values_list(
                        "start_time", "end_time", "required_staff_count"
                    ), expected,
                )
                self.assertEqual(Requirement.objects.filter(pk=old.pk).exists(), mode == "update")

    def test_identical_submitted_slots_use_last_count_without_creating_duplicate_rows(self):
        existing = self.requirement(count=7)
        data = bulk_data(**{
            "weekday-TOTAL_FORMS": "3",
            "weekday-1-start_time": "09:00",
            "weekday-1-end_time": "10:00",
            "weekday-1-required_staff_count": "4",
            "weekday-2-start_time": "09:00",
            "weekday-2-end_time": "10:00",
            "weekday-2-required_staff_count": "2",
        })

        for _ in range(2):
            response = self.client.post(self.url, data)
            self.assertRedirects(response, reverse("manager_requirement_list"))
            self.assertEqual(Requirement.objects.count(), 1)
            existing.refresh_from_db()
            self.assertEqual(existing.required_staff_count, 2)

    def test_invalid_apply_requests_do_not_modify_data(self):
        self.requirement()
        before = self.snapshot()
        invalid_examples = [
            {"start_date": "2026-02-30"},
            {"end_date": "2026-09-30"},
            {"categories": []},
            {"weekday-0-end_time": "09:00"},
            {"weekday-0-required_staff_count": "0"},
            {"weekday-0-required_staff_count": "999999999999999999999999999"},
            {"weekday-TOTAL_FORMS": "0"},
            {"action": "unknown"},
        ]
        for changes in invalid_examples:
            with self.subTest(changes=changes):
                response = self.client.post(self.url, bulk_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.snapshot(), before)
                self.assertIsNone(response.context["plan"])

    def test_selected_categories_do_not_require_unselected_formsets(self):
        response = self.client.post(self.url, bulk_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Requirement.objects.count(), 1)

    def test_multiple_selected_categories_and_time_slots_are_applied(self):
        data = bulk_data(**{
            "categories": ["weekday", "holiday_eve", "holiday"],
            "weekday-TOTAL_FORMS": "2",
            "weekday-1-start_time": "13:00",
            "weekday-1-end_time": "14:00",
            "weekday-1-required_staff_count": "2",
            "holiday_eve-TOTAL_FORMS": "1",
            "holiday_eve-INITIAL_FORMS": "0",
            "holiday_eve-0-start_time": "09:00",
            "holiday_eve-0-end_time": "10:00",
            "holiday_eve-0-required_staff_count": "4",
            "holiday-TOTAL_FORMS": "1",
            "holiday-INITIAL_FORMS": "0",
            "holiday-0-start_time": "09:00",
            "holiday-0-end_time": "10:00",
            "holiday-0-required_staff_count": "5",
        })

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Requirement.objects.count(), 5)
        self.assertEqual(Requirement.objects.get(work_date=date(2026, 10, 2)).required_staff_count, 4)
        for work_date in [date(2026, 10, 3), date(2026, 10, 4)]:
            self.assertEqual(Requirement.objects.get(work_date=work_date).required_staff_count, 5)

    def test_empty_selected_category_date_range_does_not_delete_existing_data(self):
        self.requirement(date(2026, 10, 3))
        before = self.snapshot()

        response = self.client.post(
            self.url, bulk_data(start_date="2026-10-03", end_date="2026-10-04", mode="replace")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(self.snapshot(), before)

    def test_staff_inactive_manager_and_anonymous_users_cannot_apply(self):
        self.requirement()
        before = self.snapshot()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url, bulk_data()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url, bulk_data()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

        self.client.logout()
        response = self.client.post(self.url, bulk_data())
        self.assertRedirects(
            response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )
        self.assertEqual(self.snapshot(), before)

    def test_update_failure_rolls_back_updates_duplicate_deletion_and_new_rows(self):
        self.requirement(date(2026, 10, 5), count=1)
        self.requirement(date(2026, 10, 5), count=2)
        before = self.snapshot()
        plan = self.plan_for(date(2026, 10, 5), date(2026, 10, 7))
        original_create = Requirement.objects.create
        create_count = 0

        def fail_on_second_create(**kwargs):
            nonlocal create_count
            create_count += 1
            if create_count == 2:
                raise RuntimeError("simulated bulk save failure")
            return original_create(**kwargs)

        with patch("shifts.requirement_bulk.Requirement.objects.create", side_effect=fail_on_second_create):
            with self.assertRaisesMessage(RuntimeError, "simulated bulk save failure"):
                apply_plan(self.store, plan, "update")

        self.assertEqual(create_count, 2)
        self.assertEqual(self.snapshot(), before)

    def test_replace_failure_restores_deleted_rows_and_removes_partial_new_rows(self):
        self.requirement(date(2026, 10, 5), times=(time(8), time(12)))
        self.requirement(date(2026, 10, 6), times=(time(13), time(14)))
        self.requirement(date(2026, 10, 5), store=self.other_store)
        before = self.snapshot()
        plan = self.plan_for(date(2026, 10, 5), date(2026, 10, 6))
        original_create = Requirement.objects.create
        create_count = 0

        def fail_on_second_create(**kwargs):
            nonlocal create_count
            create_count += 1
            if create_count == 2:
                raise RuntimeError("simulated bulk replace failure")
            return original_create(**kwargs)

        with patch("shifts.requirement_bulk.Requirement.objects.create", side_effect=fail_on_second_create):
            with self.assertRaisesMessage(RuntimeError, "simulated bulk replace failure"):
                apply_plan(self.store, plan, "replace")

        self.assertEqual(create_count, 2)
        self.assertEqual(self.snapshot(), before)
