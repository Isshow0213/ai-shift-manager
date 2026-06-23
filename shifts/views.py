from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
import calendar
from datetime import date
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.contrib import messages
from accounts.models import StoreMembership
from scheduler.services import generate_shifts_for_store

from .forms import AvailabilityForm, RequirementForm
from .models import Availability, Requirement, Shift


@login_required
def availability_list(request):
    today = timezone.localdate()

    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    selected_date_text = request.GET.get("date")
    selected_date = parse_date(selected_date_text) if selected_date_text else today

    if selected_date is None:
        selected_date = today

    # 表示中の月のカレンダーを作る
    calendar_obj = calendar.Calendar(firstweekday=0)
    month_weeks = calendar_obj.monthdatescalendar(year, month)

    # 前月・翌月
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

    membership = StoreMembership.objects.filter(
        user=request.user,
        is_active=True,
    ).select_related("store").first()

    # 今月の提出済み希望
    monthly_availabilities = Availability.objects.filter(
        user=request.user,
        work_date__year=year,
        work_date__month=month,
    ).order_by("work_date", "start_time")

    # 提出済みの日付だけsetにする
    submitted_dates = set(
        monthly_availabilities.values_list("work_date", flat=True)
    )

    # テンプレートで扱いやすいように、日付ごとの情報に加工する
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
                    "is_submitted": day in submitted_dates,
                    "url": f"?year={year}&month={month}&date={day.isoformat()}",
                }
            )

        calendar_weeks.append(week_days)

    # 選択日の提出済み希望だけ表示
    selected_availabilities = Availability.objects.filter(
        user=request.user,
        work_date=selected_date,
    ).order_by("start_time")

    return render(
        request,
        "shifts/availability_list.html",
        {
            "membership": membership,
            "year": year,
            "month": month,
            "calendar_weeks": calendar_weeks,
            "selected_date": selected_date,
            "selected_availabilities": selected_availabilities,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
        },
    )

@login_required
def availability_create(request):
    membership = StoreMembership.objects.filter(
        user=request.user,
        is_active=True,
    ).select_related("store").first()

    if membership is None:
        messages.error(request, "所属している店舗がありません。")
        return redirect("availability_list")

    selected_date_text = request.GET.get("date")
    selected_date = parse_date(selected_date_text) if selected_date_text else timezone.localdate()

    if selected_date is None:
        selected_date = timezone.localdate()

    if request.method == "POST":
        form = AvailabilityForm(request.POST)
        if form.is_valid():
            availability = form.save(commit=False)
            availability.user = request.user
            availability.membership = membership
            availability.save()
            return redirect(
                f"/availability/?year={availability.work_date.year}&month={availability.work_date.month}&date={availability.work_date}"
            )
    else:
        form = AvailabilityForm(
            initial={
                "work_date": selected_date,
            }
        )

    return render(
        request,
        "shifts/availability_form.html",
        {
            "form": form,
            "membership": membership,
            "selected_date": selected_date,
        },
    )

@login_required
def generate_shift_view(request):
    if request.method != "POST":
        return redirect("dashboard")

    membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("dashboard")

    generate_shifts_for_store(membership.store.id)

    messages.success(request, "シフトを自動生成しました。")
    return redirect("dashboard")

def is_manager(user):
    return user.is_authenticated and (
        user.is_staff or getattr(user, "role", "") == "admin"
    )


@login_required
def dashboard(request):
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
        return redirect("availability_list")

    store = manager_membership.store

    today = timezone.localdate()
    month_start = today.replace(day=1)

    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)

    staff_memberships = StoreMembership.objects.filter(
        store=store,
        role="staff",
        is_active=True,
    ).select_related("user")

    total_staff_count = staff_memberships.count()

    submitted_membership_ids = (
        Availability.objects.filter(
            membership__store=store,
            work_date__gte=month_start,
            work_date__lt=next_month_start,
        )
        .values_list("membership_id", flat=True)
        .distinct()
    )

    submitted_count = staff_memberships.filter(
        id__in=submitted_membership_ids
    ).count()

    unsubmitted_count = total_staff_count - submitted_count

    requirements_count = Requirement.objects.filter(
        store=store,
        work_date__gte=month_start,
        work_date__lt=next_month_start,
    ).count()

    shifts_count = Shift.objects.filter(
        store=store,
        work_date__gte=month_start,
        work_date__lt=next_month_start,
    ).count()

    return render(
        request,
        "manager/dashboard.html",
        {
            "store": store,
            "company": store.company,
            "month_start": month_start,
            "total_staff_count": total_staff_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
            "requirements_count": requirements_count,
            "shifts_count": shifts_count,
        },
    )

@login_required
def shift_list(request):
    shifts = Shift.objects.filter(user=request.user)

    return render(
        request,
        "shifts/manager_shift_list.html",
        {
            "shifts": shifts,
        },
    )

@login_required
def manager_availability_list(request):
    manager_membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("dashboard")

    store = manager_membership.store

    staff_memberships = StoreMembership.objects.filter(
        store=store,
        role="staff",
        is_active=True,
    ).select_related("user")

    availabilities = Availability.objects.filter(
        membership__store=store,
    ).select_related(
        "membership",
        "membership__user",
    ).order_by(
        "work_date",
        "start_time",
        "membership__user__username",
    )

    submitted_membership_ids = availabilities.values_list(
        "membership_id",
        flat=True,
    ).distinct()

    unsubmitted_memberships = staff_memberships.exclude(
        id__in=submitted_membership_ids
    )

    return render(
        request,
        "shifts/manager_availability_list.html",
        {
            "store": store,
            "availabilities": availabilities,
            "unsubmitted_memberships": unsubmitted_memberships,
        },
    )

@login_required
def manager_shift_list(request):
    manager_membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("dashboard")

    store = manager_membership.store

    shifts = Shift.objects.filter(
        store=store,
    ).select_related(
        "membership",
        "membership__user",
        "store",
    ).order_by(
        "work_date",
        "start_time",
        "membership__user__username",
    )

    return render(
        request,
        "shifts/manager_shift_list.html",
        {
            "store": store,
            "shifts": shifts,
        },
    )


@login_required
def manager_requirement_list(request):
    manager_membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("dashboard")

    store = manager_membership.store

    if request.method == "POST":
        form = RequirementForm(request.POST)
        if form.is_valid():
            requirement = form.save(commit=False)
            requirement.store = store
            requirement.save()
            return redirect("manager_requirement_list")
    else:
        form = RequirementForm()

    requirements = Requirement.objects.filter(
        store=store,
    ).order_by(
        "work_date",
        "start_time",
    )

    return render(
        request,
        "shifts/manager_requirement_list.html",
        {
            "store": store,
            "form": form,
            "requirements": requirements,
        },
    )

@login_required
def availability_delete(request, availability_id):
    availability = get_object_or_404(
        Availability,
        id=availability_id,
        user=request.user,
    )

    if request.method == "POST":
        availability.delete()
        return redirect("availability_list")

    return redirect("availability_list")