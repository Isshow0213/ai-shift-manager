from datetime import date, time
from importlib import import_module
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from .forms import ManagerRequirementForm
from .models import Requirement, RequirementTimePreset
from .requirement_settings import get_month_dates


def settings_data(**overrides):
    data = {
        "schedule_mode": "month", "target_month": "2026-10", "day_type": "monday",
        "start_time": "09:00", "end_time": "18:00", "required_staff_count": "3",
        "memo": "別店舗のヘルプ\n入口の受付を担当",
    }
    data.update(overrides)
    return data


class MonthlyRequirementFormTests(SimpleTestCase):
    def test_month_mode_does_not_require_a_date(self):
        form = ManagerRequirementForm(settings_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["work_dates"], [date(2026, 10, day) for day in (5, 12, 19, 26)])
        self.assertFalse(form.fields["work_date"].required)
        self.assertTrue(form.fields["work_date"].disabled)

    def test_date_mode_ignores_month_and_day_type_even_if_tampered(self):
        form = ManagerRequirementForm(settings_data(
            schedule_mode="date", work_date="2026-11-02", target_month="broken", day_type="unknown",
        ))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["work_dates"], [date(2026, 11, 2)])
        self.assertTrue(form.fields["work_date"].required)
        self.assertTrue(form.fields["target_month"].disabled)
        self.assertTrue(form.fields["day_type"].disabled)

    def test_weekdays_exclude_weekends_and_japanese_holidays_but_include_fridays(self):
        days = get_month_dates(date(2026, 10, 1), "weekday")
        self.assertEqual(len(days), 21)
        self.assertNotIn(date(2026, 10, 12), days)
        self.assertNotIn(date(2026, 10, 3), days)
        self.assertIn(date(2026, 10, 2), days)

    def test_holidays_include_substitute_and_citizens_holidays(self):
        self.assertIn(date(2026, 5, 6), get_month_dates(date(2026, 5, 1), "holiday"))
        self.assertIn(date(2026, 9, 22), get_month_dates(date(2026, 9, 1), "holiday"))
        self.assertEqual(get_month_dates(date(2026, 10, 1), "holiday"), [date(2026, 10, 12)])

    def test_each_weekday_matches_all_days_in_the_selected_month(self):
        for index, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            with self.subTest(name=name):
                days = get_month_dates(date(2026, 2, 1), name)
                self.assertEqual(len(days), 4)
                self.assertTrue(all(day.month == 2 and day.weekday() == index for day in days))

    def test_invalid_targets_times_counts_and_memos_are_rejected(self):
        examples = [
            {"target_month": ""}, {"target_month": "2026-13"}, {"target_month": "2026-10-01"},
            {"target_month": "1948-12"}, {"target_month": "2100-01"},
            {"day_type": ""}, {"day_type": "unknown"}, {"schedule_mode": "unknown"},
            {"schedule_mode": "date", "work_date": ""}, {"schedule_mode": "date", "work_date": "0001-01-01"},
            {"end_time": "09:00"}, {"end_time": "08:00"},
            {"required_staff_count": "0"}, {"required_staff_count": "2147483648"}, {"memo": "あ" * 501},
        ]
        for values in examples:
            with self.subTest(values=values):
                self.assertFalse(ManagerRequirementForm(settings_data(**values)).is_valid())

    def test_no_holidays_in_month_reports_a_field_error(self):
        form = ManagerRequirementForm(settings_data(target_month="2026-06", day_type="holiday"))
        self.assertFalse(form.is_valid())
        self.assertIn("day_type", form.errors)

    def test_legacy_single_date_post_is_supported(self):
        for day_type in ("", "weekday"):
            with self.subTest(day_type=day_type):
                form = ManagerRequirementForm(data={
                    "work_date": "2026-10-02", "start_time": "09:00", "end_time": "10:00",
                    "required_staff_count": "1", "day_type": day_type,
                })
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["schedule_mode"], "date")


class RequirementSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="設定テスト会社")
        cls.store = Store.objects.create(company=company, name="設定店舗")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="settings-manager")
        cls.staff = get_user_model().objects.create_user(username="settings-staff")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")

    def setUp(self):
        self.url = reverse("manager_requirement_list")
        self.client.force_login(self.manager)

    def requirement(self, *, store=None, work_date=date(2026, 10, 5), start=time(9), end=time(18), count=1, memo="以前のメモ"):
        return Requirement.objects.create(
            store=store or self.store, work_date=work_date, start_time=start, end_time=end,
            required_staff_count=count, memo=memo,
        )

    def test_get_defaults_to_month_and_never_writes(self):
        response = self.client.get(self.url, {"browse_month": "2026-11"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["schedule_mode"], "month")
        self.assertEqual(response.context["form"].initial["target_month"], date(2026, 11, 1))
        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(RequirementTimePreset.objects.exists())

    def test_calendar_date_link_starts_in_single_date_mode(self):
        response = self.client.get(self.url, {"date": "2026-11-12", "browse_month": "invalid"})
        self.assertEqual(response.context["form"].initial["schedule_mode"], "date")
        self.assertEqual(response.context["form"].initial["work_date"], date(2026, 11, 12))
        self.assertEqual(response.context["month"], 11)

    def test_monthly_post_saves_identical_memo_and_one_preset_for_all_matching_dates(self):
        response = self.client.post(self.url, settings_data())
        self.assertRedirects(response, self.url + "?year=2026&month=10")
        requirements = Requirement.objects.filter(store=self.store)
        self.assertEqual(set(requirements.values_list("work_date", flat=True)), {date(2026, 10, day) for day in (5, 12, 19, 26)})
        self.assertEqual(set(requirements.values_list("memo", flat=True)), {settings_data()["memo"]})
        self.assertEqual(set(requirements.values_list("day_type", flat=True)), {"monday"})
        self.assertEqual(RequirementTimePreset.objects.count(), 1)
        self.assertEqual(RequirementTimePreset.objects.get().memo, settings_data()["memo"])

    def test_single_day_can_save_an_individual_memo(self):
        response = self.client.post(self.url, settings_data(schedule_mode="date", work_date="2026-11-03", memo="この日だけヘルプ"))
        self.assertRedirects(response, self.url + "?year=2026&month=11")
        requirement = Requirement.objects.get()
        self.assertEqual(requirement.work_date, date(2026, 11, 3))
        self.assertEqual(requirement.memo, "この日だけヘルプ")
        self.assertIsNone(requirement.day_type)

    def test_reapplying_updates_count_memo_and_exact_duplicates_only(self):
        original = self.requirement()
        self.requirement()  # 過去の重複データ。
        overlapping = self.requirement(start=time(10), end=time(19))
        protected = self.requirement(store=self.other_store)
        self.client.post(self.url, settings_data())
        self.client.post(self.url, settings_data(required_staff_count="5", memo="新しいメモ"))
        original.refresh_from_db()
        self.assertEqual((original.required_staff_count, original.memo), (5, "新しいメモ"))
        self.assertEqual(Requirement.objects.filter(store=self.store, start_time=time(9), end_time=time(18)).count(), 4)
        self.assertTrue(Requirement.objects.filter(pk=overlapping.pk, memo="以前のメモ").exists())
        protected.refresh_from_db()
        self.assertEqual(protected.memo, "以前のメモ")
        self.assertEqual(RequirementTimePreset.objects.count(), 1)
        self.assertEqual(RequirementTimePreset.objects.get().required_staff_count, 5)

    def test_saved_presets_persist_across_months_and_are_store_scoped(self):
        self.client.post(self.url, settings_data(target_month="2026-01"))
        RequirementTimePreset.objects.create(store=self.other_store, start_time=time(5), end_time=time(6), required_staff_count=7, memo="別店舗の秘密")
        response = self.client.get(self.url, {"year": "2027", "month": "2"})
        self.assertEqual(list(response.context["time_presets"].values_list("store_id", flat=True)), [self.store.pk])
        self.assertContains(response, "09:00")
        self.assertNotContains(response, "別店舗の秘密")

    def test_legacy_post_without_memo_preserves_an_existing_memo(self):
        existing = self.requirement()
        response = self.client.post(self.url, {
            "work_date": "2026-10-05", "day_type": "weekday", "start_time": "09:00",
            "end_time": "18:00", "required_staff_count": "5",
        })
        self.assertRedirects(response, self.url)
        existing.refresh_from_db()
        self.assertEqual((existing.required_staff_count, existing.memo), (5, "以前のメモ"))
        self.assertEqual(RequirementTimePreset.objects.get().memo, existing.memo)

    def test_explicit_empty_memo_clears_the_previous_memo(self):
        existing = self.requirement()
        self.client.post(self.url, settings_data(schedule_mode="date", work_date="2026-10-05", memo=""))
        existing.refresh_from_db()
        self.assertEqual(existing.memo, "")
        self.assertEqual(RequirementTimePreset.objects.get().memo, "")

    def test_list_is_filtered_by_month_and_displays_escaped_memo(self):
        self.requirement(memo='<script>危険</script>\nヘルプ先')
        self.requirement(work_date=date(2026, 11, 5), memo="来月のみ")
        self.requirement(store=self.other_store, memo="別店舗のみ")
        response = self.client.get(self.url, {"browse_month": "2026-10"})
        self.assertEqual(len(response.context["requirements"]), 1)
        self.assertContains(response, "&lt;script&gt;危険&lt;/script&gt;")
        self.assertNotContains(response, "<script>危険</script>")
        self.assertNotContains(response, "来月のみ")
        self.assertNotContains(response, "別店舗のみ")

    def test_invalid_post_does_not_partially_save(self):
        response = self.client.post(self.url, settings_data(memo="あ" * 501))
        self.assertEqual(response.status_code, 200)
        self.assertIn("memo", response.context["form"].errors)
        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(RequirementTimePreset.objects.exists())

    def test_preset_failure_rolls_back_the_entire_month(self):
        with patch("shifts.requirement_settings.remember_time_preset", side_effect=RuntimeError("保存失敗")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, settings_data())
        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(RequirementTimePreset.objects.exists())

    def test_staff_and_anonymous_users_cannot_save_requirements(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url, settings_data()).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, settings_data()).status_code, 302)
        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(RequirementTimePreset.objects.exists())

    def test_preset_database_constraint_is_per_store(self):
        RequirementTimePreset.objects.create(store=self.store, start_time=time(9), end_time=time(18), required_staff_count=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RequirementTimePreset.objects.create(store=self.store, start_time=time(9), end_time=time(18), required_staff_count=2)
        RequirementTimePreset.objects.create(store=self.other_store, start_time=time(9), end_time=time(18), required_staff_count=2)
        self.assertEqual(RequirementTimePreset.objects.count(), 2)

    def test_migration_imports_all_past_slots_and_deduplicates_by_latest_entry(self):
        self.requirement(work_date=date(2020, 1, 1), count=2)
        latest = self.requirement(work_date=date(2021, 1, 1), count=5, memo="引き継ぐメモ")
        self.requirement(store=self.other_store, count=7)
        self.requirement(start=time(18), end=time(9))
        self.requirement(start=time(7), end=time(8), count=0)
        Requirement.objects.create(work_date=date(2020, 1, 1), start_time=time(7), end_time=time(8), required_staff_count=1)
        before = list(Requirement.objects.order_by("pk").values())
        migration = import_module("shifts.migrations.0009_requirement_time_presets")
        historical_apps = MigrationExecutor(connection).loader.project_state([
            ("shifts", "0009_requirement_time_presets"),
        ]).apps
        migration.import_saved_time_slots(historical_apps, connection.schema_editor(atomic=False))
        self.assertEqual(RequirementTimePreset.objects.count(), 2)
        own_preset = RequirementTimePreset.objects.get(store=self.store)
        self.assertEqual((own_preset.required_staff_count, own_preset.memo), (latest.required_staff_count, latest.memo))
        self.assertEqual(list(Requirement.objects.order_by("pk").values()), before)
