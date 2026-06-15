from django.db import models
from django.conf import settings


class Availability(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )

    work_date = models.DateField()

    start_time = models.TimeField()
    end_time = models.TimeField()

    created_at = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return f"{self.user.username} {self.work_date}"


class Requirement(models.Model):
    work_date = models.DateField()

    start_time = models.TimeField()
    end_time = models.TimeField()

    required_staff_count = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_date", "start_time"]


    def __str__(self):
        return (
            f"{self.work_date} "
            f"{self.start_time}-{self.end_time} "
            f"{self.required_staff_count}人"
        )

class Shift(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts"
    )

    work_date = models.DateField()

    start_time = models.TimeField()
    end_time = models.TimeField()

    is_generated = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_date", "start_time"]

    def __str__(self):
        return (
            f"{self.user.username} "
            f"{self.work_date} "
            f"{self.start_time}-{self.end_time}"
        )