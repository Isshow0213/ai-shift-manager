from django.urls import path

from shifts import views as shift_views
from . import views as manager_views


urlpatterns = [
    path("dashboard/", shift_views.dashboard, name="manager_dashboard"),
    path("availabilities/", shift_views.manager_availability_list, name="manager_availability_list"),
    path("requirements/", shift_views.manager_requirement_list, name="manager_requirement_list"),
    path("requirements/bulk/", shift_views.manager_requirement_bulk, name="manager_requirement_bulk"),
    path("generate/", shift_views.generate_shift_view, name="generate_shift"),

    path("staff/", manager_views.staff_list, name="manager_staff_list"),
    path("staff/<int:membership_id>/rank/", manager_views.staff_rank_update, name="manager_staff_rank_update"),
    path("staff/invitations/new/", manager_views.staff_invite_create, name="manager_staff_invite_create"),
    path("staff/invitations/<int:invitation_id>/revoke/", manager_views.staff_invite_revoke, name="manager_staff_invite_revoke"),
    path("shifts/", manager_views.shift_list, name="manager_shift_list"),
    path("shifts/overview/", manager_views.shift_overview, name="manager_shift_overview"),
    path("shifts/<int:shift_id>/edit/", manager_views.shift_edit, name="manager_shift_edit"),
    path("shifts/<int:shift_id>/delete/", manager_views.shift_delete, name="manager_shift_delete"),
]
