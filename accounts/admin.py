from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Company,
    CompanyMembership,
    Store,
    StoreMembership,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "last_name",
        "first_name",
        "role",
        "rank",
        "desired_shifts_per_week",
        "is_staff",
        "is_superuser",
    )

    search_fields = (
        "username",
        "email",
        "last_name",
        "first_name",
    )

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "シフト管理情報",
            {
                "fields": (
                    "role",
                    "rank",
                    "desired_shifts_per_week",
                )
            },
        ),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "追加情報",
            {
                "fields": (
                    "email",
                    "last_name",
                    "first_name",
                    "role",
                    "rank",
                    "desired_shifts_per_week",
                )
            },
        ),
    )


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "created_at")


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "company", "role", "created_at")


@admin.register(StoreMembership)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "store",
        "role",
        "rank",
        "desired_shifts_per_week",
        "is_active",
    )
    list_filter = (
        "store",
        "role",
        "rank",
        "is_active",
    )
    search_fields = (
        "user__username",
        "user__last_name",
        "user__first_name",
        "store__name",
    )