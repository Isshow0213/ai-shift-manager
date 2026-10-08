from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.views import MembershipLoginView, index


urlpatterns = [
    path("", index, name="index"),

    path("admin/", admin.site.urls),

    path(
        "login/",
        MembershipLoginView.as_view(),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),

    path("", include("shifts.urls")),

    path("manager/", include("manager.urls")),
]
