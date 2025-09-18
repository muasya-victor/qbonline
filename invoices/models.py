# invoices/models.py
from django.db import models
from django.conf import settings
from common.models import TimeStampModel
from users.models import Company

User = settings.AUTH_USER_MODEL


class Invoice(TimeStampModel):
    """QuickBooks Invoice model"""
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invoices')
    qb_invoice_id = models.CharField(max_length=50, db_index=True)
    doc_number = models.CharField(max_length=100, blank=True, null=True)
    txn_date = models.DateField()
    due_date = models.DateField(blank=True, null=True)
    
    # Customer info
    customer_ref_value = models.CharField(max_length=50, blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Amounts
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Status and metadata
    private_note = models.TextField(blank=True, null=True)
    customer_memo = models.TextField(blank=True, null=True)
    sync_token = models.CharField(max_length=50)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('company', 'qb_invoice_id')
        indexes = [
            models.Index(fields=['company', 'txn_date']),
            models.Index(fields=['qb_invoice_id']),
            models.Index(fields=['customer_name']),
        ]
    
    def __str__(self):
        return f"Invoice {self.doc_number or self.qb_invoice_id} - {self.customer_name}"


class InvoiceLine(TimeStampModel):
    """Invoice line items"""
    
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    line_num = models.IntegerField()
    
    # Item details
    item_ref_value = models.CharField(max_length=50, blank=True, null=True)
    item_name = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Quantities and amounts
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('invoice', 'line_num')
        ordering = ['line_num']
    
    def __str__(self):
        return f"{self.item_name} - {self.amount}"

