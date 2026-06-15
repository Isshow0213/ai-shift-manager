from django.urls import path
from . import views

urlpatterns = [
    path("availability/", views.availability_list, name="availability_list"),
    path("availability/new/", views.availability_create, name="availability_create"),
]