from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils.html import format_html
from django.urls import reverse
from companies.models import CompanyMembership, ActiveCompany

User = get_user_model()

@admin.register(User)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "full_name", "company_count", "active_company_name", "is_staff", "is_active")
    list_filter = ("is_staff", "is_active", "user_role")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-id",)
    readonly_fields = ("last_login",)

    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or "No name"
    full_name.short_description = "Full Name"

    def company_count(self, obj):
        count = CompanyMembership.objects.filter(user=obj).count()
        if count > 0:
            # FIXED: Use companies app URL
            url = reverse('admin:companies_companymembership_changelist') + f'?user__id__exact={obj.id}'
            return format_html('<a href="{}">{} companies</a>', url, count)
        return "0 companies"
    company_count.short_description = "Companies"

    def active_company_name(self, obj):
        try:
            active = ActiveCompany.objects.get(user=obj)
            # FIXED: Use companies app URL
            url = reverse('admin:companies_company_change', args=[active.company.id])
            return format_html('<a href="{}">{}</a>', url, active.company.name)
        except ActiveCompany.DoesNotExist:
            return "None"
    active_company_name.short_description = "Active Company"