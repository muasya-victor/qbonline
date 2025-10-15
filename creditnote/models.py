from django.db import models
from invoices.models import Invoice
from companies.models import Company
from common.models import TimeStampModel

class CreditNote(TimeStampModel):
    """QuickBooks Credit Memo (Credit Note) model"""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='credit_notes')
    qb_credit_id = models.CharField(max_length=50, db_index=True)
    doc_number = models.CharField(max_length=100, blank=True, null=True)
    txn_date = models.DateField()
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # Customer info
    customer_ref_value = models.CharField(max_length=50, blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)

    # Optional reference to related invoice
    related_invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='credit_notes',
        help_text="Invoice this credit note is applied to"
    )

    # Status and metadata
    private_note = models.TextField(blank=True, null=True)
    customer_memo = models.TextField(blank=True, null=True)
    sync_token = models.CharField(max_length=50)
    
    # Template info
    template_id = models.CharField(max_length=100, blank=True, null=True)
    template_name = models.CharField(max_length=255, blank=True, null=True)

    # Raw data
    raw_data = models.JSONField(blank=True, null=True)

    class Meta:
        unique_together = ('company', 'qb_credit_id')
        indexes = [
            models.Index(fields=['company', 'txn_date']),
            models.Index(fields=['qb_credit_id']),
            models.Index(fields=['customer_name']),
        ]
    
    def __str__(self):
        return f"Credit Note {self.doc_number or self.qb_credit_id} - {self.customer_name}"
    

class CreditNoteLine(TimeStampModel):
    """Line items for a credit note"""

    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='line_items')
    line_num = models.IntegerField()

    item_ref_value = models.CharField(max_length=50, blank=True, null=True)
    item_name = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    qty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    raw_data = models.JSONField(blank=True, null=True)

    class Meta:
        unique_together = ('credit_note', 'line_num')
        ordering = ['line_num']
    
    def __str__(self):
        return f"{self.item_name} - {self.amount}"


