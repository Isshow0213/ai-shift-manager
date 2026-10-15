from django.urls import path

from shifts import views as shift_views
from . import views as manager_views
from . import help_views


urlpatterns = [
    path("dashboard/", shift_views.dashboard, name="manager_dashboard"),
    path("availabilities/", shift_views.manager_availability_list, name="manager_availability_list"),
    path("availabilities/<int:availability_id>/edit/", manager_views.availability_edit, name="manager_availability_edit"),
    path("submission-deadline/", shift_views.manager_submission_deadline, name="manager_submission_deadline"),
    path("requirements/", shift_views.manager_requirement_list, name="manager_requirement_list"),
    path("requirements/bulk/", shift_views.manager_requirement_bulk, name="manager_requirement_bulk"),
    path("generate/", shift_views.generate_shift_view, name="generate_shift"),

    path("staff/", manager_views.staff_list, name="manager_staff_list"),
    path("staff/<int:membership_id>/rank/", manager_views.staff_rank_update, name="manager_staff_rank_update"),
    path("staff/<int:membership_id>/desired-shifts/", manager_views.staff_desired_shifts_update, name="manager_staff_desired_shifts_update"),
    path("staff/<int:membership_id>/permissions/", manager_views.staff_permissions_update, name="manager_staff_permissions_update"),
    path("staff/invitations/new/", manager_views.staff_invite_create, name="manager_staff_invite_create"),
    path("staff/invitations/<int:invitation_id>/revoke/", manager_views.staff_invite_revoke, name="manager_staff_invite_revoke"),
    path("shifts/", manager_views.shift_list, name="manager_shift_list"),
    path("shifts/overview/", manager_views.shift_overview, name="manager_shift_overview"),
    path("shifts/overview/order/", manager_views.shift_order_update, name="manager_shift_order_update"),
    path("shifts/<int:shift_id>/edit/", manager_views.shift_edit, name="manager_shift_edit"),
    path("shifts/<int:shift_id>/delete/", manager_views.shift_delete, name="manager_shift_delete"),
    path("help-shifts/<int:shift_id>/edit/", help_views.help_shift_edit, name="manager_help_shift_edit"),
    path("help-shifts/<int:shift_id>/delete/", help_views.help_shift_delete, name="manager_help_shift_delete"),
]
