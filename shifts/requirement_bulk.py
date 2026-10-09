from datetime import timedelta

from django.db import transaction
from jpholiday import JPHoliday

from accounts.models import Store
from .models import Requirement
from .requirement_bulk_forms import DAY_TYPES


holiday_calendar = JPHoliday()


def day_type(work_date, weekend_policy="sat_sun"):
    def is_day_off(day):
        return day.weekday() in ((5, 6) if weekend_policy == "sat_sun" else (6,)) or holiday_calendar.is_holiday(day)

    # 連休中は休日を優先し、休日ではない前日だけを祝前日とする。
    if is_day_off(work_date):
        return "holiday"
    if is_day_off(work_date + timedelta(days=1)):
        return "holiday_eve"
    return "weekday"


def build_plan(cleaned_data, slots_by_category):
    dates_by_category = {key: [] for key in cleaned_data["categories"]}
    work_date = cleaned_data["start_date"]
    while work_date <= cleaned_data["end_date"]:
        category = day_type(work_date, cleaned_data["weekend_policy"])
        if category in dates_by_category:
            dates_by_category[category].append(work_date)
        work_date += timedelta(days=1)
    return [
        {"key": key, "label": label, "dates": dates_by_category[key], "slots": slots_by_category[key]}
        for key, label in DAY_TYPES if key in dates_by_category
    ]


@transaction.atomic
def apply_plan(store, plan, mode):
    # 同じ店舗への一括適用をまとめて保存する。
    Store.objects.select_for_update().get(pk=store.pk)
    dates = [day for group in plan for day in group["dates"]]
    if mode == "replace":
        Requirement.objects.filter(store=store, work_date__in=dates).delete()
    saved_count = 0
    for group in plan:
        for day in group["dates"]:
            for slot in group["slots"]:
                matches = Requirement.objects.filter(
                    store=store, work_date=day,
                    start_time=slot["start_time"], end_time=slot["end_time"],
                ).order_by("pk")
                existing = matches.first()
                if existing:
                    existing.required_staff_count = slot["required_staff_count"]
                    existing.save(update_fields=["required_staff_count"])
                    matches.exclude(pk=existing.pk).delete()
                else:
                    Requirement.objects.create(store=store, work_date=day, **slot)
                saved_count += 1
    return len(dates), saved_count
