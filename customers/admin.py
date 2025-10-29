# customers/admin.py
from django.contrib import admin
from .models import Customer

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'company_name', 'email', 'phone', 'balance', 'active', 'company']
    list_filter = ['active', 'company', 'taxable']
    search_fields = ['display_name', 'company_name', 'email', 'given_name', 'family_name']
    readonly_fields = ['qb_customer_id', 'sync_token', 'balance', 'balance_with_jobs', 'raw_data']
    fieldsets = (
        ('Basic Info', {
            'fields': ('company', 'qb_customer_id', 'display_name', 'given_name', 'family_name', 'company_name')
        }),
        ('Contact Info', {
            'fields': ('email', 'phone', 'mobile', 'fax', 'website')
        }),
        ('Billing Address', {
            'fields': ('bill_addr_line1', 'bill_addr_line2', 'bill_addr_city', 
                      'bill_addr_state', 'bill_addr_postal_code', 'bill_addr_country')
        }),
        ('Shipping Address', {
            'fields': ('ship_addr_line1', 'ship_addr_line2', 'ship_addr_city',
                      'ship_addr_state', 'ship_addr_postal_code', 'ship_addr_country')
        }),
        ('Financial Info', {
            'fields': ('balance', 'balance_with_jobs', 'active', 'taxable', 'tax_code_ref_value', 'tax_code_ref_name')
        }),
        ('Metadata', {
            'fields': ('sync_token', 'notes', 'raw_data')
        }),
    )