from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
import calendar
from datetime import date, timedelta
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.contrib import messages
from django.views.decorators.http import require_GET
from accounts.models import StoreMembership
from scheduler.services import generate_shifts_for_store

from .forms import AvailabilityForm, RequirementForm, ShiftGenerationForm, StoreOperatingHoursForm, SubmissionDeadlineForm
from .models import Availability, Requirement, Shift, StoreOperatingHours, StoreSubmissionDeadline
from .calendar_utils import get_calendar_context
from .overview import build_shift_overview_context
from .requirement_bulk_forms import BulkRequirementForm, DAY_TYPES, RequirementTimeSlotFormSet
from .requirement_bulk import apply_plan, build_plan
from .submission_deadlines import (
    get_store_submission_deadline, get_submission_period, is_submission_closed, submission_context,
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

    submission_deadline = get_store_submission_deadline(membership.store if membership else None)
    submission_now = timezone.now()

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
            day["submission_closed"] = is_submission_closed(submission_deadline, day["date"], now=submission_now)

    selected_availabilities = Availability.objects.filter(
        user=request.user,
        work_date=selected_date,
    ).select_related("membership__store").order_by("start_time")
    deadline_by_store = {membership.store_id: submission_deadline} if membership else {}
    for availability in selected_availabilities:
        availability_store = (
            availability.membership.store if availability.membership
            else membership.store if membership else None
        )
        if availability_store and availability_store.pk not in deadline_by_store:
            deadline_by_store[availability_store.pk] = get_store_submission_deadline(availability_store)
        availability.submission_closed = is_submission_closed(
            deadline_by_store.get(availability_store.pk) if availability_store else None,
            availability.work_date, now=submission_now,
        )
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
            **submission_context(submission_deadline, selected_date, now=submission_now),
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
    if selected_date is None or not 2 <= selected_date.year <= 9998:
        selected_date = calendar_context["selected_date"]

    if request.method == "POST":
        try:
            posted_date = parse_date(request.POST.get("work_date", ""))
        except (TypeError, ValueError):
            posted_date = None
        if posted_date is not None and 2 <= posted_date.year <= 9998:
            selected_date = posted_date
    submission_deadline = get_store_submission_deadline(membership.store)

    # 前回提出した希望時間を取得（最大5件）
    all_previous_availabilities = Availability.objects.filter(
        user=request.user,
        work_date__lt=selected_date,
    ).order_by("-work_date", "-start_time")[:5]

    # 重複時間を除外したユニークな前回の希望を取得
    previous_availabilities_unique = []
    seen_times = set()
    for av in all_previous_availabilities:
        time_key = (av.start_time, av.end_time)
        if time_key not in seen_times:
            previous_availabilities_unique.append(av)
            seen_times.add(time_key)

    # 店長が設定した必要時間を取得
    requirements_for_date = Requirement.objects.filter(
        store=membership.store,
        work_date=selected_date,
    ).order_by("start_time")

    operating_hours = StoreOperatingHours.objects.filter(store=membership.store).first()
    has_full_day_hours = bool(
        operating_hours and operating_hours.start_time is not None
        and operating_hours.end_time is not None
        and operating_hours.start_time < operating_hours.end_time
    )

    if request.method == "POST":
        form = AvailabilityForm(request.POST, user=request.user, submission_deadline=submission_deadline)
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
            },
            user=request.user,
            submission_deadline=submission_deadline,
        )

    return render(
        request,
        "shifts/availability_form.html",
        {
            **calendar_context,
            "form": form,
            "membership": membership,
            "selected_date": selected_date,
            "previous_availabilities_unique": previous_availabilities_unique,
            "requirements_for_date": requirements_for_date,
            "operating_hours": operating_hours,
            "has_full_day_hours": has_full_day_hours,
            **submission_context(submission_deadline, selected_date),
        },
    )

