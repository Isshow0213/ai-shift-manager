from datetime import timedelta
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


class Company(models.Model):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Store(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="stores",
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company.name} / {self.name}"


class User(AbstractUser):
    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("staff", "Staff"),
    ]

    RANK_CHOICES = [
        ("A", "A"),
        ("B", "B"),
        ("C", "C"),
    ]

    # AbstractUser に元々ある first_name / last_name を日本語ラベルに上書き
    last_name = models.CharField(
        "苗字",
        max_length=150,
        blank=True,
    )

    first_name = models.CharField(
        "名前",
        max_length=150,
        blank=True,
    )

    # 一旦残す。あとで StoreMembership 側に移す。
    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default="staff",
    )

    rank = models.CharField(
        max_length=1,
        choices=RANK_CHOICES,
        default="C",
    )

    desired_shifts_per_week = models.PositiveIntegerField(default=0)

    class Meta(AbstractUser.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                Lower("email"), condition=~models.Q(email=""),
                name="accounts_user_email_ci_unique",
            ),
        ]

    @property
    def full_name_japanese(self):
        full_name = f"{self.last_name} {self.first_name}".strip()
        return full_name or self.username

    def __str__(self):
        return self.full_name_japanese


class CompanyMembership(models.Model):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("member", "Member"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="company_memberships",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="member",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "company")

    def __str__(self):
        return f"{self.user.username} / {self.company.name} / {self.role}"


class StoreMembership(models.Model):
    ROLE_CHOICES = [
        ("manager", "Manager"),
        ("staff", "Staff"),
    ]

    RANK_CHOICES = [
        ("A", "A"),
        ("B", "B"),
        ("C", "C"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="store_memberships",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="staff",
    )
    rank = models.CharField(
        max_length=1,
        choices=RANK_CHOICES,
        default="C",
    )
    desired_shifts_per_week = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "store")

    def __str__(self):
        return f"{self.user.username} / {self.store.name} / {self.role}"


class StaffInvitation(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="staff_invitations")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_staff_invitations")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_staff_invitations",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def is_usable(self):
        if self.used_at or self.revoked_at or self.expires_at <= timezone.now():
            return False
        return StoreMembership.objects.filter(
            user_id=self.created_by_id, store_id=self.store_id,
            role="manager", is_active=True, user__is_active=True,
        ).exists()

    def __str__(self):
        return f"{self.store.name} / 従業員招待"
