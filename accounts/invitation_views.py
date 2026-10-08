from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import EMAIL_EXISTS_ERROR, StaffInvitationRegistrationForm, email_is_in_use
from .models import StaffInvitation, StoreMembership


class InvitationUnavailable(Exception):
    pass


class EmailAlreadyRegistered(Exception):
    pass


@transaction.atomic
def register_invited_employee(invitation_id, form):
    invitation = StaffInvitation.objects.select_for_update().filter(pk=invitation_id).first()
    if invitation is None or not invitation.is_usable:
        raise InvitationUnavailable

    claimed_at = timezone.now()
    claimed = StaffInvitation.objects.filter(
        pk=invitation.pk, used_at__isnull=True, revoked_at__isnull=True,
        expires_at__gt=claimed_at,
    ).update(used_at=claimed_at)
    if claimed != 1:
        raise InvitationUnavailable
    if email_is_in_use(form.cleaned_data["email"]):
        raise EmailAlreadyRegistered

    user = form.save()
    StoreMembership.objects.create(
        user=user, store_id=invitation.store_id, role="staff", rank="C",
        desired_shifts_per_week=0, is_active=True,
    )
    invitation.used_by = user
    invitation.save(update_fields=["used_by"])
    return user


def unavailable_invitation(request, status=410):
    return render(request, "accounts/invitation_invalid.html", status=status)


@require_http_methods(["GET", "POST"])
def staff_invitation_accept(request, token):
    invitation = StaffInvitation.objects.select_related("store").filter(token=token).first()
    if invitation is None:
        return unavailable_invitation(request, status=404)
    if not invitation.is_usable:
        return unavailable_invitation(request)

    if request.user.is_authenticated:
        return render(request, "accounts/invitation_register.html", {
            "invitation": invitation, "already_authenticated": True,
        }, status=409 if request.method == "POST" else 200)

    form = StaffInvitationRegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            user = register_invited_employee(invitation.pk, form)
        except InvitationUnavailable:
            return unavailable_invitation(request)
        except EmailAlreadyRegistered:
            form.add_error("email", EMAIL_EXISTS_ERROR)
        except IntegrityError:
            if email_is_in_use(form.cleaned_data["email"]):
                form.add_error("email", EMAIL_EXISTS_ERROR)
            else:
                form.add_error(None, "登録できませんでした。もう一度お試しください。")
        else:
            login(request, user, backend="accounts.backends.EmailOrUsernameBackend")
            messages.success(request, "従業員アカウントを登録しました。シフト希望を提出できます。")
            return redirect("availability_list")

    return render(request, "accounts/invitation_register.html", {
        "invitation": invitation, "form": form, "already_authenticated": False,
    })
