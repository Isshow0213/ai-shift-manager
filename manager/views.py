from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from shifts.forms import AvailabilityForm
from shifts.models import (
    Availability, Requirement, Shift, HelpShift, DayOffRequest, StoreOperatingHours,
    StaffingWarningDismissal,
)
from shifts.staffing import build_staffing_statuses, get_staffing_status
from shifts.eligibility import EligibilityIndex, get_month_submission, submitted_availability_dates
from .help_forms import HelpShiftForm
from accounts.models import StaffInvitation, Store, StoreMembership
from .forms import (
    AvailabilityShiftForm, OutsideAvailabilityShiftForm, ShiftForm,
    StaffDesiredShiftsForm, StaffPermissionsForm, StaffRankForm, StaffingWarningForm,
)
from shifts.calendar_utils import get_calendar_context
from shifts.overview import build_shift_overview_context, save_overview_order

@login_required
@require_GET
def staff_list(request):
    manager_membership = _manager_membership(request.user)

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store

    return _render_staff_list(request, store)


def _render_staff_list(
    request, store, rank_membership_id=None, rank_form=None, *,
    desired_shifts_membership_id=None, desired_shifts_form=None,
    permissions_membership_id=None, permissions_form=None,
):

    memberships = StoreMembership.objects.filter(
        store=store,
    ).select_related("user").order_by(
        "role", "user__last_name", "user__first_name", "user__username"
    )
    for membership in memberships:
        membership.rank_form = (
            rank_form if membership.pk == rank_membership_id else StaffRankForm(
                initial={"rank": membership.rank}, auto_id=f"membership-{membership.pk}-%s",
            )
        )
        membership.desired_shifts_form = (
            desired_shifts_form
            if membership.pk == desired_shifts_membership_id else StaffDesiredShiftsForm(
                initial={"desired_shifts_per_week": membership.desired_shifts_per_week},
                auto_id=f"membership-{membership.pk}-desired-%s",
            )
        )
        membership.permissions_form = None
        if membership.role == "staff":
            membership.permissions_form = (
                permissions_form
                if membership.pk == permissions_membership_id else StaffPermissionsForm(
                    initial={
                        "can_view_shifts": membership.can_view_shifts,
                        "can_submit_availability": membership.can_submit_availability,
                        "show_in_shift_overview": membership.show_in_shift_overview,
                    },
                    auto_id=f"membership-{membership.pk}-permissions-%s",
                )
            )
    invitations = list(
        StaffInvitation.objects.filter(store=store)
        .select_related("used_by")
        .order_by("-created_at", "-pk")
    )
    now = timezone.now()
    for invitation in invitations:
        invitation.can_share = invitation.is_usable
        invitation.join_url = request.build_absolute_uri(
            reverse("staff_invitation_accept", args=[invitation.token])
        )
        if invitation.used_at is not None:
            invitation.status_label = "使用済み"
            invitation.status_class = "badge-blue"
        elif invitation.revoked_at is not None:
            invitation.status_label = "無効化"
            invitation.status_class = "badge-muted"
        elif invitation.expires_at <= now:
            invitation.status_label = "期限切れ"
            invitation.status_class = "badge-orange"
        elif not invitation.can_share:
            invitation.status_label = "無効化"
            invitation.status_class = "badge-muted"
        else:
            invitation.status_label = "未使用"
            invitation.status_class = "badge-green"
        invitation.can_revoke = (
            invitation.used_at is None and invitation.revoked_at is None
        )

    return render(
        request,
        "manager/staff_list.html",
        {
            "store": store,
            "memberships": memberships,
            "invitations": invitations,
        },
    )


