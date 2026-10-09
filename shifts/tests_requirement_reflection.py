from datetime import date, time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Company, Store, StoreMembership
from scheduler.services import generate_shifts_for_store
from .models import Availability, Requirement, RequirementTimePreset, Shift


class LinkParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def bulk_month_data(**overrides):
    data = {
        "start_date": "2026-11-01", "end_date": "2026-11-30",
        "weekend_policy": "sat_sun", "mode": "update", "action": "apply",
        "categories": ["weekday", "holiday_eve", "holiday"],
    }
    for index, category in enumerate(data["categories"]):
        data.update({
            f"{category}-TOTAL_FORMS": "1", f"{category}-INITIAL_FORMS": "0",
            f"{category}-0-start_time": f"{9 + 2 * index:02d}:00",
            f"{category}-0-end_time": f"{10 + 2 * index:02d}:00",
            f"{category}-0-required_staff_count": str(index + 1),
            f"{category}-memo": f"{category}の共通メモ",
        })
    data.update(overrides)
    return data


@override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class RequirementReflectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="反映テスト会社")
        cls.store = Store.objects.create(company=company, name="反映対象店舗")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = get_user_model().objects.create_user(username="reflection-manager")
        cls.staff = get_user_model().objects.create_user(username="reflection-staff")
        cls.other_manager = get_user_model().objects.create_user(username="reflection-other-manager")
        cls.other_staff = get_user_model().objects.create_user(username="reflection-other-staff")
        StoreMembership.objects.create(user=cls.manager, store=cls.store, role="manager")
        cls.staff_membership = StoreMembership.objects.create(user=cls.staff, store=cls.store, role="staff")
        StoreMembership.objects.create(user=cls.other_manager, store=cls.other_store, role="manager")
        cls.other_staff_membership = StoreMembership.objects.create(
            user=cls.other_staff, store=cls.other_store, role="staff",
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.staff_client = Client()
        self.staff_client.force_login(self.staff)
        self.other_client = Client()
        self.other_client.force_login(self.other_manager)
        self.other_staff_client = Client()
        self.other_staff_client.force_login(self.other_staff)
        self.requirement_url = reverse("manager_requirement_list")
        self.bulk_url = reverse("manager_requirement_bulk")

    def assert_month_link(self, response, url_name, year, month, work_date=None):
        path = reverse(url_name)
        for href in LinkParser(response.content.decode()).hrefs:
            parsed = urlsplit(href)
            query = parse_qs(parsed.query)
            if parsed.path != path:
                continue
            if query.get("year") != [str(year)] or query.get("month") != [str(month)]:
                continue
            if work_date is None or query.get("date") == [work_date.isoformat()]:
                return
        self.fail(f"Missing month-preserving link to {path}: {year}/{month}, date={work_date}")

    def assert_day_reflected(self, work_date, expected):
        params = {"year": work_date.year, "month": work_date.month, "date": work_date.isoformat()}
        manager_response = self.client.get(reverse("manager_shift_list"), params)
        self.assertEqual(manager_response.status_code, 200)
        self.assertEqual(manager_response.context["selected_date"], work_date)
        requirements = manager_response.context["requirements"]
        self.assertEqual(list(requirements.values_list(
            "start_time", "end_time", "required_staff_count", "memo",
        )), expected)
        day_marker = next(
            day for week in manager_response.context["calendar_weeks"] for day in week
            if day["date"] == work_date
        )
        self.assertEqual(day_marker["has_requirement"], bool(expected))
        for start, end, count, _memo in expected:
            self.assertContains(manager_response, start.strftime("%H:%M"))
            self.assertContains(manager_response, end.strftime("%H:%M"))
            self.assertContains(manager_response, f"{count}人")

        staff_response = self.staff_client.get(reverse("availability_create"), params)
        self.assertEqual(staff_response.status_code, 200)
        self.assertEqual(staff_response.context["selected_date"], work_date)
        self.assertEqual(list(staff_response.context["requirements_for_date"].values_list(
            "start_time", "end_time", "required_staff_count", "memo",
        )), expected)
        for start, end, count, memo in expected:
            self.assertContains(staff_response, f'data-start-time="{start:%H:%M}"')
            self.assertContains(staff_response, f'data-end-time="{end:%H:%M}"')
            self.assertContains(staff_response, f"必要人数: {count}名")
            self.assertContains(staff_response, memo)

    def test_selected_month_is_kept_on_bulk_and_shift_calendar_links(self):
        response = self.client.get(self.requirement_url, {"browse_month": "2026-11"})
        self.assertEqual((response.context["year"], response.context["month"]), (2026, 11))
        self.assert_month_link(response, "manager_requirement_bulk", 2026, 11)
        self.assert_month_link(response, "manager_shift_list", 2026, 11)

        bulk_response = self.client.get(self.bulk_url, {"year": 2026, "month": 11})
        self.assertEqual(bulk_response.context["form"].initial["start_date"], date(2026, 11, 1))
        self.assertEqual(bulk_response.context["form"].initial["end_date"], date(2026, 11, 30))
        self.assert_month_link(bulk_response, "manager_requirement_list", 2026, 11)

    def test_next_month_bulk_apply_is_visible_in_all_target_days_and_generation(self):
        response = self.client.post(
            self.bulk_url + "?year=2026&month=11", bulk_month_data(),
        )
        self.assertRedirects(response, self.requirement_url + "?year=2026&month=11")
        listing = self.client.get(response["Location"])
        self.assertEqual((listing.context["year"], listing.context["month"]), (2026, 11))
        self.assertEqual(listing.context["requirements"].count(), 30)
        self.assertEqual(Requirement.objects.filter(store=self.store).count(), 30)
        self.assertFalse(Requirement.objects.filter(store=self.other_store).exists())
        self.assertEqual(RequirementTimePreset.objects.filter(store=self.store).count(), 3)
        self.assertFalse(RequirementTimePreset.objects.filter(store=self.other_store).exists())

        # 区分判定とは別に、実際の保存日すべてで消費側の表示を確認する。
        for requirement in Requirement.objects.filter(store=self.store):
            with self.subTest(work_date=requirement.work_date):
                self.assert_day_reflected(requirement.work_date, [(
                    requirement.start_time, requirement.end_time,
                    requirement.required_staff_count, requirement.memo,
                )])
                self.assert_month_link(
                    listing, "manager_shift_list", 2026, 11, requirement.work_date,
                )
        self.assert_day_reflected(date(2026, 10, 31), [])
        self.assert_day_reflected(date(2026, 12, 1), [])

        Availability.objects.create(
            user=self.staff, membership=self.staff_membership, work_date=date(2026, 11, 4),
            start_time=time(8), end_time=time(18),
        )
        result = generate_shifts_for_store(self.store.pk, date(2026, 11, 4))
        self.assertEqual(result, {"created_count": 1, "shortfall_count": 0, "requirement_count": 1})
        generated = Shift.objects.get()
        self.assertEqual((generated.store_id, generated.membership_id), (self.store.pk, self.staff_membership.pk))
        self.assertEqual((generated.start_time, generated.end_time), (time(9), time(10)))

    def test_bulk_preview_keeps_month_and_does_not_write_requirements_or_presets(self):
        response = self.client.post(
            self.bulk_url + "?year=2026&month=11", bulk_month_data(action="preview"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dates_count"], 30)
        self.assertEqual(response.context["slots_count"], 30)
        self.assertTrue(response.context["plan"])
        self.assert_month_link(response, "manager_requirement_list", 2026, 11)
        self.assertContains(response, 'value="apply"')
        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(RequirementTimePreset.objects.exists())

    def test_bulk_redirect_uses_saved_month_when_range_starts_in_an_unselected_category(self):
        response = self.client.post(
            self.bulk_url + "?year=2026&month=10", bulk_month_data(
                start_date="2026-10-30", end_date="2026-11-04",
                categories=["weekday"],
            ),
        )
        self.assertRedirects(response, self.requirement_url + "?year=2026&month=11")
        self.assertEqual(list(Requirement.objects.values_list("work_date", flat=True)), [date(2026, 11, 4)])
        self.assert_day_reflected(date(2026, 11, 4), [(time(9), time(10), 1, "weekdayの共通メモ")])
        self.assert_day_reflected(date(2026, 10, 30), [])
        self.assert_day_reflected(date(2026, 10, 31), [])
        self.assert_day_reflected(date(2026, 11, 2), [])
        self.assert_day_reflected(date(2026, 11, 3), [])

    def test_monthly_day_types_are_reflected_only_in_the_selected_month_and_dates(self):
        selected_cases = [
            ("monday", 9, [2, 9, 16, 23, 30], date(2026, 11, 23), date(2026, 11, 24)),
            ("weekday", 11, [2, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 24, 25, 26, 27, 30], date(2026, 11, 6), date(2026, 11, 3)),
            ("holiday", 13, [3, 23], date(2026, 11, 3), date(2026, 11, 1)),
        ]
        for day_type, hour, days, included, excluded in selected_cases:
            with self.subTest(day_type=day_type):
                target_response = self.client.get(self.requirement_url, {"browse_month": "2026-11"})
                self.assertEqual(target_response.context["form"].initial["target_month"], date(2026, 11, 1))
                data = {
                    "schedule_mode": "month", "target_month": "2026-11", "day_type": day_type,
                    "start_time": f"{hour:02d}:00", "end_time": f"{hour + 1:02d}:00",
                    "required_staff_count": "2", "memo": f"月設定:{day_type}",
                }
                response = self.client.post(self.requirement_url, data)
                self.assertRedirects(response, self.requirement_url + "?year=2026&month=11")
                saved = Requirement.objects.filter(store=self.store, start_time=time(hour))
                self.assertEqual(set(saved.values_list("work_date", flat=True)), {date(2026, 11, day) for day in days})
                self.assertEqual(set(saved.values_list("memo", flat=True)), {f"月設定:{day_type}"})
                expected = list(Requirement.objects.filter(store=self.store, work_date=included).values_list(
                    "start_time", "end_time", "required_staff_count", "memo",
                ))
                self.assert_day_reflected(included, expected)
                self.assertFalse(saved.filter(work_date=excluded).exists())
                self.assertFalse(saved.exclude(work_date__year=2026, work_date__month=11).exists())
        self.assert_day_reflected(date(2026, 10, 26), [])
        self.assert_day_reflected(date(2026, 12, 7), [])

    def test_bulk_reapply_updates_existing_dates_and_keeps_other_store_settings(self):
        other_requirement = Requirement.objects.create(
            store=self.other_store, work_date=date(2026, 11, 4), start_time=time(19),
            end_time=time(20), required_staff_count=11, memo="他店の設定",
        )
        response = self.client.post(self.bulk_url, bulk_month_data())
        self.assertRedirects(response, self.requirement_url + "?year=2026&month=11")
        original_ids = set(Requirement.objects.filter(store=self.store).values_list("pk", flat=True))
        update = bulk_month_data(**{"weekday-0-required_staff_count": "4", "weekday-memo": "更新した区分"})
        response = self.client.post(self.bulk_url, update)
        self.assertRedirects(response, self.requirement_url + "?year=2026&month=11")
        self.assertEqual(set(Requirement.objects.filter(store=self.store).values_list("pk", flat=True)), original_ids)
        self.assertEqual(Requirement.objects.filter(store=self.store).count(), 30)
        self.assert_day_reflected(date(2026, 11, 4), [(time(9), time(10), 4, "更新した区分")])
        other_requirement.refresh_from_db()
        self.assertEqual((other_requirement.required_staff_count, other_requirement.memo), (11, "他店の設定"))
        self.assertEqual(RequirementTimePreset.objects.filter(store=self.store).count(), 3)
        self.assertFalse(RequirementTimePreset.objects.filter(store=self.other_store).exists())

        params = {"year": 2026, "month": 11, "date": "2026-11-04"}
        other_manager_response = self.other_client.get(reverse("manager_shift_list"), params)
        self.assertEqual(list(other_manager_response.context["requirements"]), [other_requirement])
        other_staff_response = self.other_staff_client.get(reverse("availability_create"), params)
        self.assertEqual(list(other_staff_response.context["requirements_for_date"]), [other_requirement])
        self.assertNotContains(other_staff_response, "更新した区分")
