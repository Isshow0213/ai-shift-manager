from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Requirement, Shift

from .services import generate_shifts_for_store


User = get_user_model()


class GenerateShiftsForStoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=cls.company, name="対象店舗")
        cls.other_store = Store.objects.create(company=cls.company, name="別店舗")
        cls.work_date = date(2030, 1, 10)

    def make_membership(
        self, username, *, store=None, role="staff", is_active=True, user_active=True
    ):
        user = User.objects.create(username=username, is_active=user_active)
        return StoreMembership.objects.create(
            user=user,
            store=store or self.store,
            role=role,
            is_active=is_active,
        )

    def make_availability(
        self, membership, *, work_date=None, start_time=time(8), end_time=time(18)
    ):
        return Availability.objects.create(
            user=membership.user,
            membership=membership,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=end_time,
        )

    def make_requirement(
        self,
        *,
        store=None,
        work_date=None,
        start_time=time(9),
        end_time=time(10),
        required_staff_count=1,
    ):
        return Requirement.objects.create(
            store=store or self.store,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=end_time,
            required_staff_count=required_staff_count,
        )

    def make_shift(
        self,
        membership,
        *,
        work_date=None,
        start_time=time(9),
        end_time=time(10),
        is_generated=True,
    ):
        return Shift.objects.create(
            user=membership.user,
            membership=membership,
            store=membership.store,
            work_date=work_date or self.work_date,
            start_time=start_time,
            end_time=end_time,
            is_generated=is_generated,
        )

    def test_only_active_staff_with_covering_availability_are_selected(self):
        ineligible_memberships = [
            self.make_membership("manager", role="manager"),
            self.make_membership("inactive_membership", is_active=False),
            self.make_membership("inactive_user", user_active=False),
            self.make_membership("other_store", store=self.other_store),
        ]
        for membership in ineligible_memberships:
            self.make_availability(membership)

        self.make_availability(
            self.make_membership("other_date"),
            work_date=self.work_date + timedelta(days=1),
        )
        self.make_availability(
            self.make_membership("starts_too_late"), start_time=time(9, 1)
        )
        self.make_availability(
            self.make_membership("ends_too_early"), end_time=time(9, 59)
        )
        eligible = self.make_membership("eligible")
        self.make_availability(eligible, start_time=time(9), end_time=time(10))
        self.make_requirement(required_staff_count=8)

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 1, "shortfall_count": 7, "requirement_count": 1},
        )
        shift = Shift.objects.get()
        self.assertEqual(shift.membership_id, eligible.id)
        self.assertEqual(shift.user_id, eligible.user_id)
        self.assertEqual(shift.store_id, self.store.id)
        self.assertEqual(shift.work_date, self.work_date)
        self.assertEqual(shift.start_time, time(9))
        self.assertEqual(shift.end_time, time(10))
        self.assertTrue(shift.is_generated)

    def test_regenerates_only_selected_day_and_keeps_manual_and_other_shifts(self):
        staff = self.make_membership("staff")
        other_staff = self.make_membership("other_staff", store=self.other_store)
        next_day = self.work_date + timedelta(days=1)
        self.make_availability(staff)
        self.make_requirement()
        self.make_requirement(work_date=next_day)
        self.make_requirement(store=self.other_store)

        old_generated = self.make_shift(staff)
        manual = self.make_shift(
            staff, start_time=time(16), end_time=time(17), is_generated=False
        )
        other_day = self.make_shift(staff, work_date=next_day)
        other_store = self.make_shift(other_staff)
        preserved_ids = [manual.id, other_day.id, other_store.id]
        preserved_before = list(
            Shift.objects.filter(pk__in=preserved_ids).order_by("pk").values()
        )

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 1, "shortfall_count": 0, "requirement_count": 1},
        )
        self.assertFalse(Shift.objects.filter(pk=old_generated.id).exists())
        self.assertEqual(
            list(Shift.objects.filter(pk__in=preserved_ids).order_by("pk").values()),
            preserved_before,
        )
        regenerated = Shift.objects.get(
            store=self.store, work_date=self.work_date, is_generated=True
        )
        self.assertEqual(regenerated.membership_id, staff.id)
        self.assertEqual(Shift.objects.count(), 4)

    def test_reports_total_shortfall_across_requirements(self):
        staff = self.make_membership("staff")
        self.make_availability(staff)
        self.make_requirement(required_staff_count=3)
        self.make_requirement(
            start_time=time(13), end_time=time(14), required_staff_count=2
        )

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 2, "shortfall_count": 3, "requirement_count": 2},
        )
        self.assertEqual(Shift.objects.count(), 2)

    def test_duplicate_availability_does_not_count_a_person_more_than_once(self):
        first_staff = self.make_membership("first_staff")
        second_staff = self.make_membership("second_staff")
        for _ in range(3):
            self.make_availability(first_staff)
        self.make_availability(second_staff)
        self.make_requirement(required_staff_count=3)

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 2, "shortfall_count": 1, "requirement_count": 1},
        )
        self.assertCountEqual(
            Shift.objects.values_list("user_id", flat=True),
            [first_staff.user_id, second_staff.user_id],
        )

    def test_overlapping_manual_shift_blocks_automatic_assignment_for_that_person(self):
        busy_staff = self.make_membership("busy_staff")
        free_staff = self.make_membership("free_staff")
        self.make_availability(busy_staff)
        self.make_availability(free_staff)
        manual = self.make_shift(
            busy_staff,
            start_time=time(8, 30),
            end_time=time(9, 30),
            is_generated=False,
        )
        self.make_requirement()

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 1, "shortfall_count": 0, "requirement_count": 1},
        )
        generated = Shift.objects.get(is_generated=True)
        self.assertEqual(generated.user_id, free_staff.user_id)
        self.assertTrue(Shift.objects.filter(pk=manual.id, is_generated=False).exists())

    def test_a_person_is_not_assigned_to_overlapping_requirements(self):
        staff = self.make_membership("staff")
        self.make_availability(staff)
        self.make_requirement(start_time=time(9), end_time=time(11))
        self.make_requirement(start_time=time(10), end_time=time(12))

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 1, "shortfall_count": 1, "requirement_count": 2},
        )
        self.assertEqual(Shift.objects.count(), 1)

    def test_manual_shift_covering_the_requirement_counts_toward_staffing(self):
        assigned_staff = self.make_membership("assigned_staff")
        available_staff = self.make_membership("available_staff")
        self.make_availability(available_staff)
        manual = self.make_shift(
            assigned_staff,
            start_time=time(8),
            end_time=time(11),
            is_generated=False,
        )
        self.make_requirement()

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 0, "shortfall_count": 0, "requirement_count": 1},
        )
        self.assertEqual(Shift.objects.get().pk, manual.pk)
        self.assertFalse(Shift.objects.filter(is_generated=True).exists())

    def test_generated_shift_covering_a_later_requirement_counts_toward_staffing(self):
        staff = self.make_membership("staff")
        self.make_availability(staff)
        self.make_requirement(start_time=time(9), end_time=time(12))
        self.make_requirement(start_time=time(10), end_time=time(11))

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 1, "shortfall_count": 0, "requirement_count": 2},
        )
        self.assertEqual(Shift.objects.count(), 1)

    def test_adjacent_requirements_are_not_treated_as_overlapping(self):
        staff = self.make_membership("staff")
        self.make_availability(staff)
        self.make_requirement(start_time=time(9), end_time=time(10))
        self.make_requirement(start_time=time(10), end_time=time(11))

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 2, "shortfall_count": 0, "requirement_count": 2},
        )
        self.assertEqual(Shift.objects.count(), 2)

    def test_no_requirements_returns_zero_counts_and_preserves_existing_shifts(self):
        staff = self.make_membership("staff")
        self.make_shift(staff)
        self.make_shift(
            staff, start_time=time(16), end_time=time(17), is_generated=False
        )
        self.make_requirement(work_date=self.work_date + timedelta(days=1))
        shifts_before = list(Shift.objects.order_by("pk").values())

        result = generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(
            result,
            {"created_count": 0, "shortfall_count": 0, "requirement_count": 0},
        )
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def test_creation_failure_restores_deleted_and_preserved_shifts(self):
        first_staff = self.make_membership("first_staff")
        second_staff = self.make_membership("second_staff")
        self.make_availability(first_staff)
        self.make_availability(second_staff)
        self.make_requirement(required_staff_count=2)
        self.make_shift(first_staff)
        self.make_shift(
            second_staff, start_time=time(16), end_time=time(17), is_generated=False
        )
        shifts_before = list(Shift.objects.order_by("pk").values())
        original_create = Shift.objects.create
        create_count = 0

        def fail_on_second_create(**kwargs):
            nonlocal create_count
            create_count += 1
            if create_count == 2:
                raise RuntimeError("simulated creation failure")
            return original_create(**kwargs)

        with patch(
            "scheduler.services.Shift.objects.create", side_effect=fail_on_second_create
        ):
            with self.assertRaisesMessage(RuntimeError, "simulated creation failure"):
                generate_shifts_for_store(self.store.id, self.work_date)

        self.assertEqual(create_count, 2)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)
