from .models import StoreMembership


def app_access(request):
    user = getattr(request, "user", None)
    can_manage_store = bool(
        user is not None
        and user.is_authenticated
        and StoreMembership.objects.filter(
            user=user, role="manager", is_active=True
        ).exists()
    )
    return {"can_manage_store": can_manage_store}