@login_required
@require_POST
def staff_rank_update(request, membership_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    membership = get_object_or_404(
        StoreMembership.objects.select_related("user"), pk=membership_id, store=store,
    )
    form = StaffRankForm(request.POST, auto_id=f"membership-{membership.pk}-%s")
    if form.is_valid():
        rank = form.cleaned_data["rank"]
        StoreMembership.objects.filter(pk=membership.pk, store=store).update(rank=rank)
        messages.success(request, f"{membership.user.full_name_japanese}さんのランクを{rank}に変更しました。")
        return redirect("manager_staff_list")

    return _render_staff_list(request, store, rank_membership_id=membership.pk, rank_form=form)


@login_required
@require_POST
def staff_desired_shifts_update(request, membership_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    membership = get_object_or_404(
        StoreMembership.objects.select_related("user"), pk=membership_id, store=store,
    )
    form = StaffDesiredShiftsForm(
        request.POST, auto_id=f"membership-{membership.pk}-desired-%s",
    )
    if form.is_valid():
        desired_shifts = form.cleaned_data["desired_shifts_per_week"]
        StoreMembership.objects.filter(pk=membership.pk, store=store).update(
            desired_shifts_per_week=desired_shifts,
        )
        messages.success(
            request,
            f"{membership.user.full_name_japanese}さんの希望出勤数を週{desired_shifts}回に変更しました。",
        )
        return redirect("manager_staff_list")

    return _render_staff_list(
        request, store, desired_shifts_membership_id=membership.pk, desired_shifts_form=form,
    )


@login_required
@require_POST
def staff_permissions_update(request, membership_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    membership = get_object_or_404(
        StoreMembership.objects.select_related("user"),
        pk=membership_id, store=store,
    )
    if membership.role != "staff":
        raise PermissionDenied("管理者の権限は変更できません。")
    form = StaffPermissionsForm(
        request.POST, auto_id=f"membership-{membership.pk}-permissions-%s",
    )
    if form.is_valid():
        StoreMembership.objects.filter(
            pk=membership.pk, store=store, role="staff",
        ).update(**form.cleaned_data)
        messages.success(
            request, f"{membership.user.full_name_japanese}さんの店舗スタッフの権限を保存しました。",
        )
        return redirect("manager_staff_list")

    return _render_staff_list(
        request, store, permissions_membership_id=membership.pk, permissions_form=form,
    )


@login_required
@require_POST
def staff_invite_create(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    StaffInvitation.objects.create(
        store=manager_membership.store, created_by=request.user
    )
    messages.success(request, "従業員の招待リンクを発行しました。")
    return redirect("manager_staff_list")


@login_required
@require_POST
def staff_invite_revoke(request, invitation_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    invitation = get_object_or_404(
        StaffInvitation, pk=invitation_id, store=manager_membership.store
    )
    updated = StaffInvitation.objects.filter(
        pk=invitation.pk, used_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    if updated:
        messages.success(request, "招待リンクを無効化しました。")
    else:
        messages.info(request, "この招待リンクはすでに使用済み、または無効化されています。")
    return redirect("manager_staff_list")

@login_required
def shift_list(request):
    manager_membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related(
        "store",
        "store__company",
    ).first()

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    calendar_context = get_calendar_context(request)
    year = calendar_context["year"]
    month = calendar_context["month"]
    selected_date = calendar_context["selected_date"]

    monthly_availabilities = Availability.objects.for_store(store).filter(
        work_date__year=year,
        work_date__month=month,
    ).select_related("user")

    monthly_requirements = list(Requirement.objects.filter(
        store=store,
        work_date__year=year,
        work_date__month=month,
    ))

    monthly_shifts = list(Shift.objects.filter(
        store=store,
        work_date__year=year,
        work_date__month=month,
    ))
    monthly_help_shifts = list(HelpShift.objects.filter(
        store=store, work_date__year=year, work_date__month=month,
    ))
    monthly_dismissals = StaffingWarningDismissal.objects.filter(
        store=store, work_date__year=year, work_date__month=month,
    ).select_related("dismissed_by")
    staffing_statuses = build_staffing_statuses(
        monthly_requirements, monthly_shifts, monthly_help_shifts, monthly_dismissals,
    )

    availability_dates = submitted_availability_dates(store, year, month)

    requirement_dates = {requirement.work_date for requirement in monthly_requirements}

    shift_dates = {shift.work_date for shift in monthly_shifts}
    shift_dates.update(shift.work_date for shift in monthly_help_shifts)

    for week in calendar_context["calendar_weeks"]:
        for day in week:
            day["has_availability"] = day["date"] in availability_dates
            day["has_requirement"] = day["date"] in requirement_dates
            day["has_shift"] = day["date"] in shift_dates
            staffing = staffing_statuses.get(day["date"])
            day["show_staffing_warning"] = bool(staffing and staffing["show_warning"])
            day["staffing_warning_dismissed"] = bool(staffing and staffing["is_warning_dismissed"])
            day["max_staffing_shortfall"] = staffing["max_shortfall"] if staffing else 0

    availabilities = Availability.objects.for_store(store).filter(
        work_date=selected_date,
    ).select_related(
        "user",
        "membership",
        "membership__user",
    ).order_by(
        "start_time",
        "end_time",
        "membership__user__username",
        "pk",
    )

    requirements = Requirement.objects.filter(
        store=store,
        work_date=selected_date,
    ).order_by(
        "start_time",
    )

    shifts = Shift.objects.filter(
        store=store,
        work_date=selected_date,
    ).select_related(
        "user",
        "membership",
        "membership__user",
        "store",
    ).order_by(
        "start_time",
        "membership__user__username",
    )

    submitted_mode = (
        request.POST.get("shift_mode", "availability")
        if request.method == "POST" else None
    )
    form = AvailabilityShiftForm(
        request.POST if request.method == "POST" and submitted_mode not in ("outside", "help") else None,
        store=store, selected_date=selected_date, initial={"work_date": selected_date},
    )
    outside_form = OutsideAvailabilityShiftForm(
        request.POST if request.method == "POST" and submitted_mode == "outside" else None,
        store=store, selected_date=selected_date,
        initial={"work_date": selected_date}, prefix="outside",
    )
    help_form = HelpShiftForm(
        request.POST if request.method == "POST" and submitted_mode == "help" else None,
        store=store, selected_date=selected_date,
        initial={"work_date": selected_date}, prefix="help",
    )
    help_shifts = HelpShift.objects.filter(store=store, work_date=selected_date)

    if request.method == "POST":
        if submitted_mode == "help":
            if help_form.is_valid():
                help_shift = help_form.save()
                messages.success(request, "ヘルプのシフトを追加しました。")
                return redirect(_shift_calendar_url(help_shift.work_date))
        submitted_form = outside_form if submitted_mode == "outside" else form
        if submitted_mode not in ("availability", "outside", "help"):
            submitted_form.is_valid()
            submitted_form.add_error(
                None, "追加方法が不正です。画面の追加ボタンから操作してください。"
            )
        elif submitted_mode != "help" and submitted_form.is_valid():
            shift = submitted_form.save(commit=False)
            shift.store = store
            shift.user = shift.membership.user
            shift.is_generated = False
            shift.save()
            label = "希望外のシフト" if submitted_mode == "outside" else "希望通りのシフト"
            messages.success(request, f"{label}を追加しました。")
            return redirect(_shift_calendar_url(shift.work_date))

    eligible_membership_ids = set(
        form.fields["membership"].queryset.values_list("pk", flat=True)
    )
    eligibility_index = EligibilityIndex(store, selected_date)
    day_candidates = []
    all_day_membership_ids = []
    for membership in eligibility_index.memberships:
        eligibility = eligibility_index.for_membership(membership)
        if eligibility.available:
            day_candidates.append({
                "membership": membership, "all_day": eligibility.all_day,
                "intervals": [{"start_time": start, "end_time": end} for start, end in eligibility.intervals],
                "submission": eligibility.submission,
            })
            if eligibility.all_day:
                all_day_membership_ids.append(str(membership.pk))
    hours = StoreOperatingHours.objects.filter(store=store).first()
    requirement_times = list(requirements)
    all_day_start = min((requirement.start_time for requirement in requirement_times), default=None)
    all_day_end = max((requirement.end_time for requirement in requirement_times), default=None)
    if all_day_start is None and hours and hours.start_time and hours.end_time and hours.start_time < hours.end_time:
        all_day_start, all_day_end = hours.start_time, hours.end_time
    availability_autofill = {
        "date": selected_date.isoformat(),
        "memberships": {},
        "all_day_membership_ids": all_day_membership_ids,
        "all_day_start_time": all_day_start.strftime("%H:%M") if all_day_start else "00:00",
        "all_day_end_time": all_day_end.strftime("%H:%M") if all_day_end else "23:59",
    }
    for membership_id in all_day_membership_ids:
        availability_autofill["memberships"][membership_id] = []
    for availability in availabilities:
        if availability.membership_id in eligible_membership_ids:
            availability_autofill["memberships"].setdefault(
                str(availability.membership_id), []
            ).append({
                "id": availability.pk,
                "start_time": availability.start_time.strftime("%H:%M"),
                "end_time": availability.end_time.strftime("%H:%M"),
            })

    return render(
        request,
        "manager/shift_list.html",
        {
            **calendar_context,
            "store": store,
            "availabilities": availabilities,
            "day_candidates": day_candidates,
            "requirements": requirements,
            "shifts": shifts,
            "form": form,
            "outside_form": outside_form,
            "help_form": help_form,
            "help_shifts": help_shifts,
            "availability_autofill": availability_autofill,
            "selected_staffing": staffing_statuses.get(selected_date),
        },
    )


def _shift_calendar_url(work_date):
    return (
        f"{reverse('manager_shift_list')}?year={work_date.year}"
        f"&month={work_date.month}&date={work_date.isoformat()}"
    )


def _manager_membership(user):
    return StoreMembership.objects.filter(
        user=user, role="manager", is_active=True
    ).select_related("store").first()


@login_required
@require_POST
@transaction.atomic
def staffing_warning_update(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        raise PermissionDenied

    form = StaffingWarningForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("入力内容が正しくありません。カレンダーから操作してください。")

    store = manager_membership.store
    work_date = form.cleaned_data["work_date"]
    if form.cleaned_data["action"] == "restore":
        StaffingWarningDismissal.objects.filter(store=store, work_date=work_date).delete()
        messages.success(request, "この日の人数不足の警告を再表示しました。")
    else:
        staffing = get_staffing_status(store, work_date)
        if not staffing or not staffing["has_shortage"]:
            messages.info(request, "この日は必要人数を下回っていません。")
        elif staffing["staffing_signature"] != form.cleaned_data["staffing_signature"]:
            messages.warning(request, "シフトまたは必要人数が変更されています。現在の内容を確認してから警告を解除してください。")
        else:
            StaffingWarningDismissal.objects.update_or_create(
                store=store, work_date=work_date,
                defaults={
                    "staffing_signature": staffing["staffing_signature"],
                    "dismissed_by": request.user,
                },
            )
            messages.success(request, "この人数で足りていると確認し、この日の警告を解除しました。")
    return redirect(_shift_calendar_url(work_date))


@login_required
@require_http_methods(["GET", "POST"])
def availability_edit(request, availability_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    availability = get_object_or_404(
        Availability.objects.for_store(store).select_related("user", "membership", "membership__user"),
        pk=availability_id,
    )
    original_work_date = availability.work_date
    original_start_time = availability.start_time
    original_end_time = availability.end_time
    query = request.GET.copy()
    query.setdefault("year", str(availability.work_date.year))
    query.setdefault("month", str(availability.work_date.month))
    query.setdefault("date", availability.work_date.isoformat())
    calendar_context = get_calendar_context(SimpleNamespace(GET=query))
    form = AvailabilityForm(
        request.POST if request.method == "POST" else None,
        instance=availability, user=availability.user, submission_deadline=None,
        store=store, membership=availability.membership,
    )
    if request.method == "POST" and form.is_valid():
        edited_availability = form.save(commit=False)
        with transaction.atomic():
            if edited_availability.membership_id:
                StoreMembership.objects.select_for_update().get(pk=edited_availability.membership_id)
            target_submission = get_month_submission(edited_availability.membership, edited_availability.work_date)
            if target_submission and DayOffRequest.objects.filter(
                submission=target_submission, work_date=edited_availability.work_date,
            ).exists():
                form.add_error(None, "休み希望日に時間指定を保存することはできません。休み希望を変更してください。")
            else:
                edited_availability.submission = get_month_submission(
                    edited_availability.membership, edited_availability.work_date, create=True,
                )
                edited_availability.save(update_fields=["work_date", "start_time", "end_time", "submission"])
        if not form.errors:
            messages.success(request, "従業員のシフト希望を変更しました。")
            work_date = edited_availability.work_date
            return redirect(
                f"{reverse('manager_availability_list')}?year={work_date.year}"
                f"&month={work_date.month}&date={work_date.isoformat()}"
            )

    return render(request, "manager/availability_edit.html", {
        **calendar_context, "store": store, "availability": availability, "form": form,
        "original_work_date": original_work_date,
        "original_start_time": original_start_time,
        "original_end_time": original_end_time,
    })


@login_required
@require_GET
def shift_overview(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    return render(
        request, "manager/shift_overview.html",
        build_shift_overview_context(
            request, manager_membership.store, calendar_url_name="manager_shift_list",
        ),
    )


@login_required
@require_POST
def shift_order_update(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    with transaction.atomic():
        Store.objects.select_for_update().get(pk=store.pk)
        context = build_shift_overview_context(request, store)
        visible_ids = {row["user"].pk for row in context["rows"]}
        try:
            user_ids = [int(value) for value in request.POST.getlist("user_ids")]
        except ValueError:
            user_ids = None
        if (
            user_ids is None or len(user_ids) != len(visible_ids)
            or set(user_ids) != visible_ids
        ):
            messages.error(
                request,
                "表示順を保存できませんでした。画面を再読み込みして、全員の順番を設定してください。",
            )
        else:
            save_overview_order(store, user_ids)
            messages.success(
                request, "表示順を保存しました。従業員の全体シフト表にも反映されます。",
            )

    return redirect(
        f"{reverse('manager_shift_overview')}?year={context['year']}&month={context['month']}"
    )


@login_required
@require_http_methods(["GET", "POST"])
def shift_edit(request, shift_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    shift = get_object_or_404(
        Shift.objects.select_related("user", "membership"), pk=shift_id, store=store
    )
    original_date = shift.work_date
    initial = {}
    if shift.membership_id is None:
        initial["membership"] = StoreMembership.objects.filter(
            user=shift.user, store=store, role__in=["staff", "manager"],
            is_active=True, user__is_active=True,
        ).first()
    form = ShiftForm(
        request.POST if request.method == "POST" else None,
        instance=shift,
        store=store,
        include_managers=True,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        edited_shift = form.save(commit=False)
        edited_shift.store = store
        edited_shift.user = edited_shift.membership.user
        edited_shift.is_generated = False
        edited_shift.save()
        messages.success(request, "シフトを変更しました。")
        return redirect(_shift_calendar_url(edited_shift.work_date))

    return render(
        request,
        "manager/shift_form.html",
        {
            "store": store,
            "shift": shift,
            "form": form,
            "selected_date": original_date,
            "calendar_url": _shift_calendar_url(original_date),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def shift_delete(request, shift_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    shift = get_object_or_404(
        Shift.objects.select_related("user"), pk=shift_id, store=store
    )
    calendar_url = _shift_calendar_url(shift.work_date)
    if request.method == "POST":
        shift.delete()
        messages.success(request, "シフトを削除しました。")
        return redirect(calendar_url)

    return render(
        request,
        "manager/shift_confirm_delete.html",
        {
            "store": store,
            "shift": shift,
            "selected_date": shift.work_date,
            "calendar_url": calendar_url,
        },
    )
