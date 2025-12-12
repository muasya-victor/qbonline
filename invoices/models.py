from django.db import models
from django.conf import settings
from common.models import TimeStampModel
from companies.models import Company
from customers.models import Customer
from django.db.models import Sum
from decimal import Decimal


User = settings.AUTH_USER_MODEL

class Invoice(TimeStampModel):
    """QuickBooks Invoice model"""
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invoices')
    qb_invoice_id = models.CharField(max_length=50, db_index=True)
    
    # Customer relationship
    customer = models.ForeignKey(
        Customer, 
        on_delete=models.CASCADE, 
        related_name='invoices',
        null=True,
        blank=True
    )
    
    # Backup customer info
    customer_ref_value = models.CharField(max_length=50, blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    
    doc_number = models.CharField(max_length=100, blank=True, null=True)
    txn_date = models.DateField()
    due_date = models.DateField(blank=True, null=True)
    
    # Amounts
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_kra_validated = models.BooleanField(default=False)
    
    # Enhanced tax information
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    
    # Status and metadata
    private_note = models.TextField(blank=True, null=True)
    customer_memo = models.TextField(blank=True, null=True)
    sync_token = models.CharField(max_length=50)

    # Template info
    template_id = models.CharField(max_length=100, blank=True, null=True, help_text="QuickBooks template ID used for this invoice")
    template_name = models.CharField(max_length=255, blank=True, null=True, help_text="Name of the QuickBooks template")
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('company', 'qb_invoice_id')
        indexes = [
            models.Index(fields=['company', 'txn_date']),
            models.Index(fields=['qb_invoice_id']),
            models.Index(fields=['customer']),
            models.Index(fields=['customer_name']),
        ]
    
    @property
    def total_credits_applied(self):
        """
        Calculate total amount of all credit notes linked to this invoice.
        Safe to add - doesn't affect existing code.
        """
        # Use aggregation to sum all credit notes
        result = self.credit_notes.aggregate(
            total=Sum('total_amt')
        )
        return result['total'] or Decimal('0.00')
    
    @property
    def available_credit_balance(self):
        """
        Calculate available balance for new credit notes.
        Returns the amount that can still be credited.
        """
        total_credits = self.total_credits_applied
        available = self.total_amt - total_credits
        
        # Ensure we don't return negative values
        return max(Decimal('0.00'), available)
    
    @property
    def is_fully_credited(self):
        """
        Check if invoice is fully credited (with tolerance for rounding errors).
        Uses 1 cent tolerance to handle decimal precision issues.
        """
        return self.available_credit_balance <= Decimal('0.01')
    
    @property
    def credit_utilization_percentage(self):
        """
        Get percentage of invoice that has been credited.
        Useful for reporting.
        """
        if self.total_amt == Decimal('0.00'):
            return Decimal('0.00')
        
        return (self.total_credits_applied / self.total_amt) * Decimal('100')
    
    def can_accept_credit_note(self, credit_amount: Decimal) -> bool:
        """
        Check if invoice can accept a credit note of the given amount.
        
        Args:
            credit_amount: The amount to check
            
        Returns:
            bool: True if invoice can accept the credit note
        """
        if credit_amount <= Decimal('0.00'):
            return False
        
        return credit_amount <= self.available_credit_balance
    
    def get_credit_summary(self) -> dict:
        """
        Get comprehensive summary of credits for this invoice.
        
        Returns:
            dict: Summary with total credits, available balance, etc.
        """
        return {
            'invoice_id': self.id,
            'invoice_number': self.doc_number,
            'invoice_total': float(self.total_amt),
            'total_credits_applied': float(self.total_credits_applied),
            'available_credit_balance': float(self.available_credit_balance),
            'is_fully_credited': self.is_fully_credited,
            'credit_utilization_percentage': float(self.credit_utilization_percentage),
            'linked_credit_notes_count': self.credit_notes.count(),
        }
    
    
    # All existing methods remain unchanged
    def __str__(self):
        return f"Invoice {self.doc_number or self.qb_invoice_id} - {self.customer_name}"

    @property
    def linked_customer(self):
        return self.customer

    @property
    def has_stub_customer(self):
        """Check if this invoice is linked to a stub customer"""
        return self.customer and getattr(self.customer, 'is_stub', False)

    @property
    def customer_quality(self):
        """Get customer link quality"""
        if not self.customer:
            return "missing"
        elif self.has_stub_customer:
            return "stub"
        else:
            return "complete"
        
    # In the Invoice model class
    def get_annotated_available_balance(self):
        """Safely get available balance from annotated field or calculate"""
        if hasattr(self, 'available_balance'):
            return self.available_balance
        return self.available_credit_balance

    def get_annotated_total_credits_applied(self):
        """Safely get total credits applied from annotated field or calculate"""
        # Check for the annotated field (instance attribute)
        if hasattr(self, '_total_credits_applied') or 'total_credits_applied' in self.__dict__:
            return self.total_credits_applied  
        return self.calculate_total_credits_applied() 

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

    # Enhanced tax information
    tax_code_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxCodeRef value")
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('invoice', 'line_num')
        ordering = ['line_num']
    
    def __str__(self):
        return f"{self.item_name} - {self.amount}"