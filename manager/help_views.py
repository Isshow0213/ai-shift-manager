from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from shifts.models import HelpShift
from .help_forms import HelpShiftForm
from .views import _manager_membership, _shift_calendar_url


@login_required
@require_http_methods(["GET", "POST"])
def help_shift_edit(request, shift_id):
    membership = _manager_membership(request.user)
    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")
    shift = get_object_or_404(HelpShift, pk=shift_id, store=membership.store)
    original_date = shift.work_date
    form = HelpShiftForm(
        request.POST if request.method == "POST" else None,
        store=membership.store, instance=shift,
    )
    if request.method == "POST" and form.is_valid():
        edited_shift = form.save()
        messages.success(request, "ヘルプのシフトを変更しました。")
        return redirect(_shift_calendar_url(edited_shift.work_date))
    return render(request, "manager/help_shift_form.html", {
        "store": membership.store, "help_shift": shift, "form": form,
        "selected_date": original_date, "calendar_url": _shift_calendar_url(original_date),
    })


@login_required
@require_http_methods(["GET", "POST"])
def help_shift_delete(request, shift_id):
    membership = _manager_membership(request.user)
    if membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")
    shift = get_object_or_404(HelpShift, pk=shift_id, store=membership.store)
    calendar_url = _shift_calendar_url(shift.work_date)
    if request.method == "POST":
        shift.delete()
        messages.success(request, "ヘルプのシフトを削除しました。")
        return redirect(calendar_url)
    return render(request, "manager/help_shift_confirm_delete.html", {
        "store": membership.store, "help_shift": shift,
        "selected_date": shift.work_date, "calendar_url": calendar_url,
    })
