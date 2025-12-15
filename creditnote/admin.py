from django.contrib import admin
from .models import CreditNote, CreditNoteLine


class CreditNoteLineInline(admin.TabularInline):
    """
    Inline display for Credit Note line items
    """
    model = CreditNoteLine
    extra = 0
    readonly_fields = (
        'line_num',
        'item_ref_value',
        'item_name',
        'qty',
        'unit_price',
        'amount',
        'tax_percent',
        'tax_amount',
    )
    ordering = ('line_num',)


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    """
    Admin configuration for Credit Notes
    """
    list_display = (
        'doc_number',
        'qb_credit_id',
        'company',
        'customer_name',
        'txn_date',
        'total_amt',
        'balance',
        'status',
        'is_kra_validated',
        'created_at',
    )

    list_filter = (
        'company',
        'txn_date',
        'is_kra_validated',
    )

    search_fields = (
        'doc_number',
        'qb_credit_id',
        'customer_name',
        'customer_ref_value',
    )

    readonly_fields = (
        'qb_credit_id',
        'sync_token',
        'created_at',
        'updated_at',
    )

    date_hierarchy = 'txn_date'
    ordering = ('-txn_date',)

    inlines = [CreditNoteLineInline]

    fieldsets = (
        (
            'Core Information',
            {
                'fields': (
                    'company',
                    'qb_credit_id',
                    'doc_number',
                    'txn_date',
                    'related_invoice',
                )
            },
        ),
        (
            'Customer Details',
            {
                'fields': (
                    'customer_ref_value',
                    'customer_name',
                )
            },
        ),
        (
            'Amounts',
            {
                'fields': (
                    'subtotal',
                    'tax_total',
                    'tax_percent',
                    'total_amt',
                    'balance',
                )
            },
        ),
        (
            'Tax & Compliance',
            {
                'fields': (
                    'is_kra_validated',
                    'tax_rate_ref',
                )
            },
        ),
        (
            'Notes',
            {
                'fields': (
                    'private_note',
                    'customer_memo',
                )
            },
        ),
        (
            'Templates',
            {
                'fields': (
                    'template_id',
                    'template_name',
                )
            },
        ),
        (
            'System Metadata',
            {
                'fields': (
                    'sync_token',
                    'raw_data',
                    'created_at',
                    'updated_at',
                )
            },
        ),
    )


@admin.register(CreditNoteLine)
class CreditNoteLineAdmin(admin.ModelAdmin):
    """
    Admin configuration for Credit Note Lines
    """
    list_display = (
        'credit_note',
        'line_num',
        'item_name',
        'qty',
        'unit_price',
        'amount',
        'tax_amount',
    )

    list_filter = (
        'credit_note__company',
    )

    search_fields = (
        'item_name',
        'description',
        'credit_note__doc_number',
        'credit_note__qb_credit_id',
    )

    ordering = ('credit_note', 'line_num')
