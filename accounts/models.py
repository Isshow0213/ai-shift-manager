from django.contrib.auth.models import AbstractUser
from django.db import models


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