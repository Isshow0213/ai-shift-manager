from .models import StoreMembership
from .shift_access import permitted_shift_stores, permitted_submission_stores


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


def shift_view_access(request):
    submission_store = permitted_submission_stores(getattr(request, "user", None)).first()
    return {
        "can_view_permitted_shifts": permitted_shift_stores(getattr(request, "user", None)).exists(),
        "can_submit_company_availability": submission_store is not None,
        "company_submission_default_store": submission_store,
    }
