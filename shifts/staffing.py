"""登録済みシフトの同時出勤人数と、店長による不足警告の確認状態。"""
from collections import defaultdict
import hashlib
import json

from .models import HelpShift, Requirement, Shift, StaffingWarningDismissal


def _staffing_signature(requirements, shifts, help_shifts):
    # 人数に関係する内容だけを比較する。表示色やメモの変更では再警告しない。
    data = {
        "requirements": sorted(
            (item.start_time.isoformat(), item.end_time.isoformat(), item.required_staff_count)
            for item in requirements
        ),
        "shifts": sorted(
            (item.user_id, item.start_time.isoformat(), item.end_time.isoformat())
            for item in shifts
        ),
        "help_shifts": sorted(
            (item.name, item.start_time.isoformat(), item.end_time.isoformat())
            for item in help_shifts
        ),
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _shortages(requirements, shifts, help_shifts):
    boundaries = sorted({
        point
        for item in [*requirements, *shifts, *help_shifts]
        for point in (item.start_time, item.end_time)
    })
    shortages = []
    for start, end in zip(boundaries, boundaries[1:]):
        # 重なる必要人数は加算しない。同じ人が複数の設定を満たせるため、最大値を使う。
        required = max((
            item.required_staff_count for item in requirements
            if item.start_time <= start and item.end_time >= end
        ), default=0)
        if not required:
            continue
        users = {
            item.user_id for item in shifts
            if item.start_time <= start and item.end_time >= end
        }
        helpers = {
            item.name for item in help_shifts
            if item.start_time <= start and item.end_time >= end
        }
        scheduled = len(users) + len(helpers)
        if scheduled >= required:
            continue
        if (
            shortages and shortages[-1]["end_time"] == start
            and shortages[-1]["required_staff_count"] == required
            and shortages[-1]["scheduled_staff_count"] == scheduled
        ):
            shortages[-1]["end_time"] = end
        else:
            shortages.append({
                "start_time": start, "end_time": end,
                "required_staff_count": required,
                "scheduled_staff_count": scheduled,
                "shortfall_count": required - scheduled,
            })
    return shortages


def build_staffing_statuses(requirements, shifts, help_shifts, dismissals=()):
    """月全体を一度の読込で判定する。入力は同じ店舗のデータに限定する。"""
    requirements_by_date = defaultdict(list)
    shifts_by_date = defaultdict(list)
    help_by_date = defaultdict(list)
    for item in requirements:
        requirements_by_date[item.work_date].append(item)
    for item in shifts:
        shifts_by_date[item.work_date].append(item)
    for item in help_shifts:
        help_by_date[item.work_date].append(item)
    dismissals_by_date = {item.work_date: item for item in dismissals}

    statuses = {}
    for work_date, day_requirements in requirements_by_date.items():
        day_shifts = shifts_by_date[work_date]
        day_help = help_by_date[work_date]
        signature = _staffing_signature(day_requirements, day_shifts, day_help)
        shortages = _shortages(day_requirements, day_shifts, day_help)
        dismissal = dismissals_by_date.get(work_date)
        is_dismissed = bool(
            shortages and dismissal and dismissal.staffing_signature == signature
        )
        statuses[work_date] = {
            "has_shortage": bool(shortages),
            "is_warning_dismissed": is_dismissed,
            "show_warning": bool(shortages) and not is_dismissed,
            "shortages": shortages,
            "staffing_signature": signature,
            "dismissal": dismissal if is_dismissed else None,
            "max_shortfall": max((item["shortfall_count"] for item in shortages), default=0),
        }
    return statuses


def get_staffing_status(store, work_date):
    return build_staffing_statuses(
        Requirement.objects.filter(store=store, work_date=work_date),
        Shift.objects.filter(store=store, work_date=work_date),
        HelpShift.objects.filter(store=store, work_date=work_date),
        StaffingWarningDismissal.objects.filter(store=store, work_date=work_date),
    ).get(work_date)
