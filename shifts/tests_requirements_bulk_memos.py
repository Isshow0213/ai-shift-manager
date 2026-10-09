from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership

from .models import Requirement, RequirementTimePreset
from .tests_requirements_bulk import bulk_data


@override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class BulkRequirementMemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="メモ検証会社")
        cls.store = Store.objects.create(company=company, name="メモ検証店舗")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="bulk_memo_manager")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("manager_requirement_bulk")

    def requirement(self, *, memo="変更前のメモ", store=None, start=time(9), end=time(10), work_date=date(2026, 10, 1)):
        return Requirement.objects.create(
            store=store or self.store, work_date=work_date,
            start_time=start, end_time=end, required_staff_count=1, memo=memo,
        )

    def preset(self, *, memo="候補のメモ", store=None, start=time(9), end=time(10)):
        return RequirementTimePreset.objects.create(
            store=store or self.store, start_time=start, end_time=end,
            required_staff_count=1, memo=memo,
        )

    def snapshot(self):
        return (
            list(Requirement.objects.order_by("pk").values()),
            list(RequirementTimePreset.objects.order_by("pk").values()),
        )

    def all_categories_data(self, **overrides):
        data = bulk_data(
            end_date="2026-10-09",
            categories=["weekday", "holiday_eve", "holiday"],
        )
        for key, count, memo in [
            ("weekday", 3, "平日共通\n仕込みをお願いします"),
            ("holiday_eve", 4, "祝前日共通\n在庫の確認"),
            ("holiday", 5, "休日共通\n○○店のヘルプ"),
        ]:
            data.update({
                f"{key}-TOTAL_FORMS": "2",
                f"{key}-INITIAL_FORMS": "0",
                f"{key}-0-start_time": "09:00",
                f"{key}-0-end_time": "10:00",
                f"{key}-0-required_staff_count": str(count),
                f"{key}-1-start_time": "13:00",
                f"{key}-1-end_time": "14:00",
                f"{key}-1-required_staff_count": str(count + 1),
                f"{key}-memo": memo,
            })
        data.update(overrides)
        return data

    def test_each_category_memo_is_saved_to_every_day_and_time_slot(self):
        response = self.client.post(self.url, self.all_categories_data())

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        expected = []
        # 2026年10月1〜9日: 金曜が祝前日、土曜・日曜が休日。
        for days, count, memo in [
            ([1, 5, 6, 7, 8], 3, "平日共通\n仕込みをお願いします"),
            ([2, 9], 4, "祝前日共通\n在庫の確認"),
            ([3, 4], 5, "休日共通\n○○店のヘルプ"),
        ]:
            for day in days:
                expected.extend([
                    (date(2026, 10, day), time(9), time(10), count, memo),
                    (date(2026, 10, day), time(13), time(14), count + 1, memo),
                ])
        self.assertCountEqual(
            Requirement.objects.filter(store=self.store).values_list(
                "work_date", "start_time", "end_time", "required_staff_count", "memo",
            ),
            expected,
        )
        self.assertFalse(Requirement.objects.filter(store=self.other_store).exists())

    def test_preview_exposes_shared_and_slot_memos_without_writing_requirements_or_presets(self):
        self.requirement()
        self.preset()
        before = self.snapshot()
        memo = '<script>alert("メモ")</script>\n2行目'
        data = self.all_categories_data(action="preview", **{"weekday-memo": memo})

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(response.context["dates_count"], 9)
        self.assertEqual(response.context["slots_count"], 18)
        for group in response.context["plan"]:
            expected_memo = data[f'{group["key"]}-memo']
            self.assertEqual(group["memo"], expected_memo)
            self.assertTrue(all(slot["memo"] == expected_memo for slot in group["slots"]))
        self.assertContains(response, "&lt;script&gt;alert(&quot;メモ&quot;)&lt;/script&gt;")
        self.assertNotContains(response, '<script>alert("メモ")</script>')

    def test_memo_at_500_character_limit_is_saved(self):
        memo = "あ" * 500

        response = self.client.post(self.url, bulk_data(**{"weekday-memo": memo}))

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        self.assertEqual(Requirement.objects.get(store=self.store).memo, memo)
        self.assertEqual(RequirementTimePreset.objects.get(store=self.store).memo, memo)

    def test_memo_above_limit_rejects_preview_and_apply_without_writes(self):
        self.requirement()
        self.preset()
        before = self.snapshot()
        memo = "あ" * 501

        for action in ("preview", "apply"):
            with self.subTest(action=action):
                response = self.client.post(
                    self.url, bulk_data(action=action, **{"weekday-memo": memo}),
                )

                self.assertEqual(response.status_code, 200)
                group = next(group for group in response.context["groups"] if group["key"] == "weekday")
                self.assertIn("memo", group["memo_form"].errors)
                self.assertEqual(group["memo_form"]["memo"].value(), memo)
                self.assertIsNone(response.context["plan"])
                self.assertEqual(self.snapshot(), before)

    def test_unselected_category_memo_is_ignored_even_if_invalid(self):
        holiday = self.requirement(work_date=date(2026, 10, 3), memo="休日は変更しない")
        response = self.client.post(self.url, bulk_data(**{
            "weekday-memo": "平日だけ変更",
            "holiday-memo": "あ" * 501,
        }))

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        holiday.refresh_from_db()
        self.assertEqual(holiday.memo, "休日は変更しない")
        self.assertEqual(holiday.required_staff_count, 1)
        self.assertEqual(
            Requirement.objects.get(store=self.store, work_date=date(2026, 10, 1)).memo,
            "平日だけ変更",
        )
        self.assertEqual(Requirement.objects.count(), 2)
        self.assertEqual(RequirementTimePreset.objects.get(store=self.store).memo, "平日だけ変更")

    def test_exact_existing_slot_updates_memo_and_preserves_other_slots_and_stores(self):
        existing = self.requirement()
        overlapping = self.requirement(start=time(9, 30), end=time(11), memo="別の枠")
        other_store = self.requirement(store=self.other_store, memo="別店舗のメモ")

        response = self.client.post(self.url, bulk_data(**{"weekday-memo": "更新したメモ"}))

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        existing.refresh_from_db()
        overlapping.refresh_from_db()
        other_store.refresh_from_db()
        self.assertEqual((existing.required_staff_count, existing.memo), (3, "更新したメモ"))
        self.assertEqual(overlapping.memo, "別の枠")
        self.assertEqual(other_store.memo, "別店舗のメモ")
        self.assertEqual(Requirement.objects.count(), 3)
        preset = RequirementTimePreset.objects.get(store=self.store)
        self.assertEqual((preset.required_staff_count, preset.memo), (3, "更新したメモ"))

    def test_explicit_empty_memo_clears_existing_memo(self):
        existing = self.requirement()

        response = self.client.post(self.url, bulk_data(**{"weekday-memo": ""}))

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        existing.refresh_from_db()
        self.assertEqual(existing.memo, "")
        self.assertEqual(RequirementTimePreset.objects.get(store=self.store).memo, "")

    def test_legacy_post_without_memo_key_preserves_existing_memo(self):
        existing = self.requirement(memo="旧画面からの更新でも残す")

        response = self.client.post(self.url, bulk_data())

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        existing.refresh_from_db()
        self.assertEqual(existing.required_staff_count, 3)
        self.assertEqual(existing.memo, "旧画面からの更新でも残す")
        self.assertEqual(Requirement.objects.count(), 1)
        self.assertEqual(RequirementTimePreset.objects.get(store=self.store).memo, existing.memo)

    def test_repeated_bulk_save_deduplicates_presets_across_dates_and_categories(self):
        other_preset = self.preset(store=self.other_store, memo="別店舗の候補")
        first_ids = None

        for _ in range(2):
            response = self.client.post(self.url, self.all_categories_data())
            self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
            presets = RequirementTimePreset.objects.filter(store=self.store)
            self.assertCountEqual(
                presets.values_list("start_time", "end_time"),
                [(time(9), time(10)), (time(13), time(14))],
            )
            current_ids = set(presets.values_list("pk", flat=True))
            if first_ids is None:
                first_ids = current_ids
            else:
                self.assertEqual(current_ids, first_ids)

        self.assertEqual(Requirement.objects.filter(store=self.store).count(), 18)
        other_preset.refresh_from_db()
        self.assertEqual(other_preset.memo, "別店舗の候補")
        self.assertEqual(RequirementTimePreset.objects.count(), 3)

    def test_replace_removes_old_requirements_but_keeps_saved_time_presets(self):
        old_response = self.client.post(self.url, bulk_data(**{
            "weekday-0-start_time": "08:00", "weekday-0-end_time": "12:00",
            "weekday-memo": "保存済みの時間帯",
        }))
        self.assertRedirects(old_response, reverse("manager_requirement_list") + "?year=2026&month=10")
        old_requirement = Requirement.objects.get(store=self.store)
        old_preset = RequirementTimePreset.objects.get(store=self.store)
        other_preset = self.preset(store=self.other_store)

        response = self.client.post(
            self.url, bulk_data(mode="replace", **{"weekday-memo": "置き換え後のメモ"}),
        )

        self.assertRedirects(response, reverse("manager_requirement_list") + "?year=2026&month=10")
        self.assertFalse(Requirement.objects.filter(pk=old_requirement.pk).exists())
        replacement = Requirement.objects.get(store=self.store)
        self.assertEqual(
            (replacement.start_time, replacement.end_time, replacement.memo),
            (time(9), time(10), "置き換え後のメモ"),
        )
        old_preset.refresh_from_db()
        self.assertEqual(
            (old_preset.start_time, old_preset.end_time, old_preset.memo),
            (time(8), time(12), "保存済みの時間帯"),
        )
        self.assertEqual(RequirementTimePreset.objects.filter(store=self.store).count(), 2)
        self.assertTrue(RequirementTimePreset.objects.filter(pk=other_preset.pk).exists())
