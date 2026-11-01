"""Shared daily wish saving and store-scoped time suggestions."""
from types import SimpleNamespace

from django.db import transaction
from django.db.models import F, Max

from accounts.models import StoreMembership
from accounts.shift_access import membership_has_permission
from .eligibility import get_month_submission, month_edit_closed
from .models import Availability, DayOffRequest, Requirement, StoreOperatingHours
from .submission_views import mark_submission_draft


def availability_time_suggestions(user, store, work_date):
    """The five most recently used distinct time pairs for this person/store."""
    if store is None:
        return {
            "previous_availabilities_unique": [],
            "requirements_for_date": [],
            "operating_hours": None,
            "has_full_day_hours": False,
        }
    previous = (
        Availability.objects.filter(user=user, work_date__lt=work_date,
                                    end_time__gt=F("start_time"))
        .for_store(store).order_by().values("start_time", "end_time")
        .annotate(last_work_date=Max("work_date"))
        .order_by("-last_work_date", "-start_time", "-end_time")[:5]
    )
    history = [SimpleNamespace(
        start_time=row["start_time"], end_time=row["end_time"],
        work_date=row["last_work_date"],
    ) for row in previous]
    hours = StoreOperatingHours.objects.filter(store=store).first()
    return {
        "previous_availabilities_unique": history,
        "requirements_for_date": Requirement.objects.filter(
            store=store, work_date=work_date, end_time__gt=F("start_time"),
        ).order_by("start_time", "end_time", "pk"),
        "operating_hours": hours,
        "has_full_day_hours": bool(
            hours and hours.start_time is not None and hours.end_time is not None
            and hours.start_time < hours.end_time
        ),
    }


def save_daily_availability(form, *, user, store, membership, submission_mode,
                            month_closed=False):
    """Save a valid daily setting as draft, preserving all submission checks."""
    if not form.is_valid():
        return False
    work_date = form.cleaned_data["work_date"]
    if month_closed or month_edit_closed(store, work_date):
        form.add_error(None, "提出締切を過ぎているため、この月の希望は変更できません。")
        return False
    with transaction.atomic():
        locked_membership = StoreMembership.objects.select_for_update().get(pk=membership.pk)
        if not membership_has_permission(locked_membership, "can_submit_availability"):
            form.add_error(None, "この店舗へのシフト希望の提出権限がありません。")
            return False
        submission = get_month_submission(locked_membership, work_date, create=True)
        if submission.submission_mode != submission_mode:
            form.add_error(None, "提出方式が変更されました。画面を再読み込みしてください。")
            return False
        day_status = form.cleaned_data["day_status"]
        day_availabilities = Availability.objects.filter(
            user=user, work_date=work_date,
        ).for_store(store)
        day_off_requests = DayOffRequest.objects.filter(
            submission=submission, work_date=work_date,
        )
        if day_status == "available":
            day_off_requests.delete()
            availability = form.save(commit=False)
            availability.user = user
            availability.membership = locked_membership
            availability.store = store
            availability.submission = submission
            availability.save()
        else:
            day_availabilities.delete()
            if day_status == "day_off":
                DayOffRequest.objects.get_or_create(submission=submission, work_date=work_date)
            else:
                day_off_requests.delete()
        mark_submission_draft(submission)
    return True
