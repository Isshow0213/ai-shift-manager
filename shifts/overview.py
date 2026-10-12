from datetime import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import StoreMembership
from .calendar_utils import get_calendar_context
from .models import Shift, ShiftDisplayOrder


def order_overview_users(store, users):
    positions = dict(ShiftDisplayOrder.objects.filter(store=store).values_list(
        "user_id", "position",
    ))
    return sorted(users, key=lambda user: (
        user.pk not in positions, positions.get(user.pk, 0),
        user.last_name, user.first_name, user.username, user.pk,
    ))


def save_overview_order(store, user_ids):
    """表示中の行だけを入れ替え、他の月にだけ出る行の位置も保存する。

    呼び出し元で店舗をロックし、表示中の全行のIDであることを検証する。
    """
    visible_ids = set(user_ids)
    all_user_ids = set(StoreMembership.objects.filter(store=store).values_list(
        "user_id", flat=True,
    ))
    all_user_ids.update(Shift.objects.filter(store=store).values_list("user_id", flat=True))
    existing_orders = {
        order.user_id: order for order in ShiftDisplayOrder.objects.filter(store=store)
    }
    all_user_ids.update(existing_orders)
    users = order_overview_users(store, get_user_model().objects.filter(pk__in=all_user_ids))
    replacements = iter(user_ids)
    ordered_ids = [
        next(replacements) if user.pk in visible_ids else user.pk for user in users
    ]
    new_orders, changed_orders = [], []
    for position, user_id in enumerate(ordered_ids):
        order = existing_orders.get(user_id)
        if order is None:
            new_orders.append(ShiftDisplayOrder(
                store=store, user_id=user_id, position=position,
            ))
        elif order.position != position:
            order.position = position
            changed_orders.append(order)
    ShiftDisplayOrder.objects.bulk_create(new_orders)
    ShiftDisplayOrder.objects.bulk_update(changed_orders, ["position"])


def build_shift_overview_context(request, store, *, calendar_url_name=None):
    calendar_context = get_calendar_context(request)
    year, month = calendar_context["year"], calendar_context["month"]
    weekdays = ("月", "火", "水", "木", "金", "土", "日")
    calendar_url = reverse(calendar_url_name) if calendar_url_name else None
    month_days = []
    for week in calendar_context["calendar_weeks"]:
        for day in week:
            if day["is_current_month"]:
                work_date = day["date"]
                month_days.append({
                    "date": work_date,
                    "day": work_date.day,
                    "weekday": weekdays[work_date.weekday()],
                    "is_today": day["is_today"],
                    "is_weekend": work_date.weekday() >= 5,
                    "is_saturday": work_date.weekday() == 5,
                    "is_sunday": work_date.weekday() == 6,
                    "url": (
                        f"{calendar_url}?year={year}&month={month}"
                        f"&date={work_date.isoformat()}"
                        if calendar_url else None
                    ),
                })

    memberships = list(
        StoreMembership.objects.filter(store=store).select_related("user")
    )
    membership_by_user = {membership.user_id: membership for membership in memberships}
    users = {
        membership.user_id: membership.user
        for membership in memberships if membership.is_active
    }
    monthly_shifts = list(Shift.objects.filter(
        store=store, work_date__year=year, work_date__month=month,
    ).select_related("user").order_by("work_date", "start_time", "end_time", "pk"))

    shifts_by_user_date = {}
    scheduled_users = set()
    staff_by_date = {day["date"]: set() for day in month_days}
    minutes_by_user = {}
    for shift in monthly_shifts:
        users[shift.user_id] = shift.user
        scheduled_users.add(shift.user_id)
        staff_by_date[shift.work_date].add(shift.user_id)
        shifts_by_user_date.setdefault((shift.user_id, shift.work_date), []).append(shift)
        duration = (
            datetime.combine(shift.work_date, shift.end_time)
            - datetime.combine(shift.work_date, shift.start_time)
        )
        minutes_by_user[shift.user_id] = (
            minutes_by_user.get(shift.user_id, 0)
            + max(0, int(duration.total_seconds() // 60))
        )

    rows = []
    for user in order_overview_users(store, users.values()):
        membership = membership_by_user.get(user.pk)
        cells = [
            {
                "date": day["date"],
                "shifts": shifts_by_user_date.get((user.pk, day["date"]), []),
                "url": day["url"],
            }
            for day in month_days
        ]
        total_minutes = minutes_by_user.get(user.pk, 0)
        rows.append({
            "user": user,
            "membership": membership,
            "is_active": bool(membership and membership.is_active and user.is_active),
            "cells": cells,
            "working_days": sum(bool(cell["shifts"]) for cell in cells),
            "shift_count": sum(len(cell["shifts"]) for cell in cells),
            "total_minutes": total_minutes,
            "hours_display": f"{total_minutes // 60}:{total_minutes % 60:02d}",
        })

    total_minutes = sum(minutes_by_user.values())
    return {
        **calendar_context,
        "store": store,
        "month_days": month_days,
        "rows": rows,
        "days_count": len(month_days),
        "day_staff_counts": [len(staff_by_date[day["date"]]) for day in month_days],
        "total_shifts": len(monthly_shifts),
        "scheduled_staff_count": len(scheduled_users),
        "total_working_days": sum(row["working_days"] for row in rows),
        "total_hours_display": f"{total_minutes // 60}:{total_minutes % 60:02d}",
    }
