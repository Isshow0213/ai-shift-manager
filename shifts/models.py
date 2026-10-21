from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db.models import Q


class AvailabilityQuerySet(models.QuerySet):
    def for_store(self, store):
        # 既存の店舗所属がある希望は、その所属店舗を正として扱う。
        return self.filter(Q(membership__store=store) | Q(membership__isnull=True, store=store))


class Availability(models.Model):
    objects = AvailabilityQuerySet.as_manager()
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
    submission = models.ForeignKey(
        "ShiftSubmission", on_delete=models.CASCADE, related_name="availabilities",
        null=True, blank=True,
    )

    work_date = models.DateField()
    store = models.ForeignKey(
        "accounts.Store", on_delete=models.CASCADE, related_name="availabilities",
        null=True, blank=True,
    )
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_user(self):
        return self.membership.user if self.membership_id else self.user

    def clean(self):
        super().clean()
        if self.submission_id and self.work_date and DayOffRequest.objects.filter(
            submission=self.submission, work_date=self.work_date,
        ).exists():
            raise ValidationError("同じ日に休み希望と出勤時間は設定できません。")

    def __str__(self):
        return f"{self.user.username} {self.work_date}"


class ShiftSubmission(models.Model):
    SUBMISSION_MODE_CHOICES = [("availability", "出勤希望方式"), ("day_off", "休み希望方式")]

    membership = models.ForeignKey(
        "accounts.StoreMembership", on_delete=models.CASCADE, related_name="shift_submissions",
    )
    target_month = models.DateField("対象月")
    submission_mode = models.CharField("提出方式", max_length=12, choices=SUBMISSION_MODE_CHOICES)
    is_submitted = models.BooleanField("提出済み", default=False)
    submitted_at = models.DateTimeField("提出日時", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["target_month", "membership_id"]
        constraints = [models.UniqueConstraint(
            fields=["membership", "target_month"], name="unique_membership_submission_month",
        ), models.CheckConstraint(
            condition=models.Q(target_month__day=1), name="submission_month_first_day",
        )]
        verbose_name = "月ごとのシフト希望提出"
        verbose_name_plural = "月ごとのシフト希望提出"

    def clean(self):
        super().clean()
        if self.target_month and (self.target_month.day != 1 or not 2 <= self.target_month.year <= 9998):
            raise ValidationError({"target_month": "対象月は2年〜9998年の月初日で指定してください。"})


class DayOffRequest(models.Model):
    submission = models.ForeignKey(ShiftSubmission, on_delete=models.CASCADE, related_name="day_off_requests")
    work_date = models.DateField("休み希望日")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_date", "pk"]
        constraints = [models.UniqueConstraint(
            fields=["submission", "work_date"], name="unique_submission_day_off",
        )]
        verbose_name = "休み希望"
        verbose_name_plural = "休み希望"

    def clean(self):
        super().clean()
        if self.submission_id and self.work_date:
            submission = self.submission
            if self.work_date.replace(day=1) != submission.target_month:
                raise ValidationError({"work_date": "提出対象月の日付を選択してください。"})
            if submission.submission_mode != "day_off":
                raise ValidationError("休み希望方式の月にだけ休みを設定できます。")
            if Availability.objects.filter(membership=submission.membership, work_date=self.work_date).exists():
                raise ValidationError("同じ日に休み希望と出勤時間は設定できません。")


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

class RequirementTimePreset(models.Model):
    store = models.ForeignKey(
        "accounts.Store", on_delete=models.CASCADE, related_name="requirement_time_presets",
    )
    start_time = models.TimeField("開始時刻")
    end_time = models.TimeField("終了時刻")
    required_staff_count = models.PositiveIntegerField("必要人数", validators=[MinValueValidator(1)])
    memo = models.TextField("メモ", max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_time", "end_time"]
        constraints = [models.UniqueConstraint(
            fields=["store", "start_time", "end_time"], name="unique_store_requirement_time_preset",
        )]
        verbose_name = "必要人数の保存済み時間帯"
        verbose_name_plural = "必要人数の保存済み時間帯"


class Shift(models.Model):
    DISPLAY_COLOR_CHOICES = [
        ("", "標準"),
        ("blue", "青"),
        ("green", "緑"),
        ("red", "赤"),
        ("orange", "オレンジ"),
        ("yellow", "黄色"),
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

class HelpShift(models.Model):
    """アカウントを作らず、名前を入力して登録するヘルプ勤務。"""

    DISPLAY_COLOR_CHOICES = Shift.DISPLAY_COLOR_CHOICES

    store = models.ForeignKey(
        "accounts.Store", on_delete=models.CASCADE, related_name="help_shifts",
    )
    name = models.CharField("ヘルプの名前", max_length=150)
    work_date = models.DateField("日付")
    start_time = models.TimeField("開始時刻")
    end_time = models.TimeField("終了時刻")
    note = models.TextField("メモ", max_length=500, blank=True, default="")
    display_color = models.CharField(
        "表示色", max_length=8, choices=DISPLAY_COLOR_CHOICES, blank=True, default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_generated = False

    class Meta:
        ordering = ["work_date", "start_time", "pk"]
        verbose_name = "ヘルプのシフト"
        verbose_name_plural = "ヘルプのシフト"
        constraints = [models.CheckConstraint(
            condition=models.Q(end_time__gt=models.F("start_time")),
            name="help_shift_end_after_start",
        )]

    @property
    def display_color_class(self):
        return Shift.display_color_class.fget(self)

    def __str__(self):
        return f"{self.name} {self.work_date} {self.start_time}-{self.end_time}"


class ShiftDisplayOrder(models.Model):
    """店舗の全体シフト表で共有する従業員の表示順。"""

    store = models.ForeignKey(
        "accounts.Store", on_delete=models.CASCADE, related_name="shift_display_orders",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="shift_display_orders",
    )
    position = models.PositiveIntegerField("表示順")

    class Meta:
        ordering = ["position", "user_id"]
        constraints = [models.UniqueConstraint(
            fields=["store", "user"], name="unique_store_shift_display_order",
        )]
        verbose_name = "全体シフトの表示順"
        verbose_name_plural = "全体シフトの表示順"


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


class StoreSubmissionDeadline(models.Model):
    class Mode(models.TextChoices):
        WEEKLY = "weekly", "週ごと"
        MONTHLY = "monthly", "月ごと"

    store = models.OneToOneField(
        "accounts.Store", on_delete=models.CASCADE,
        related_name="submission_deadline", verbose_name="店舗",
    )
    mode = models.CharField("締切の管理方法", max_length=7, choices=Mode.choices, default=Mode.MONTHLY)
    weekly_deadline_weekday = models.PositiveSmallIntegerField(
        "締切曜日", default=2, validators=[MinValueValidator(0), MaxValueValidator(6)],
    )
    monthly_deadline_day = models.PositiveSmallIntegerField(
        "前月の締切日", default=15, validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "シフト希望の提出締切"
        verbose_name_plural = "シフト希望の提出締切"

    def __str__(self):
        return f"{self.store.name} · {self.get_mode_display()}"


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
