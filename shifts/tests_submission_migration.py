from datetime import UTC, date, datetime, time
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from accounts.models import Company, Store, StoreMembership
from shifts.models import Availability, Shift, ShiftSubmission


class PreserveExistingSubmissionsMigrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="既存希望移行テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.second_store = Store.objects.create(company=company, name="支店")
        cls.employee = get_user_model().objects.create_user(
            username="migration_employee", last_name="移行", first_name="従業員",
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.employee, store=cls.store, submission_mode="day_off", rank="A",
            desired_shifts_per_week=4, can_view_shifts=False,
        )
        cls.second_membership = StoreMembership.objects.create(
            user=cls.employee, store=cls.second_store, submission_mode="availability",
            is_active=False, can_submit_availability=False,
        )
        cls.month = date(2030, 11, 1)
        cls.work_date = date(2030, 11, 12)

    def migrate_existing(self):
        migration = import_module("shifts.migrations.0013_shiftsubmission_dayoffrequest_and_more")
        migration.preserve_existing_submissions(apps, SimpleNamespace(connection=connection))

    def legacy_availability(self, *, membership=None, work_date=None, store=None,
                            user=None, memberless=False, saved_at=None):
        membership = membership or self.membership
        availability = Availability.objects.create(
            user=user or membership.user,
            membership=None if memberless else membership,
            store=store,
            work_date=work_date or self.work_date,
            start_time=time(9), end_time=time(18), submission=None,
        )
        if saved_at:
            Availability.objects.filter(pk=availability.pk).update(created_at=saved_at)
            availability.refresh_from_db()
        return availability

    def test_existing_times_become_submitted_without_changing_business_data(self):
        first_saved = datetime(2030, 10, 2, 9, tzinfo=UTC)
        last_saved = datetime(2030, 10, 8, 14, tzinfo=UTC)
        first = self.legacy_availability(saved_at=first_saved)
        second = self.legacy_availability(work_date=date(2030, 11, 20), saved_at=last_saved)
        Shift.objects.create(
            user=self.employee, membership=self.membership, store=self.store,
            work_date=self.work_date, start_time=time(10), end_time=time(17),
            is_generated=False, note="移行前のヘルプ先", display_color="green",
        )
        untouched_availability_fields = [
            "id", "user_id", "membership_id", "store_id", "work_date",
            "start_time", "end_time", "created_at",
        ]
        wishes_before = list(Availability.objects.order_by("pk").values(*untouched_availability_fields))
        users_before = list(get_user_model().objects.order_by("pk").values())
        memberships_before = list(StoreMembership.objects.order_by("pk").values())
        shifts_before = list(Shift.objects.order_by("pk").values())

        self.migrate_existing()

        submission = ShiftSubmission.objects.get()
        self.assertEqual(submission.membership, self.membership)
        self.assertEqual(submission.target_month, self.month)
        self.assertEqual(submission.submission_mode, "availability")
        self.assertTrue(submission.is_submitted)
        self.assertEqual(submission.submitted_at, last_saved)
        for availability in [first, second]:
            availability.refresh_from_db()
            self.assertEqual(availability.submission_id, submission.pk)
        self.assertEqual(list(Availability.objects.order_by("pk").values(*untouched_availability_fields)), wishes_before)
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), users_before)
        self.assertEqual(list(StoreMembership.objects.order_by("pk").values()), memberships_before)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), shifts_before)

    def test_months_and_store_memberships_are_migrated_independently(self):
        first = self.legacy_availability()
        next_month = self.legacy_availability(work_date=date(2030, 12, 1))
        # 無効所属の既存希望も失わず、所属の有効状態は変更しない。
        other_store = self.legacy_availability(membership=self.second_membership, store=self.second_store)

        self.migrate_existing()

        self.assertEqual(ShiftSubmission.objects.count(), 3)
        for availability, membership, month in [
            (first, self.membership, self.month),
            (next_month, self.membership, date(2030, 12, 1)),
            (other_store, self.second_membership, self.month),
        ]:
            with self.subTest(availability=availability.pk):
                availability.refresh_from_db()
                self.assertEqual(availability.submission.membership_id, membership.pk)
                self.assertEqual(availability.submission.target_month, month)
                self.assertTrue(availability.submission.is_submitted)
        self.second_membership.refresh_from_db()
        self.assertFalse(self.second_membership.is_active)
        self.assertFalse(self.second_membership.can_submit_availability)

    def test_memberless_rows_use_exact_user_and_explicit_store_or_remain_untouched(self):
        known = self.legacy_availability(memberless=True, store=self.second_store)
        unknown_user = get_user_model().objects.create_user(username="migration_unassigned")
        unknown = self.legacy_availability(memberless=True, store=self.store, user=unknown_user)
        no_store = self.legacy_availability(memberless=True)
        unchanged_ids = [unknown.pk, no_store.pk]
        unmatched_before = list(Availability.objects.filter(pk__in=unchanged_ids).order_by("pk").values())
        membership_count = StoreMembership.objects.count()

        self.migrate_existing()

        known.refresh_from_db()
        self.assertEqual(known.membership_id, self.second_membership.pk)
        self.assertEqual(known.submission.membership_id, self.second_membership.pk)
        self.assertEqual(known.store_id, self.second_store.pk)
        self.assertEqual(ShiftSubmission.objects.count(), 1)
        self.assertEqual(StoreMembership.objects.count(), membership_count)
        self.assertEqual(list(Availability.objects.filter(pk__in=unchanged_ids).order_by("pk").values()), unmatched_before)

    def test_running_twice_does_not_duplicate_or_rewrite_submissions(self):
        self.legacy_availability()
        self.legacy_availability(work_date=date(2030, 11, 20))
        self.legacy_availability(membership=self.second_membership)
        self.migrate_existing()
        submissions_before = list(ShiftSubmission.objects.order_by("pk").values())
        wishes_before = list(Availability.objects.order_by("pk").values())

        self.migrate_existing()

        self.assertEqual(ShiftSubmission.objects.count(), 2)
        self.assertEqual(list(ShiftSubmission.objects.order_by("pk").values()), submissions_before)
        self.assertEqual(list(Availability.objects.order_by("pk").values()), wishes_before)
