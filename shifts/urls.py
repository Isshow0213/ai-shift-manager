from django.urls import path
from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("availability/", views.availability_list, name="availability_list"),
    path("availability/new/", views.availability_create, name="availability_create"),
    path("shifts/", views.shift_list, name="shift_list"),
    path("generate/", views.generate_shift_view),
    path(
        "availability/<int:availability_id>/delete/",
        views.availability_delete,
        name="availability_delete",
    ),
]
