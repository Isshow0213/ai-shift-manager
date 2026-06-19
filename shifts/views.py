from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib import messages
from accounts.models import StoreMembership
from scheduler.services import generate_shifts_for_store

from .forms import AvailabilityForm, RequirementForm
from .models import Availability, Requirement, Shift


@login_required
def availability_list(request):
    availabilities = Availability.objects.filter(
        user=request.user,
    ).order_by("work_date", "start_time")

    membership = StoreMembership.objects.filter(
        user=request.user,
        is_active=True,
    ).select_related("store").first()

    return render(
        request,
        "shifts/availability_list.html",
        {
            "availabilities": availabilities,
            "membership": membership,
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

    if request.method == "POST":
        form = AvailabilityForm(request.POST)
        if form.is_valid():
            availability = form.save(commit=False)
            availability.user = request.user
            availability.membership = membership
            availability.save()
            return redirect("availability_list")
    else:
        form = AvailabilityForm()

    return render(
        request,
        "shifts/availability_form.html",
        {
            "form": form,
            "membership": membership,
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