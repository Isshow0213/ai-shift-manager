from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from shifts.models import Availability, Requirement, Shift
from accounts.models import StoreMembership
from .forms import StoreMembershipForm, ShiftForm
from shifts.calendar_utils import get_calendar_context

@login_required
def staff_list(request):
    manager_membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store

    if request.method == "POST":
        form = StoreMembershipForm(request.POST, store=store)
        if form.is_valid():
            membership = form.save(commit=False)
            membership.store = store
            membership.save()
            messages.success(request, "従業員を店舗に追加しました。")
            return redirect("manager_staff_list")
    else:
        form = StoreMembershipForm(store=store)

    memberships = StoreMembership.objects.filter(
        store=store,
    ).select_related("user").order_by(
        "role", "user__last_name", "user__first_name", "user__username"
    )

    return render(
        request,
        "manager/staff_list.html",
        {
            "store": store,
            "form": form,
            "memberships": memberships,
        },
    )

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
