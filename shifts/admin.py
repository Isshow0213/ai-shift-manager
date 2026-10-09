from django.contrib import admin
from .models import (
    Availability,
    Requirement,
    Shift,
    StoreOperatingHours,
    AvailabilityChangeNotification,
)


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "membership",
        "work_date",
        "start_time",
        "end_time",
        "created_at",
    )
    list_filter = ("work_date", "membership__store")
    search_fields = ("user__username", "user__email")


@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = (
        "store",
        "work_date",
        "start_time",
        "end_time",
        "required_staff_count",
        "deadline",
        "day_type",
    )
    list_filter = ("store", "work_date", "day_type")
    fieldsets = (
        ("基本情報", {
            "fields": ("store", "work_date", "day_type")
        }),
        ("時間", {
            "fields": ("start_time", "end_time")
        }),
        ("人数・メモ", {
            "fields": ("required_staff_count", "memo")
        }),
        ("締切", {
            "fields": ("deadline",)
        }),
    )


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "membership",
        "store",
        "work_date",
        "start_time",
        "end_time",
        "is_generated",
    )
    list_filter = (
        "store",
        "work_date",
        "is_generated",
    )


@admin.register(StoreOperatingHours)
class StoreOperatingHoursAdmin(admin.ModelAdmin):
    list_display = ("store", "start_time", "end_time")
    list_filter = ("store",)
    fieldsets = (
        ("店舗", {
            "fields": ("store",)
        }),
        ("営業時間", {
            "fields": ("start_time", "end_time"),
            "description": "通し勤務で使用される営業時間を設定します。"
        }),
    )


@admin.register(AvailabilityChangeNotification)
class AvailabilityChangeNotificationAdmin(admin.ModelAdmin):
    list_display = ("requirement", "availability", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    readonly_fields = ("created_at",)
    fieldsets = (
        ("通知内容", {
            "fields": ("requirement", "availability")
        }),
        ("ステータス", {
            "fields": ("is_read",)
        }),
        ("作成日時", {
            "fields": ("created_at",),
            "classes": ("collapse",)
        }),
    )
