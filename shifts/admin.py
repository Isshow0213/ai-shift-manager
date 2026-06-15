from django.contrib import admin
from .models import Availability, Requirement, Shift

admin.site.register(Availability)

@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = (
        "work_date",
        "start_time",
        "end_time",
        "required_staff_count",
    )

@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "work_date",
        "start_time",
        "end_time",
        "is_generated",
    )