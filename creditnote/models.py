from django.db import models
from django.conf import settings
from common.models import TimeStampModel
from companies.models import Company
from invoices.models import Invoice

User = settings.AUTH_USER_MODEL

class CreditNote(TimeStampModel):
    """QuickBooks Credit Note (Credit Memo) model"""
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='credit_notes')
    qb_credit_id = models.CharField(max_length=50, db_index=True)
    doc_number = models.CharField(max_length=100, blank=True, null=True)
    txn_date = models.DateField()
    
    # Customer info
    customer_ref_value = models.CharField(max_length=50, blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Amounts
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_kra_validated = models.BooleanField(default=False)
    
    # Enhanced tax information
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    
    # Related invoice
    related_invoice = models.ForeignKey(
        Invoice, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='credit_notes'
    )
    
    # Status and metadata
    private_note = models.TextField(blank=True, null=True)
    customer_memo = models.TextField(blank=True, null=True)
    sync_token = models.CharField(max_length=50)

    # Template info (QuickBooks CustomTemplateRef)
    template_id = models.CharField(max_length=100, blank=True, null=True, help_text="QuickBooks template ID used for this credit note")
    template_name = models.CharField(max_length=255, blank=True, null=True, help_text="Name of the QuickBooks template")
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('company', 'qb_credit_id')
        indexes = [
            models.Index(fields=['company', 'txn_date']),
            models.Index(fields=['qb_credit_id']),
            models.Index(fields=['customer_name']),
            models.Index(fields=['related_invoice']),
        ]
    
    def __str__(self):
        return f"Credit Note {self.doc_number or self.qb_credit_id} - {self.customer_name}"


class CreditNoteLine(TimeStampModel):
    """Credit Note line items"""
    
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='line_items')
    line_num = models.IntegerField()
    
    # Item details
    item_ref_value = models.CharField(max_length=50, blank=True, null=True)
    item_name = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Quantities and amounts
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # Enhanced tax information
    tax_code_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxCodeRef value")
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('credit_note', 'line_num')
        ordering = ['line_num']
    
    def __str__(self):
        return f"{self.item_name} - {self.amount}"