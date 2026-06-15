from django.contrib.auth.models import AbstractUser
from django.db import models


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

    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default="staff"
    )

    rank = models.CharField(
        max_length=1,
        choices=RANK_CHOICES,
        default="C"
    )

    desired_shifts_per_week = models.PositiveIntegerField(
        default=0
    )