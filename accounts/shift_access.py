from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Store, StoreMembership


def active_store_memberships(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return StoreMembership.objects.none()
    return StoreMembership.objects.filter(
        user=user, is_active=True, role__in=["manager", "staff"],
    ).select_related("store", "store__company")


def membership_has_permission(membership, capability):
    return bool(membership and membership.is_active and membership.user.is_active and (
        membership.role == "manager"
        or membership.role == "staff" and getattr(membership, capability)
    ))


def _permitted_memberships(user, capability):
    return active_store_memberships(user).filter(
        Q(role="manager") | Q(role="staff", **{capability: True}),
    )


def _stores(memberships):
    return Store.objects.filter(pk__in=memberships.values("store_id")).select_related(
        "company",
    ).order_by("company__name", "name", "pk")


def permitted_shift_stores(user):
    return _stores(_permitted_memberships(user, "can_view_shifts"))


def permitted_submission_stores(user):
    return _stores(_permitted_memberships(user, "can_submit_availability"))


def available_submission_stores(user):
    # 希望の参照は、提出を停止した所属店舗でも利用できる。
    return _stores(active_store_memberships(user))


def select_submission_store(user, store_id=None):
    memberships = active_store_memberships(user)
    if store_id is None:
        membership = _permitted_memberships(user, "can_submit_availability").first() or memberships.first()
        return (membership.store, membership) if membership else (None, None)
    try:
        store_id = int(store_id)
    except (TypeError, ValueError):
        raise Http404("所属する店舗がありません。")
    if not 0 < store_id <= 9223372036854775807:
        raise Http404("所属する店舗がありません。")
    membership = get_object_or_404(memberships, store_id=store_id)
    return membership.store, membership


def can_submit_availability(user, store):
    return bool(store and _permitted_memberships(user, "can_submit_availability").filter(store=store).exists())


def select_view_membership(user, store_id=None):
    if store_id is not None:
        _, membership = select_submission_store(user, store_id)
    else:
        membership = _permitted_memberships(user, "can_view_shifts").first() or active_store_memberships(user).first()
    if membership and not membership_has_permission(membership, "can_view_shifts"):
        raise PermissionDenied("この店舗の全体シフトを閲覧する権限がありません。")
    return membership
