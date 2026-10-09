from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from shifts.models import Availability, Requirement, Shift
from accounts.models import StaffInvitation, StoreMembership
from .forms import ShiftForm, StaffRankForm
from shifts.calendar_utils import get_calendar_context

@login_required
@require_GET
def staff_list(request):
    manager_membership = _manager_membership(request.user)

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store

    return _render_staff_list(request, store)


def _render_staff_list(request, store, rank_membership_id=None, rank_form=None):

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

    monthly_availabilities = Availability.objects.filter(
        membership__store=store,
        work_date__year=year,
        work_date__month=month,
    )

    monthly_requirements = Requirement.objects.filter(
        store=store,
        work_date__year=year,
        work_date__month=month,
    )

    monthly_shifts = Shift.objects.filter(
        store=store,
        work_date__year=year,
        work_date__month=month,
    )

    availability_dates = set(
        monthly_availabilities.values_list("work_date", flat=True)
    )

    requirement_dates = set(
        monthly_requirements.values_list("work_date", flat=True)
    )

    shift_dates = set(
        monthly_shifts.values_list("work_date", flat=True)
    )

    for week in calendar_context["calendar_weeks"]:
        for day in week:
            day["has_availability"] = day["date"] in availability_dates
            day["has_requirement"] = day["date"] in requirement_dates
            day["has_shift"] = day["date"] in shift_dates

    availabilities = Availability.objects.filter(
        membership__store=store,
        work_date=selected_date,
    ).select_related(
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

    if request.method == "POST":
        form = ShiftForm(
            request.POST,
            store=store,
            selected_date=selected_date,
        )

        if form.is_valid():
            shift = form.save(commit=False)
            shift.store = store
            shift.user = shift.membership.user
            shift.is_generated = False
            shift.save()
            messages.success(request, "手動でシフトを追加しました。")
            return redirect(_shift_calendar_url(shift.work_date))
    else:
        form = ShiftForm(
            store=store,
            selected_date=selected_date,
            initial={
                "work_date": selected_date,
            },
        )

    eligible_membership_ids = set(
        form.fields["membership"].queryset.values_list("pk", flat=True)
    )
    availability_autofill = {
        "date": selected_date.isoformat(),
        "memberships": {},
    }
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
            "requirements": requirements,
            "shifts": shifts,
            "form": form,
            "availability_autofill": availability_autofill,
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
@require_GET
def shift_overview(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    calendar_context = get_calendar_context(request)
    year, month = calendar_context["year"], calendar_context["month"]
    weekdays = ("月", "火", "水", "木", "金", "土", "日")
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
                    "url": _shift_calendar_url(work_date),
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
    for user in sorted(
        users.values(),
        key=lambda user: (user.last_name, user.first_name, user.username, user.pk),
    ):
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
    return render(request, "manager/shift_overview.html", {
        **calendar_context,
        "store": store,
        "month_days": month_days,
        "rows": rows,
        "days_count": len(month_days),
        "day_staff_counts": [len(staff_by_date[day["date"]]) for day in month_days],
        "total_shifts": len(monthly_shifts), "scheduled_staff_count": len(scheduled_users),
        "total_working_days": sum(row["working_days"] for row in rows),
        "total_hours_display": f"{total_minutes // 60}:{total_minutes % 60:02d}",
    })


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
            user=shift.user, store=store, role="staff", is_active=True, user__is_active=True
        ).first()
    form = ShiftForm(
        request.POST if request.method == "POST" else None,
        instance=shift,
        store=store,
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
