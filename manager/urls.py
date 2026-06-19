from django.urls import path

from shifts import views as shift_views
from . import views as manager_views


urlpatterns = [
    path("dashboard/", shift_views.dashboard, name="manager_dashboard"),
    path("availabilities/", shift_views.manager_availability_list, name="manager_availability_list"),
    path("requirements/", shift_views.manager_requirement_list, name="manager_requirement_list"),
    path("shifts/", shift_views.manager_shift_list, name="manager_shift_list"),
    path("generate/", shift_views.generate_shift_view, name="generate_shift"),

    path("staff/", manager_views.staff_list, name="manager_staff_list"),
]