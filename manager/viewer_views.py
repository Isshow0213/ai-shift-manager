from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import StoreShiftViewingPermission
from .viewer_forms import ShiftViewerGrantForm
from .views import _manager_membership


def _render_shift_viewers(request, store, form=None):
    if form is None:
        form = ShiftViewerGrantForm(store=store)
    permissions = StoreShiftViewingPermission.objects.filter(
        store=store, company_membership__company_id=store.company_id,
    ).select_related("company_membership__user", "created_by").order_by(
        "-created_at", "-pk",
    )
    bound_membership = getattr(form, "cleaned_data", {}).get("company_membership")
    errors_in_permission_row = False
    for permission in permissions:
        initial = {
            "company_membership": permission.company_membership_id,
            "can_view_shifts": permission.can_view_shifts,
            "can_submit_availability": permission.can_submit_availability,
            "show_in_shift_overview": permission.show_in_shift_overview,
        }
        bound_row = form.is_bound and bound_membership is not None and (
            permission.company_membership_id == bound_membership.pk
        )
        permission.permissions_form = ShiftViewerGrantForm(
            form.data if bound_row else None, store=store, initial=initial,
            company_membership_hidden=True, auto_id=f"permission-{permission.pk}-%s",
        )
        errors_in_permission_row = errors_in_permission_row or bound_row
    if errors_in_permission_row:
        form = ShiftViewerGrantForm(store=store)
    return render(request, "manager/shift_viewers.html", {
        "store": store,
        "company": store.company,
        "form": form,
        "permissions": permissions,
        "has_company_memberships": form.fields["company_membership"].queryset.exists(),
    })


@login_required
@require_GET
def shift_viewers(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")
    return _render_shift_viewers(request, manager_membership.store)


@login_required
@require_POST
def shift_viewer_grant(request):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    form = ShiftViewerGrantForm(request.POST, store=store)
    if form.is_valid():
        company_membership = form.cleaned_data["company_membership"]
        StoreShiftViewingPermission.objects.update_or_create(
            store=store, company_membership=company_membership,
            defaults={
                "is_active": True, "created_by": request.user,
                "can_view_shifts": form.cleaned_data["can_view_shifts"],
                "can_submit_availability": form.cleaned_data["can_submit_availability"],
                "show_in_shift_overview": form.cleaned_data["show_in_shift_overview"],
            },
        )
        messages.success(
            request,
            f"{company_membership.user.full_name_japanese}さんの、この店舗の権限を保存しました。",
        )
        return redirect("manager_shift_viewers")

    return _render_shift_viewers(request, store, form=form)


@login_required
@require_POST
def shift_viewer_revoke(request, permission_id):
    manager_membership = _manager_membership(request.user)
    if manager_membership is None:
        messages.error(request, "管理できる店舗がありません。")
        return redirect("manager_dashboard")

    store = manager_membership.store
    permission = get_object_or_404(
        StoreShiftViewingPermission, pk=permission_id, store=store,
    )
    StoreShiftViewingPermission.objects.filter(pk=permission.pk, store=store).update(
        is_active=False,
    )
    messages.success(request, "この会社メンバーの店舗権限と表への表示を解除しました。")
    return redirect("manager_shift_viewers")
