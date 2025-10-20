from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import Company, CompanyMembership, ActiveCompany

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "qb_company_name", "realm_id", "connection_status", "currency_display", "member_count","invoice_template_id", "created_by", "created_at")
    list_filter = ("is_connected_db", "currency_code", "created_at")
    search_fields = ("name", "qb_company_name", "realm_id", "created_by__email")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "connection_status_display", "token_expiry_display")

    def connection_status(self, obj):
        if obj.is_connected:
            return format_html('<span style="color: green;">✓ Connected</span>')
        return format_html('<span style="color: red;">✗ Disconnected</span>')
    connection_status.short_description = "Status"

    def connection_status_display(self, obj):
        status = "Connected" if obj.is_connected else "Disconnected"
        color = "green" if obj.is_connected else "red"
        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status)
    connection_status_display.short_description = "Connection Status"

    def currency_display(self, obj):
        return obj.currency_code or "USD"
    currency_display.short_description = "Currency"

    def member_count(self, obj):
        count = CompanyMembership.objects.filter(company=obj).count()
        if count > 0:
            # FIX: Use companies app URL
            url = reverse('admin:companies_companymembership_changelist') + f'?company__id__exact={obj.id}'
            return format_html('<a href="{}">{} members</a>', url, count)
        return "0 members"
    member_count.short_description = "Members"

    def token_expiry_display(self, obj):
        if obj.token_data and 'expires_in' in obj.token_data:
            return f"Expires in {obj.token_data['expires_in']} seconds"
        return "No expiry data"
    token_expiry_display.short_description = "Token Expiry"

    fieldsets = (
        (None, {
            "fields": ("name", "qb_company_name", "realm_id", "created_by")
        }),
        ("QuickBooks Details", {
            "fields": ("currency_code", "qb_company_id", "connection_status_display", "token_expiry_display"),
        }),
        ("Connection Tokens", {
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