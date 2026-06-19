from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.models import StoreMembership
from .forms import StoreMembershipForm

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