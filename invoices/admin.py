from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    """Inline for invoice line items"""
    model = InvoiceLine
    extra = 0
    readonly_fields = ('id', 'created_at', 'updated_at')
    fields = ('line_num', 'item_name', 'description', 'qty', 'unit_price', 'amount')

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin interface for invoices"""

    list_display = [
        'doc_number', 'customer_name', 'txn_date', 'due_date',
        'total_amt_formatted', 'balance_formatted', 'company_name', 'connection_status'
    ]

    list_filter = [
        'txn_date', 'due_date', 'company', 'created_at'
    ]

    search_fields = [
        'doc_number', 'customer_name', 'qb_invoice_id',
        'private_note', 'customer_memo'
    ]

    readonly_fields = [
        'id', 'qb_invoice_id', 'sync_token', 'created_at',
        'updated_at', 'raw_data_formatted'
    ]

    fieldsets = (
        ('Invoice Details', {
            'fields': ('qb_invoice_id', 'doc_number', 'txn_date', 'due_date')
        }),
        ('Customer Information', {
            'fields': ('customer_ref_value', 'customer_name')
        }),
        ('Financial Information', {
            'fields': ('total_amt', 'balance')
        }),
        ('Notes', {
            'fields': ('private_note', 'customer_memo'),
            'classes': ('collapse',)
        }),
        ('System Information', {
            'fields': ('company', 'sync_token', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
        ('QuickBooks Data', {
            'fields': ('raw_data_formatted',),
            'classes': ('collapse',)
        })
    )

    inlines = [InvoiceLineInline]
    date_hierarchy = 'txn_date'

    def company_name(self, obj):
        """Display company name with link"""
        if obj.company:
            url = reverse('admin:users_company_change', args=[obj.company.id])
            return format_html('<a href="{}">{}</a>', url, obj.company.name)
        return '-'
    company_name.short_description = 'Company'
    company_name.admin_order_field = 'company__name'

    def total_amt_formatted(self, obj):
        """Display formatted total amount with currency"""
        if obj.company and obj.company.currency_code:
            currency = obj.company.currency_code
        else:
            currency = 'USD'

        if obj.total_amt:
            return format_html(
                '<span style="font-weight: bold;">{} {:.2f}</span>',
                currency, float(obj.total_amt)
            )
        return '-'
    total_amt_formatted.short_description = 'Total Amount'
    total_amt_formatted.admin_order_field = 'total_amt'

    def balance_formatted(self, obj):
        """Display formatted balance with currency and color coding"""
        if obj.company and obj.company.currency_code:
            currency = obj.company.currency_code
        else:
            currency = 'USD'

        if obj.balance is not None:
            balance = float(obj.balance)
            if balance > 0:
                color = "red"  # Outstanding balance
                style = f"color: {color}; font-weight: bold;"
            else:
                color = "green"  # Paid
                style = f"color: {color};"

            return format_html(
                '<span style="{}">{} {:.2f}</span>',
                style, currency, balance
            )
        return '-'
    balance_formatted.short_description = 'Balance'
    balance_formatted.admin_order_field = 'balance'

    def connection_status(self, obj):
        """Display QB connection status"""
        if obj.company and obj.company.is_connected:
            return format_html(
                '<span style="color: green;">● Connected</span>'
            )
        return format_html(
            '<span style="color: red;">● Disconnected</span>'
        )
    connection_status.short_description = 'QB Status'

    def raw_data_formatted(self, obj):
        """Display formatted raw QB data"""
        if obj.raw_data:
            import json
            formatted = json.dumps(obj.raw_data, indent=2)
            return format_html(
                '<pre style="background: #f8f8f8; padding: 10px; overflow: auto; max-height: 400px;">{}</pre>',
                formatted
            )
        return 'No data'
    raw_data_formatted.short_description = 'Raw QB Data'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('company')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    actions = ['mark_as_synced']

    def mark_as_synced(self, request, queryset):
        """Admin action to mark invoices as recently synced"""
        count = queryset.count()
        # Update the updated_at field to current time
        from django.utils import timezone
        queryset.update(updated_at=timezone.now())
        self.message_user(request, f"{count} invoices marked as synced.")
    mark_as_synced.short_description = "Mark selected invoices as synced"


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    """Admin interface for invoice line items"""

    list_display = [
        'invoice_doc_number', 'line_num', 'item_name',
        'qty', 'unit_price_formatted', 'amount_formatted', 'invoice_customer'
    ]

    list_filter = [
        'invoice__company', 'invoice__txn_date', 'created_at'
    ]

    search_fields = [
        'item_name', 'description', 'invoice__doc_number',
        'invoice__customer_name'
    ]

    readonly_fields = [
        'id', 'created_at', 'updated_at', 'raw_data_formatted'
    ]

    fieldsets = (
        ('Line Item Details', {
            'fields': ('invoice', 'line_num', 'item_ref_value', 'item_name')
        }),
        ('Description & Amounts', {
            'fields': ('description', 'qty', 'unit_price', 'amount')
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
        ('Raw Data', {
            'fields': ('raw_data_formatted',),
            'classes': ('collapse',)
        })
    )

    def invoice_doc_number(self, obj):
        """Display invoice doc number with link"""
        url = reverse('admin:invoices_invoice_change', args=[obj.invoice.id])
        return format_html(
            '<a href="{}">{}</a>',
            url,
            obj.invoice.doc_number or f'Invoice #{obj.invoice.qb_invoice_id}'
        )
    invoice_doc_number.short_description = 'Invoice'
    invoice_doc_number.admin_order_field = 'invoice__doc_number'

    def invoice_customer(self, obj):
        """Display customer name"""
        return obj.invoice.customer_name or '-'
    invoice_customer.short_description = 'Customer'
    invoice_customer.admin_order_field = 'invoice__customer_name'

    def unit_price_formatted(self, obj):
        """Display formatted unit price with currency"""
        if obj.invoice.company and obj.invoice.company.currency_code:
            currency = obj.invoice.company.currency_code
        else:
            currency = 'USD'

        if obj.unit_price:
            return f"{currency} {float(obj.unit_price):.2f}"
        return '-'
    unit_price_formatted.short_description = 'Unit Price'
    unit_price_formatted.admin_order_field = 'unit_price'

    def amount_formatted(self, obj):
        """Display formatted amount with currency"""
        if obj.invoice.company and obj.invoice.company.currency_code:
            currency = obj.invoice.company.currency_code
        else:
            currency = 'USD'

        if obj.amount:
            return format_html(
                '<span style="font-weight: bold;">{} {:.2f}</span>',
                currency, float(obj.amount)
            )
        return '-'
    amount_formatted.short_description = 'Amount'
    amount_formatted.admin_order_field = 'amount'

    def raw_data_formatted(self, obj):
        """Display formatted raw QB data"""
        if obj.raw_data:
            import json
            formatted = json.dumps(obj.raw_data, indent=2)
            return format_html(
                '<pre style="background: #f8f8f8; padding: 10px; overflow: auto; max-height: 400px;">{}</pre>',
                formatted
            )
        return 'No data'
    raw_data_formatted.short_description = 'Raw QB Data'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('invoice', 'invoice__company')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser