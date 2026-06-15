from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import AvailabilityForm
from .models import Availability, Requirement, Shift


@login_required
def availability_list(request):
    availabilities = Availability.objects.filter(user=request.user)

    return render(
        request,
        "shifts/availability_list.html",
        {"availabilities": availabilities},
    )


@login_required
def availability_create(request):
    if request.method == "POST":
        form = AvailabilityForm(request.POST)
        if form.is_valid():
            availability = form.save(commit=False)
            availability.user = request.user
            availability.save()
            return redirect("availability_list")
    else:
        form = AvailabilityForm()

    return render(
        request,
        "shifts/availability_form.html",
        {"form": form},
    )

def is_manager(user):
    return user.is_authenticated and (
        user.is_staff or getattr(user, "role", "") == "admin"
    )


@user_passes_test(is_manager, login_url="login")
def dashboard(request):
    User = get_user_model()

    today = timezone.localdate()
    month_start = today.replace(day=1)

    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)

    staff_users = User.objects.filter(role="staff")
    total_staff_count = staff_users.count()

    submitted_staff_ids = (
        Availability.objects.filter(
            work_date__gte=month_start,
            work_date__lt=next_month_start,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )

    submitted_count = staff_users.filter(id__in=submitted_staff_ids).count()
    unsubmitted_count = total_staff_count - submitted_count

    requirements_count = Requirement.objects.filter(
        work_date__gte=month_start,
        work_date__lt=next_month_start,
    ).count()

    shifts_count = Shift.objects.filter(
        work_date__gte=month_start,
        work_date__lt=next_month_start,
    ).count()

    return render(
        request,
        "shifts/dashboard.html",
        {
            "total_staff_count": total_staff_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
            "requirements_count": requirements_count,
            "shifts_count": shifts_count,
            "month_start": month_start,
        },
    )


@login_required
def shift_list(request):
    shifts = Shift.objects.filter(user=request.user)

    return render(
        request,
        "shifts/shift_list.html",
        {
            "shifts": shifts,
        },
    )