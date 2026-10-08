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
    path("shifts/", manager_views.shift_list, name="manager_shift_list"),
    path("shifts/<int:shift_id>/edit/", manager_views.shift_edit, name="manager_shift_edit"),
    path("shifts/<int:shift_id>/delete/", manager_views.shift_delete, name="manager_shift_delete"),
]
