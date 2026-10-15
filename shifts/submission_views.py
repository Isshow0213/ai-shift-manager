from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import StoreMembership
from accounts.shift_access import membership_has_permission, select_submission_store

from .calendar_utils import get_calendar_context
from .eligibility import effective_submission_mode, get_month_submission, month_deadline_date, month_edit_closed
from .submission_forms import SubmissionSettingsForm
from .submission_deadlines import get_store_submission_deadline


def submission_return_url(store, work_date):
    return (
        f"{reverse('availability_list')}?year={work_date.year}"
        f"&month={work_date.month}&date={work_date.isoformat()}"
        + (f"&store={store.pk}" if store else "")
    )


def mark_submission_draft(submission):
    if submission.is_submitted:
        submission.is_submitted = False
        # 最後の提出日時は、その月の提出方式を固定する履歴として残す。
        submission.save(update_fields=["is_submitted"])


def monthly_submission_context(membership, work_date):
    submission = get_month_submission(membership, work_date) if membership else None
    mode = effective_submission_mode(membership, work_date) if membership else "availability"
    store = membership.store if membership else None
    deadline_policy = get_store_submission_deadline(store)
    return {
        "monthly_submission": submission,
        "submission_mode": mode,
        "submission_mode_label": "休み希望方式" if mode == "day_off" else "出勤希望方式",
        "is_day_off_mode": mode == "day_off",
        "month_submitted": bool(submission and submission.is_submitted),
        "month_submission_closed": bool(store and month_edit_closed(store, work_date)),
        "month_submission_deadline": month_deadline_date(store, work_date) if store else None,
        "month_deadline_is_weekly": bool(deadline_policy and deadline_policy.mode == "weekly"),
        "monthly_submit_url": (
            f"{reverse('monthly_submission_submit')}?store={store.pk}"
            f"&year={work_date.year}&month={work_date.month}"
        ) if store else "",
        "settings_url": (
            f"{reverse('submission_settings')}?store={store.pk}&year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        ) if store else reverse("submission_settings"),
    }


@login_required
@require_http_methods(["GET", "POST"])
def submission_settings(request):
    from .views import _submission_access_context

    store, membership = select_submission_store(request.user, request.GET.get("store"))
    if membership is None:
        messages.error(request, "所属している店舗がありません。")
        return redirect("availability_list")
    if not membership_has_permission(membership, "can_submit_availability"):
        raise PermissionDenied("この店舗へのシフト希望の提出権限がありません。")
    calendar_context = get_calendar_context(request)
    selected_date = calendar_context["selected_date"]
    form = SubmissionSettingsForm(request.POST or None, instance=membership)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_membership = StoreMembership.objects.select_for_update().get(pk=membership.pk)
            locked_membership.submission_mode = form.cleaned_data["submission_mode"]
            locked_membership.save(update_fields=["submission_mode"])
        messages.success(request, "提出方式を保存しました。提出済みの月の方式は変わりません。")
        return redirect(
            f"{reverse('submission_settings')}?store={store.pk}&year={selected_date.year}"
            f"&month={selected_date.month}&date={selected_date.isoformat()}"
        )
    return render(request, "shifts/submission_settings.html", {
        **_submission_access_context(request, store, membership),
        **calendar_context,
        "store": store, "form": form,
    })


@login_required
@require_POST
def monthly_submission_submit(request):
    store, membership = select_submission_store(request.user, request.GET.get("store"))
    if membership is None:
        messages.error(request, "所属している店舗がありません。")
        return redirect("availability_list")
    if not membership_has_permission(membership, "can_submit_availability"):
        raise PermissionDenied("この店舗へのシフト希望の提出権限がありません。")
    calendar_context = get_calendar_context(request)
    target_month = calendar_context["selected_date"].replace(day=1)
    return_url = submission_return_url(store, target_month)
    if month_edit_closed(store, target_month):
        messages.error(request, "提出締切を過ぎているため、この月の希望は提出できません。")
        return redirect(return_url)
    with transaction.atomic():
        locked_membership = StoreMembership.objects.select_for_update().get(pk=membership.pk)
        submission = get_month_submission(locked_membership, target_month, create=True)
        submission.is_submitted = True
        submission.submitted_at = timezone.now()
        submission.save()
    messages.success(request, f"{target_month:%Y年%m月}のシフト希望を提出しました。")
    return redirect(return_url)
