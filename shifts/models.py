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
    DAY_TYPE_CHOICES = [
        ('weekday', '平日'),
        ('holiday', '祝日'),
        ('sunday', '日曜日'),
        ('monday', '月曜日'),
        ('tuesday', '火曜日'),
        ('wednesday', '水曜日'),
        ('thursday', '木曜日'),
        ('friday', '金曜日'),
        ('saturday', '土曜日'),
    ]

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
    deadline = models.DateTimeField(null=True, blank=True, verbose_name="締切")
    memo = models.TextField("メモ", max_length=500, blank=True, default="")
    day_type = models.CharField(
        "曜日種別",
        max_length=10,
        choices=DAY_TYPE_CHOICES,
        null=True,
        blank=True,
    )
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
        ("red", "赤"),
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

class StoreOperatingHours(models.Model):
    """店舗の営業時間（通し時間）設定"""
    store = models.OneToOneField(
        "accounts.Store",
        on_delete=models.CASCADE,
        related_name="operating_hours",
    )
    start_time = models.TimeField("営業開始時刻", null=True, blank=True)
    end_time = models.TimeField("営業終了時刻", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "店舗営業時間"
        verbose_name_plural = "店舗営業時間"

    def __str__(self):
        return f"{self.store.name} {self.start_time}-{self.end_time}"


class AvailabilityChangeNotification(models.Model):
    """締切後の希望変更通知"""
    requirement = models.ForeignKey(
        Requirement,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    availability = models.ForeignKey(
        Availability,
        on_delete=models.CASCADE,
        related_name="change_notifications",
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Changed availability for {self.availability.user} on {self.availability.work_date}"