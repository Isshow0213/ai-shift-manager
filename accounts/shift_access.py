from functools import wraps

from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from .models import Store, StoreMembership, StoreShiftViewingPermission


def _active_user(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def _permitted_stores(user, capability):
    if not _active_user(user):
        return Store.objects.none()
    return Store.objects.filter(
        shift_viewing_permissions__company_membership__user=user,
        shift_viewing_permissions__is_active=True,
        shift_viewing_permissions__company_membership__company_id=F("company_id"),
        **{f"shift_viewing_permissions__{capability}": True},
    ).select_related("company").distinct().order_by("company__name", "name", "pk")


def permitted_shift_stores(user):
    return _permitted_stores(user, "can_view_shifts")


def permitted_submission_stores(user):
    return _permitted_stores(user, "can_submit_availability")


def active_store_memberships(user):
    if not _active_user(user):
        return StoreMembership.objects.none()
    return StoreMembership.objects.filter(
        user=user, is_active=True, role__in=["manager", "staff"],
    ).select_related("store", "store__company")


def has_store_membership(user):
    return active_store_memberships(user).exists()


def available_submission_stores(user):
    if not _active_user(user):
        return Store.objects.none()
    return Store.objects.filter(
        Q(pk__in=active_store_memberships(user).values("store_id"))
        | Q(pk__in=permitted_submission_stores(user).values("pk"))
    ).select_related("company").order_by("company__name", "name", "pk")


def select_submission_store(user, store_id=None):
    memberships = active_store_memberships(user)
    if store_id is None:
        membership = memberships.first()
        return (membership.store, membership) if membership else (permitted_submission_stores(user).first(), None)
    try:
        store_id = int(store_id)
    except (TypeError, ValueError):
        raise Http404("希望を提出できる店舗がありません。")
    if not 0 < store_id <= 9223372036854775807:
        raise Http404("希望を提出できる店舗がありません。")
    store = get_object_or_404(available_submission_stores(user), pk=store_id)
    return store, memberships.filter(store=store).first()


def can_submit_availability(user, store):
    return bool(store and (
        active_store_memberships(user).filter(store=store).exists()
        or permitted_submission_stores(user).filter(pk=store.pk).exists()
    ))


def is_shift_viewer_only(user):
    if not _active_user(user):
        return False
    # 最後の許可を取り消した後も個人画面の操作権限を付けない。
    return not has_store_membership(user) and StoreShiftViewingPermission.objects.filter(
        company_membership__user=user,
    ).exists()


def redirect_shift_viewer(view):
    """会社メンバーの希望操作には、店舗ごとの希望提出権限が必要。"""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if is_shift_viewer_only(request.user) and not permitted_submission_stores(request.user).exists():
            return redirect("viewer_shift_overview")
        return view(request, *args, **kwargs)
    return wrapped


def redirect_company_personal_shifts(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if is_shift_viewer_only(request.user):
            if permitted_shift_stores(request.user).exists():
                return redirect("viewer_shift_overview")
            if permitted_submission_stores(request.user).exists():
                return redirect("availability_list")
            return redirect("viewer_shift_overview")
        return view(request, *args, **kwargs)
    return wrapped
