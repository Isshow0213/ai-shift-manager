"""提出状態と勤務可能時間の判定を、従業員画面・手動作成・自動生成で共有する。"""
import calendar
from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from accounts.models import StoreMembership
from .models import Availability, DayOffRequest, ShiftSubmission
from .submission_deadlines import JAPAN_TIMEZONE, get_store_submission_deadline, get_submission_period


def month_start(work_date):
    return work_date.replace(day=1)


def effective_submission_mode(membership, work_date):
    submission = get_month_submission(membership, work_date)
    return submission.submission_mode if submission else membership.submission_mode


def get_month_submission(membership, work_date, create=False):
    if membership is None:
        return None
    target_month = month_start(work_date)
    if create:
        submission, _ = ShiftSubmission.objects.get_or_create(
            membership=membership, target_month=target_month,
            defaults={"submission_mode": membership.submission_mode},
        )
    else:
        submission = ShiftSubmission.objects.filter(membership=membership, target_month=target_month).first()
    if submission and not submission.is_submitted and submission.submitted_at is None:
        if submission.submission_mode != membership.submission_mode:
            submission.submission_mode = membership.submission_mode
            if create:
                submission.save(update_fields=["submission_mode", "updated_at"])
    return submission


def month_deadline_date(store, work_date):
    policy = get_store_submission_deadline(store)
    if policy is None:
        return None
    first = month_start(work_date)
    periods = [get_submission_period(policy, date(first.year, first.month, day))
               for day in range(1, calendar.monthrange(first.year, first.month)[1] + 1)]
    return min(period.deadline_date for period in periods if period is not None)


def month_edit_closed(store, work_date, now=None):
    deadline = month_deadline_date(store, work_date)
    if deadline is None:
        return False
    return timezone.localdate(now or timezone.now(), timezone=JAPAN_TIMEZONE) > deadline


@dataclass(frozen=True)
class DayEligibility:
    available: bool
    all_day: bool = False
    intervals: tuple = ()
    reason: str = "available"
    submission: object = None

    def covers(self, start_time, end_time):
        if not self.available or not start_time or not end_time or start_time >= end_time:
            return False
        if self.all_day:
            return True
        covered_until = start_time
        for start, end in self.intervals:
            if end <= covered_until:
                continue
            if start > covered_until:
                return False
            covered_until = max(covered_until, end)
            if covered_until >= end_time:
                return True
        return False


class EligibilityIndex:
    """一日の候補をまとめて取得し、候補人数に比例したDB照会を避ける。"""
    def __init__(self, store, work_date, require_submitted=True):
        self.store = store
        self.work_date = work_date
        self.require_submitted = require_submitted
        self.memberships = list(StoreMembership.objects.filter(
            store=store, is_active=True, user__is_active=True, role__in=["manager", "staff"],
        ).select_related("user", "store"))
        member_ids = [member.pk for member in self.memberships]
        self.submissions = {submission.membership_id: submission for submission in ShiftSubmission.objects.filter(
            membership_id__in=member_ids, target_month=month_start(work_date),
        )}
        self.day_off_ids = set(DayOffRequest.objects.filter(
            submission_id__in=[submission.pk for submission in self.submissions.values()], work_date=work_date,
        ).values_list("submission_id", flat=True))
        self.intervals = {}
        self.availabilities = {}
        for availability in Availability.objects.filter(
            membership_id__in=member_ids, work_date=work_date,
        ).order_by("start_time", "end_time", "pk"):
            submission = self.submissions.get(availability.membership_id)
            if availability.submission_id is not None and (
                submission is None or availability.submission_id != submission.pk
            ):
                continue
            if availability.start_time < availability.end_time:
                self.intervals.setdefault(availability.membership_id, []).append((availability.start_time, availability.end_time))
                self.availabilities.setdefault(availability.membership_id, []).append(availability)

    def for_membership(self, membership):
        if not membership.is_active or not membership.user.is_active or membership.store_id != self.store.pk:
            return DayEligibility(False, reason="inactive")
        if membership.role not in ("manager", "staff"):
            return DayEligibility(False, reason="inactive")
        submission = self.submissions.get(membership.pk)
        if self.require_submitted and (submission is None or not submission.is_submitted):
            return DayEligibility(False, reason="not_submitted", submission=submission)
        mode = submission.submission_mode if submission else membership.submission_mode
        if submission and not submission.is_submitted and submission.submitted_at is None:
            mode = membership.submission_mode
        if mode == "day_off" and submission and submission.pk in self.day_off_ids:
            return DayEligibility(False, reason="day_off", submission=submission)
        intervals = tuple(self.intervals.get(membership.pk, []))
        if intervals:
            return DayEligibility(True, intervals=intervals, submission=submission)
        if mode == "day_off":
            return DayEligibility(True, all_day=True, submission=submission)
        return DayEligibility(False, reason="no_availability", submission=submission)


def get_eligibility(membership, work_date, require_submitted=True):
    if not membership:
        return DayEligibility(False, reason="inactive")
    return EligibilityIndex(membership.store, work_date, require_submitted=require_submitted).for_membership(membership)


def eligible_membership_ids(store, work_date):
    index = EligibilityIndex(store, work_date)
    return [membership.pk for membership in index.memberships if index.for_membership(membership).available]


def submitted_availability_dates(store, year, month):
    first = date(year, month, 1)
    submissions = list(ShiftSubmission.objects.filter(
        membership__store=store, membership__is_active=True, membership__user__is_active=True,
        is_submitted=True, target_month=first,
    ))
    by_member = {submission.membership_id: submission for submission in submissions}
    off_by_submission = {}
    for submission_id, work_date in DayOffRequest.objects.filter(
        submission_id__in=[submission.pk for submission in submissions],
    ).values_list("submission_id", "work_date"):
        off_by_submission.setdefault(submission_id, set()).add(work_date)
    available_dates = set()
    for availability in Availability.objects.filter(
        membership_id__in=by_member, work_date__year=year, work_date__month=month,
    ):
        submission = by_member[availability.membership_id]
        if availability.submission_id not in (None, submission.pk):
            continue
        if submission.submission_mode != "day_off" or availability.work_date not in off_by_submission.get(submission.pk, set()):
            available_dates.add(availability.work_date)
    for submission in submissions:
        if submission.submission_mode == "day_off":
            available_dates.update(date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1)
                                   if date(year, month, day) not in off_by_submission.get(submission.pk, set()))
    return available_dates
