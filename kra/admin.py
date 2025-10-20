from django.contrib import admin
from .models import KRACompanyConfig, KRAInvoiceCounter, KRAInvoiceSubmission

@admin.register(KRACompanyConfig)
class KRACompanyConfigAdmin(admin.ModelAdmin):
    list_display = ['company', 'tin', 'bhf_id', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['company__name', 'tin']

@admin.register(KRAInvoiceCounter)
class KRAInvoiceCounterAdmin(admin.ModelAdmin):
    list_display = ['company', 'last_invoice_number', 'last_used_date']
    search_fields = ['company__name']

@admin.register(KRAInvoiceSubmission)
class KRAInvoiceSubmissionAdmin(admin.ModelAdmin):
    list_display = ['company', 'invoice', 'kra_invoice_number', 'status', 'created_at']
    list_filter = ['status', 'created_at', 'company']
    search_fields = ['invoice__doc_number', 'kra_invoice_number', 'trd_invoice_no']
    readonly_fields = ['created_at', 'updated_at']