from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import AvailabilityForm
from .models import Availability


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