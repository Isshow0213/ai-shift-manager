from .models import StoreMembership
from .shift_access import permitted_shift_stores


def app_access(request):
    user = getattr(request, "user", None)
    can_manage_store = bool(
        user is not None
        and user.is_authenticated
        and StoreMembership.objects.filter(user=user, role="manager", is_active=True).exists()
    )
    return {"can_manage_store": can_manage_store}


def shift_view_access(request):
    view_store = permitted_shift_stores(getattr(request, "user", None)).first()
    return {"can_view_store_shifts": view_store is not None, "default_view_store": view_store}