@login_required
@require_GET
def store_shift_overview(request):
    membership = StoreMembership.objects.filter(
        user=request.user, is_active=True,
    ).select_related("store").first()
    if membership is None:
        messages.error(request, "所属している店舗がありません。")
        return redirect("availability_list")

    return render(request, "shifts/shift_overview.html", {
        **build_shift_overview_context(request, membership.store),
        "membership": membership,
    })


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
def manager_submission_deadline(request):
    membership = StoreMembership.objects.filter(
        user=request.user, role="manager", is_active=True,
    ).select_related("store").first()
    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("availability_list")

    policy = get_store_submission_deadline(membership.store)
    form = SubmissionDeadlineForm(request.POST if request.method == "POST" else None, instance=policy)
    if request.method == "POST" and form.is_valid():
        StoreSubmissionDeadline.objects.update_or_create(
            store=membership.store,
            defaults={field: form.cleaned_data[field] for field in form.Meta.fields},
        )
        messages.success(request, "提出締切を保存しました。次の週・月にも自動で適用されます。")
        return redirect("manager_submission_deadline")

    calendar_context = get_calendar_context(request)
    example_policy = policy or StoreSubmissionDeadline()
    period = get_submission_period(example_policy, calendar_context["selected_date"])
    next_date = period.end_date + timedelta(days=1)
    next_period = get_submission_period(example_policy, next_date) if next_date.year <= 9998 else None
    return render(request, "shifts/submission_deadline_settings.html", {
        **calendar_context, "store": membership.store, "form": form, "policy": policy,
        "configured": policy is not None, "submission_period": period, "next_submission_period": next_period,
    })


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

    operating_hours = StoreOperatingHours.objects.filter(store=store).first()

    if request.method == "POST":
        # フォームのタイプを判定
        form_type = request.POST.get("form_type", "requirement")
        
        if form_type == "operating_hours":
            operating_hours_form = StoreOperatingHoursForm(request.POST, instance=operating_hours)
            form = RequirementForm()
            if operating_hours_form.is_valid():
                StoreOperatingHours.objects.update_or_create(
                    store=store,
                    defaults={
                        "start_time": operating_hours_form.cleaned_data["start_time"],
                        "end_time": operating_hours_form.cleaned_data["end_time"],
                    },
                )
                messages.success(request, "通し勤務の時間帯を保存しました。")
                return redirect("manager_requirement_list")
        else:
            form = RequirementForm(request.POST)
            operating_hours_form = StoreOperatingHoursForm(instance=operating_hours)
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
        operating_hours_form = StoreOperatingHoursForm(instance=operating_hours)

    requirements = Requirement.objects.filter(
        store=store,
    ).order_by(
        "work_date",
        "start_time",
    )

    # 過去30日分の要件設定を取得（復元用）
    thirty_days_ago = date.today() - timedelta(days=30)
    all_past_requirements = Requirement.objects.filter(
        store=store,
        work_date__gte=thirty_days_ago,
        work_date__lt=date.today(),
    ).order_by("-work_date", "start_time")
    
    # Pythonレベルで重複を除外（時刻の組み合わせで）
    seen_times = set()
    past_requirements = []
    for req in all_past_requirements:
        time_key = (req.start_time, req.end_time)
        if time_key not in seen_times:
            past_requirements.append(req)
            seen_times.add(time_key)
            if len(past_requirements) >= 20:
                break

    return render(
        request,
        "shifts/manager_requirement_list.html",
        {
            "store": store,
            "form": form,
            "operating_hours_form": operating_hours_form,
            "operating_hours": operating_hours,
            "requirements": requirements,
            "past_requirements": past_requirements,
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
        Availability.objects.select_related("membership__store"),
        id=availability_id,
        user=request.user,
    )

    return_url = (
        f"{reverse('availability_list')}?year={availability.work_date.year}"
        f"&month={availability.work_date.month}&date={availability.work_date.isoformat()}"
    )
    if request.method == "POST":
        if availability.membership:
            store = availability.membership.store
        else:
            membership = StoreMembership.objects.filter(user=request.user, is_active=True).select_related("store").first()
            store = membership.store if membership else None
        if is_submission_closed(get_store_submission_deadline(store), availability.work_date):
            messages.error(request, "提出締切を過ぎているため、この希望は削除できません。")
        else:
            availability.delete()
            messages.success(request, "シフト希望を削除しました。")

    return redirect(return_url)
