import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import StoreSubmissionDeadline


JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class SubmissionPeriod:
    start_date: date
    end_date: date
    deadline_date: date
    closes_at: datetime


def get_store_submission_deadline(store):
    if store is None:
        return None
    return StoreSubmissionDeadline.objects.filter(store=store).first()


def get_submission_period(policy, work_date):
    if policy is None:
        return None
    if not 2 <= work_date.year <= 9998:
        raise ValueError("締切の計算に対応する日付は2年〜9998年です。")

    if policy.mode == StoreSubmissionDeadline.Mode.WEEKLY:
        start_date = work_date - timedelta(days=work_date.weekday())
        end_date = start_date + timedelta(days=6)
        deadline_date = start_date - timedelta(days=7) + timedelta(days=policy.weekly_deadline_weekday)
    else:
        start_date = work_date.replace(day=1)
        end_date = work_date.replace(day=calendar.monthrange(work_date.year, work_date.month)[1])
        previous_month_end = start_date - timedelta(days=1)
        deadline_date = previous_month_end.replace(
            day=min(policy.monthly_deadline_day, previous_month_end.day),
        )

    closes_at = datetime.combine(deadline_date + timedelta(days=1), time.min, tzinfo=JAPAN_TIMEZONE)
    return SubmissionPeriod(start_date, end_date, deadline_date, closes_at)


def is_submission_closed(policy, work_date, now=None):
    if policy is None:
        return False
    if work_date.year < 2 or work_date.year > 9998:
        return work_date.year < 2
    period = get_submission_period(policy, work_date)
    return bool(period and (now if now is not None else timezone.now()) >= period.closes_at)


def submission_context(policy, work_date, now=None):
    return {
        "submission_deadline_configured": policy is not None,
        "submission_period": get_submission_period(policy, work_date),
        "submission_closed": is_submission_closed(policy, work_date, now=now),
    }
