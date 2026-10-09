import calendar
from datetime import date

from django.db import transaction
from jpholiday import JPHoliday

from accounts.models import Store
from .models import Requirement, RequirementTimePreset


holiday_calendar = JPHoliday()
WEEKDAYS = {name: index for index, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
)}


def get_month_dates(target_month, day_type):
    days = [date(target_month.year, target_month.month, day) for day in range(
        1, calendar.monthrange(target_month.year, target_month.month)[1] + 1,
    )]
    if day_type == "weekday":
        return [day for day in days if day.weekday() < 5 and not holiday_calendar.is_holiday(day)]
    if day_type == "holiday":
        return [day for day in days if holiday_calendar.is_holiday(day)]
    return [day for day in days if day.weekday() == WEEKDAYS[day_type]]


def remember_time_preset(store, start_time, end_time, required_staff_count, memo=""):
    preset, _ = RequirementTimePreset.objects.update_or_create(
        store=store, start_time=start_time, end_time=end_time,
        defaults={"required_staff_count": required_staff_count, "memo": memo},
    )
    return preset


@transaction.atomic
def save_requirements(store, work_dates, slot):
    Store.objects.select_for_update().get(pk=store.pk)
    if not work_dates:
        return 0
    for work_date in work_dates:
        matches = Requirement.objects.filter(
            store=store, work_date=work_date,
            start_time=slot["start_time"], end_time=slot["end_time"],
        ).order_by("pk")
        existing = matches.first()
        if existing:
            update_fields = [field for field in ("required_staff_count", "memo", "day_type") if field in slot]
            for field in update_fields:
                setattr(existing, field, slot[field])
            existing.save(update_fields=update_fields)
            matches.exclude(pk=existing.pk).delete()
        else:
            existing = Requirement.objects.create(store=store, work_date=work_date, **slot)
    remember_time_preset(store, existing.start_time, existing.end_time, existing.required_staff_count, existing.memo)
    return len(work_dates)
