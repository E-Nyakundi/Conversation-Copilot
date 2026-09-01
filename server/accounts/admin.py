from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AuthToken, TenantQuota, UsageEvent, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # django.contrib.admin still useful as a break-glass fallback even
    # though the real day-to-day tool is the custom admin UI (§7 of the
    # design doc) — kept minimal, not trying to replicate the custom UI
    # here.
    ordering = ("-created_at",)
    list_display = ("email", "is_staff", "is_active", "must_change_password", "created_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Invite info", {"fields": ("must_change_password", "created_by")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
    search_fields = ("email",)


@admin.register(TenantQuota)
class TenantQuotaAdmin(admin.ModelAdmin):
    # Session-cap fix (2026-09-01): max_minutes/minutes_used columns
    # dropped along with the fields themselves.
    list_display = (
        "user", "is_active", "max_sessions", "sessions_used", "updated_at",
    )
    list_filter = ("is_active",)


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ("user", "session", "minutes", "note", "created_at")
    list_filter = ("user",)
    ordering = ("-created_at",)
