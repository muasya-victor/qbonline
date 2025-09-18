from django.contrib import admin
from .models import Company, CompanyMembership, ActiveCompany
from django.contrib.auth import get_user_model

User = get_user_model()

@admin.register(User)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "first_name", "last_name", "is_staff", "is_active",)
    list_filter = ("is_staff", "is_active",)
    search_fields = ("email", "first_name", "last_name")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "realm_id", "created_by", "created_at")
    list_filter = ( "created_at",)
    search_fields = ("name", "realm_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at",)

    fieldsets = (
        (None, {
            "fields": ("name", "realm_id",  "created_by")
        }),
        ("Connection", {
            "fields": ("access_token", "refresh_token", "token_data"),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "company", "role", "is_default")
    list_filter = ("role", "is_default")
    search_fields = ("user__email", "company__name", "company__realm_id")
    raw_id_fields = ("user", "company")


@admin.register(ActiveCompany)
class ActiveCompanyAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "company", "last_updated")
    list_filter = ("last_updated",)
    search_fields = ("user__email", "company__name", "company__realm_id")
    raw_id_fields = ("user", "company")
    ordering = ("-last_updated",)
