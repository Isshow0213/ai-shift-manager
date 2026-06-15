from django.contrib import admin
from .models import Availability, Requirement, Shift


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
    )
    list_filter = ("store", "work_date")


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