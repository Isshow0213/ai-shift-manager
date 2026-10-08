from posixpath import normpath
from urllib.parse import unquote, urljoin, urlsplit

from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse

from .models import StoreMembership
from .auth_forms import EmailOrUsernameAuthenticationForm


def get_login_destination(user):
    if user.is_authenticated and StoreMembership.objects.filter(
        user=user, role="manager", is_active=True
    ).exists():
        return "manager_dashboard"
    return "availability_list"


def index(request):
    if request.user.is_authenticated:
        return redirect(get_login_destination(request.user))
    return redirect("login")


class MembershipLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True
    authentication_form = EmailOrUsernameAuthenticationForm

    def get_default_redirect_url(self):
        return reverse(get_login_destination(self.request.user))

    def get_redirect_url(self):
        redirect_to = super().get_redirect_url()
        if not redirect_to or not self.request.user.is_authenticated:
            return redirect_to

        path = urlsplit(urljoin(self.request.build_absolute_uri(), redirect_to)).path
        path = normpath(unquote(path).replace("\\", "/"))
        if path == normpath(reverse("login")):
            return ""

        manager_prefix = reverse("manager_dashboard").rsplit("/", 2)[0]
        legacy_generate = reverse("availability_list").rsplit("/", 2)[0] + "/generate"
        targets_manager = (
            path == manager_prefix
            or path.startswith(manager_prefix + "/")
            or path == normpath(reverse("dashboard"))
            or path == legacy_generate
        )
        if targets_manager and get_login_destination(self.request.user) != "manager_dashboard":
            return ""
        return redirect_to
