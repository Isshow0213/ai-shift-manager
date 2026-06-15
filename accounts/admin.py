from django.contrib import admin
from .models import (
    Company,
    CompanyMembership,
    Store,
    StoreMembership,
    User,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "email",
        "role",
        "rank",
        "desired_shifts_per_week",
        "is_staff",
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