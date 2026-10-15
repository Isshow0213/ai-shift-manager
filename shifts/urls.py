from django.urls import path
from . import views
from . import submission_views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("availability/", views.availability_list, name="availability_list"),
    path("availability/new/", views.availability_create, name="availability_create"),
    path("availability/submit/", submission_views.monthly_submission_submit, name="monthly_submission_submit"),
    path("settings/shift-submission/", submission_views.submission_settings, name="submission_settings"),
    path("shifts/", views.shift_list, name="shift_list"),
    path("shifts/store/", views.store_shift_overview, name="store_shift_overview"),
    path("generate/", views.generate_shift_view),
    path(
        "availability/<int:availability_id>/delete/",
        views.availability_delete,
        name="availability_delete",
    ),
]
