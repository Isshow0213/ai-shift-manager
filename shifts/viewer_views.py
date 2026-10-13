from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from accounts.shift_access import (
    has_store_membership, permitted_shift_stores, available_submission_stores, can_submit_availability,
)
from .overview import build_shift_overview_context


@login_required
@require_GET
def viewer_shift_overview(request):
    stores = permitted_shift_stores(request.user)
    store_id = request.GET.get("store")
    if store_id is not None:
        try:
            store_id = int(store_id)
        except (TypeError, ValueError):
            raise Http404("閲覧できる店舗がありません。")
        if not 0 < store_id <= 9223372036854775807:
            raise Http404("閲覧できる店舗がありません。")
        store = get_object_or_404(stores, pk=store_id)
    else:
        store = stores.first()
        if store is None:
            raise PermissionDenied("全体シフトの閲覧許可がありません。")

    return render(request, "shifts/shift_overview.html", {
        **build_shift_overview_context(request, store),
        "accessible_stores": stores, "viewer_mode": True,
        "has_store_membership": has_store_membership(request.user),
        "can_submit_selected_store": can_submit_availability(request.user, store),
        "available_submission_stores": available_submission_stores(request.user),
    })
