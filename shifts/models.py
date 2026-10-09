from django.db import models
from django.conf import settings


class Availability(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )

    membership = models.ForeignKey(
        "accounts.StoreMembership",
        on_delete=models.CASCADE,
        related_name="availabilities",
        null=True,
        blank=True,
    )

    work_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return f"{self.user.username} {self.work_date}"


class Requirement(models.Model):
    store = models.ForeignKey(
        "accounts.Store",
        on_delete=models.CASCADE,
        related_name="requirements",
        null=True,
        blank=True,
    )

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
    DISPLAY_COLOR_CHOICES = [
        ("", "標準"),
        ("blue", "青"),
        ("green", "緑"),
        ("orange", "オレンジ"),
        ("purple", "紫"),
        ("pink", "ピンク"),
        ("gray", "グレー"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts"
    )
    membership = models.ForeignKey(
        "accounts.StoreMembership",
        on_delete=models.CASCADE,
        related_name="shifts",
        null=True,
        blank=True,
    )

    store = models.ForeignKey(
        "accounts.Store",
        on_delete=models.CASCADE,
        related_name="shifts",
        null=True,
        blank=True,
    )

    work_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_generated = models.BooleanField(default=True)
    note = models.TextField("メモ", max_length=500, blank=True, default="")
    display_color = models.CharField(
        "表示色", max_length=8, choices=DISPLAY_COLOR_CHOICES, blank=True, default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_date", "start_time"]

    @property
    def display_color_class(self):
        color = self.display_color
        if not color or color not in dict(self.DISPLAY_COLOR_CHOICES):
            color = "purple" if self.is_generated else "blue"
        return f"shift-color-{color}"

    def __str__(self):
        return (
            f"{self.user.username} "
            f"{self.work_date} "
            f"{self.start_time}-{self.end_time}"
        )
