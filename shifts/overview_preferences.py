"""Private wish and consecutive-day annotations for the manager overview only."""
from datetime import date, timedelta

from .models import Availability, DayOffRequest, Shift, ShiftSubmission


def _merge_intervals(intervals):
    """Compare covered time, independently of duplicate or split records."""
    merged = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _consecutive_lengths(user_ids, first, last):
    """Read this month plus just enough history to finish boundary streaks.

    Usually one bounded date query suffices. Only people who worked every day
    back to the loaded boundary need another, progressively larger, window.
    Work at other stores counts, without exposing its times or notes.
    """
    worked = {user_id: set() for user_id in user_ids}
    window = 31
    lower = date.fromordinal(max(1, first.toordinal() - window))
    query = Shift.objects.filter(user_id__in=user_ids, work_date__range=(lower, last))
    for user_id, work_date in query.order_by().values_list("user_id", "work_date").distinct():
        worked[user_id].add(work_date)

    # A person who does not work on the first day cannot carry a streak in.
    pending = {user_id for user_id, dates in worked.items()
               if first in dates and all(date.fromordinal(ordinal) in dates
                                         for ordinal in range(lower.toordinal(), first.toordinal()))}
    while pending and lower > date.min:
        previous_end = lower - timedelta(days=1)
        window *= 2
        previous_start = date.fromordinal(max(1, lower.toordinal() - window))
        query = Shift.objects.filter(user_id__in=pending,
                                     work_date__range=(previous_start, previous_end))
        batch = {user_id: set() for user_id in pending}
        for user_id, work_date in query.order_by().values_list("user_id", "work_date").distinct():
            batch[user_id].add(work_date)
            worked[user_id].add(work_date)
        expected = (previous_end - previous_start).days + 1
        pending = {user_id for user_id in pending if len(batch[user_id]) == expected}
        lower = previous_start

    lengths = {}
    for user_id, dates in worked.items():
        previous = None
        count = 0
        for work_date in sorted(dates):
            count = count + 1 if previous and (work_date - previous).days == 1 else 1
            if first <= work_date <= last:
                lengths[user_id, work_date] = count
            previous = work_date
    return lengths


def annotate_manager_overview(context, store):
    days = context["month_days"]
    if not days:
        return
    first, last = days[0]["date"], days[-1]["date"]
    rows = context["rows"]
    member_ids = [row["membership"].pk for row in rows if row["membership"]]
    submissions = {s.membership_id: s for s in ShiftSubmission.objects.filter(
        membership__store=store, membership_id__in=member_ids,
        target_month=first, is_submitted=True)}
    wishes = {}
    for availability in Availability.objects.filter(
            membership_id__in=submissions, work_date__range=(first, last)
    ).order_by("start_time", "end_time"):
        if availability.submission_id in (None, submissions[availability.membership_id].pk):
            wishes.setdefault((availability.membership_id, availability.work_date), []).append(
                (availability.start_time, availability.end_time))
    off = set(DayOffRequest.objects.filter(
        submission_id__in=[s.pk for s in submissions.values()], work_date__range=(first, last)
    ).values_list("submission_id", "work_date"))
    lengths = _consecutive_lengths([row["user"].pk for row in rows], first, last)

    for row in rows:
        membership = row["membership"]
        submission = submissions.get(membership.pk) if membership else None
        for cell in row["cells"]:
            day = cell["date"]
            actual = _merge_intervals((shift.start_time, shift.end_time) for shift in cell["shifts"])
            preferred = _merge_intervals(wishes.get((membership.pk, day), [])) if membership else []
            label = ""
            if submission:
                if submission.submission_mode == "day_off" and (submission.pk, day) in off:
                    if actual:
                        label = "希望：休み"
                elif preferred and preferred != actual:
                    label = "希望：" + "、".join(f"{start:%H:%M}〜{end:%H:%M}" for start, end in preferred)
                elif submission.submission_mode == "availability" and not preferred and actual:
                    label = "希望：出勤なし"
            cell["unmet_preference"] = label
            count = lengths.get((row["user"].pk, day), 0)
            cell["consecutive_days"] = count if cell["shifts"] and count >= 2 else 0
