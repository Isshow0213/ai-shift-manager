from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
import calendar
from django.utils import timezone
from django.utils.dateparse import parse_date
from shifts.models import Availability, Requirement, Shift
from accounts.models import StoreMembership
from .forms import StoreMembershipForm, ShiftForm

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
        form = StoreMembershipForm(request.POST)
        if form.is_valid():
            membership = form.save(commit=False)
            membership.store = store
            membership.save()
            messages.success(request, "従業員を店舗に追加しました。")
            return redirect("manager_staff_list")
    else:
        form = StoreMembershipForm()

    memberships = StoreMembership.objects.filter(
        store=store,
        is_active=True,
    ).select_related("user").order_by("role", "user__username")

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
    today = timezone.localdate()

    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    selected_date_text = request.GET.get("date")
    selected_date = parse_date(selected_date_text) if selected_date_text else today

    if selected_date is None:
        selected_date = today

    calendar_obj = calendar.Calendar(firstweekday=0)
    month_weeks = calendar_obj.monthdatescalendar(year, month)

    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1

    if month == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month + 1

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

    calendar_weeks = []

    for week in month_weeks:
        week_days = []

        for day in week:
            week_days.append(
                {
                    "date": day,
                    "day": day.day,
                    "is_current_month": day.month == month,
                    "is_today": day == today,
                    "is_selected": day == selected_date,
                    "has_availability": day in availability_dates,
                    "has_requirement": day in requirement_dates,
                    "has_shift": day in shift_dates,
                    "url": f"?year={year}&month={month}&date={day.isoformat()}",
                }
            )

        calendar_weeks.append(week_days)

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
            return redirect(
                f"{request.path}?year={shift.work_date.year}&month={shift.work_date.month}&date={shift.work_date}"
            )
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
            "store": store,
            "year": year,
            "month": month,
            "calendar_weeks": calendar_weeks,
            "selected_date": selected_date,
            "availabilities": availabilities,
            "requirements": requirements,
            "shifts": shifts,
            "form": form,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
        },
    )