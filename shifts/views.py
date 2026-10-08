from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
import calendar
from datetime import date
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.contrib import messages
from accounts.models import StoreMembership
from scheduler.services import generate_shifts_for_store

from .forms import AvailabilityForm, RequirementForm, ShiftGenerationForm
from .models import Availability, Requirement, Shift
from .calendar_utils import get_calendar_context
from .requirement_bulk_forms import BulkRequirementForm, DAY_TYPES, RequirementTimeSlotFormSet
from .requirement_bulk import (
    RequirementOverlapError, apply_plan, build_plan, validate_existing_requirements,
)


@login_required
def availability_list(request):
    calendar_context = get_calendar_context(request)
    year = calendar_context["year"]
    month = calendar_context["month"]
    selected_date = calendar_context["selected_date"]

    membership = StoreMembership.objects.filter(
        user=request.user,
        is_active=True,
    ).select_related("store").first()

    monthly_availabilities = Availability.objects.filter(
        user=request.user,
        work_date__year=year,
        work_date__month=month,
    ).order_by("work_date", "start_time")

    submitted_dates = set(
        monthly_availabilities.values_list("work_date", flat=True)
    )

    monthly_shifts = Shift.objects.filter(
        user=request.user, work_date__year=year, work_date__month=month
    )
    shift_dates = set(monthly_shifts.values_list("work_date", flat=True))
    for week in calendar_context["calendar_weeks"]:
        for day in week:
            day["is_submitted"] = day["date"] in submitted_dates
            day["has_shift"] = day["date"] in shift_dates

    selected_availabilities = Availability.objects.filter(
        user=request.user,
        work_date=selected_date,
    ).order_by("start_time")
    selected_shifts = Shift.objects.filter(
        user=request.user, work_date=selected_date
    ).select_related("store").order_by("start_time", "id")

    return render(
        request,
        "shifts/availability_list.html",
        {
            **calendar_context,
            "membership": membership,
            "selected_availabilities": selected_availabilities,
            "selected_shifts": selected_shifts,
            "submitted_days_count": len(submitted_dates),
            "monthly_shifts_count": monthly_shifts.count(),
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

    calendar_context = get_calendar_context(request)
    selected_date_text = request.GET.get("date")
    try:
        selected_date = parse_date(selected_date_text) if selected_date_text else None
    except (TypeError, ValueError):
        selected_date = None
    if selected_date is None:
        selected_date = calendar_context["selected_date"]

    if request.method == "POST":
        form = AvailabilityForm(request.POST)
        if form.is_valid():
            availability = form.save(commit=False)
            availability.user = request.user
            availability.membership = membership
            availability.save()
            messages.success(request, "シフト希望を提出しました。")
            return redirect(
                f"{reverse('availability_list')}?year={availability.work_date.year}"
                f"&month={availability.work_date.month}&date={availability.work_date.isoformat()}"
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
            **calendar_context,
            "form": form,
            "membership": membership,
            "selected_date": selected_date,
        },
    )

@login_required
def generate_shift_view(request):
    if request.method != "POST":
        return redirect("manager_shift_list")

    membership = StoreMembership.objects.filter(
        user=request.user,
        role="manager",
        is_active=True,
    ).select_related("store").first()

    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    form = ShiftGenerationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "自動生成する日付を選んでください。")
        return redirect("manager_shift_list")

    work_date = form.cleaned_data["work_date"]
    result = generate_shifts_for_store(membership.store.id, work_date)

    if result["requirement_count"] == 0:
        messages.warning(request, "この日の必要人数が設定されていません。")
    elif result["shortfall_count"]:
        messages.warning(
            request,
            f"シフトを{result['created_count']}件作成しました。"
            f"必要人数に対して合計{result['shortfall_count']}人分不足しています。",
        )
    else:
        messages.success(
            request, f"シフトを{result['created_count']}件自動生成しました。"
        )

    return redirect(
        f"{reverse('manager_shift_list')}?year={work_date.year}"
        f"&month={work_date.month}&date={work_date.isoformat()}"
    )

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
    calendar_context = get_calendar_context(request)
    membership = StoreMembership.objects.filter(
        user=request.user, is_active=True
    ).select_related("store").first()
    shifts = Shift.objects.filter(
        user=request.user,
        work_date__year=calendar_context["year"],
        work_date__month=calendar_context["month"],
    ).select_related("store").order_by("work_date", "start_time", "id")
    shift_dates = set(shifts.values_list("work_date", flat=True))
    for week in calendar_context["calendar_weeks"]:
        for day in week:
            day["has_shift"] = day["date"] in shift_dates

    return render(
        request,
        "shifts/shift_list.html",
        {
            **calendar_context,
            "membership": membership,
            "shifts": shifts,
            "shift_days_count": len(shift_dates),
            "shifts_count": shifts.count(),
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
            messages.success(request, "必要人数を保存しました。")
            return redirect("manager_requirement_list")
    else:
        selected_date_text = request.GET.get("date")
        try:
            selected_date = parse_date(selected_date_text) if selected_date_text else None
        except ValueError:
            selected_date = None
        form = RequirementForm(initial={"work_date": selected_date})

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
def manager_requirement_bulk(request):
    membership = StoreMembership.objects.filter(
        user=request.user, role="manager", is_active=True,
    ).select_related("store").first()
    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("availability_list")

    store = membership.store
    calendar_context = get_calendar_context(request)
    month_start = date(calendar_context["year"], calendar_context["month"], 1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    data = request.POST if request.method == "POST" else None
    form = BulkRequirementForm(data, initial={"start_date": month_start, "end_date": month_end})
    selected_post_categories = data.getlist("categories") if data is not None else []
    groups = [
        {
            "key": key, "label": label,
            "formset": RequirementTimeSlotFormSet(
                data if key in selected_post_categories else None, prefix=key,
            ),
        }
        for key, label in DAY_TYPES
    ]
    plan = None
    dates_count = slots_count = existing_count = 0

    if request.method == "POST" and form.is_valid():
        selected_categories = form.cleaned_data["categories"]
        slots_by_category = {}
        valid = True
        for group in groups:
            if group["key"] in selected_categories:
                if group["formset"].is_valid():
                    slots_by_category[group["key"]] = [
                        row.cleaned_data for row in group["formset"] if row.cleaned_data
                    ]
                else:
                    valid = False
        if valid:
            candidate_plan = build_plan(form.cleaned_data, slots_by_category)
            try:
                validate_existing_requirements(store, candidate_plan, form.cleaned_data["mode"])
                dates_count = sum(len(group["dates"]) for group in candidate_plan)
                slots_count = sum(len(group["dates"]) * len(group["slots"]) for group in candidate_plan)
                if not dates_count:
                    form.add_error(None, "指定した期間に、選択した区分の日付がありません。")
                elif request.POST.get("action") == "apply":
                    dates_count, slots_count = apply_plan(store, candidate_plan, form.cleaned_data["mode"])
                    messages.success(request, f"{dates_count}日分・{slots_count}件の必要人数を一括保存しました。")
                    return redirect("manager_requirement_list")
                elif request.POST.get("action", "preview") == "preview":
                    plan = candidate_plan
                    dates = [day for group in plan for day in group["dates"]]
                    existing_count = Requirement.objects.filter(store=store, work_date__in=dates).count()
                else:
                    form.add_error(None, "確認または保存ボタンから操作してください。")
            except RequirementOverlapError as error:
                form.add_error(None, str(error))

    selected_categories = form["categories"].value() or []
    for group in groups:
        group["selected"] = group["key"] in selected_categories

    return render(request, "shifts/manager_requirement_bulk.html", {
        "store": store, "form": form, "groups": groups, "plan": plan,
        "dates_count": dates_count, "slots_count": slots_count, "existing_count": existing_count,
    })


@login_required
def availability_delete(request, availability_id):
    availability = get_object_or_404(
        Availability,
        id=availability_id,
        user=request.user,
    )

    return_url = (
        f"{reverse('availability_list')}?year={availability.work_date.year}"
        f"&month={availability.work_date.month}&date={availability.work_date.isoformat()}"
    )
    if request.method == "POST":
        availability.delete()
        messages.success(request, "シフト希望を削除しました。")

    return redirect(return_url)
