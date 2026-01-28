from django.db import models
from django.conf import settings
from common.models import TimeStampModel
from companies.models import Company
from invoices.models import Invoice
from django.db.models import Sum
from decimal import Decimal
import json

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

    customer = models.ForeignKey(
        "customers.Customer", 
        on_delete=models.CASCADE, 
        related_name='credit_notes',
        null=True,
        blank=True
    )

    
    # Original currency amounts (as they come from QuickBooks)
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # KRA validation - same as invoices
    is_kra_validated = models.BooleanField(default=False)
    
    # Enhanced tax information
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # Currency information (NEW - same as Invoice)
    currency_ref_value = models.CharField(
        max_length=10, 
        blank=True, 
        null=True, 
        help_text="Currency code (e.g., USD, KES)"
    )
    currency_name = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        help_text="Currency name"
    )
    
    # Exchange rate at transaction time (NEW - same as Invoice)
    exchange_rate = models.DecimalField(
        max_digits=15, 
        decimal_places=6, 
        default=1.0,
        help_text="Exchange rate to KES at transaction time"
    )
    
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

    # Template info
    template_id = models.CharField(max_length=100, blank=True, null=True)
    template_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('company', 'qb_credit_id')
        indexes = [
            models.Index(fields=['company', 'txn_date']),
            models.Index(fields=['qb_credit_id']),
            models.Index(fields=['customer_name']),
            models.Index(fields=['related_invoice']),
            models.Index(fields=['currency_ref_value']),  # NEW: Index for currency
        ]

    @property
    def effective_currency(self):
        """Get the currency code, extracting from raw_data if not set"""
        if self.currency_ref_value:
            return self.currency_ref_value
        
        # Try to extract from raw_data
        if self.raw_data and isinstance(self.raw_data, dict):
            currency_ref = self.raw_data.get('CurrencyRef', {})
            if isinstance(currency_ref, dict):
                return currency_ref.get('value', 'KES')
        
        return 'KES'  # Default to KES
    
    @property
    def effective_exchange_rate(self):
        """Get the exchange rate, extracting from raw_data if not set"""
        if self.exchange_rate != Decimal('1.0'):
            return self.exchange_rate
        
        # Try to extract from raw_data
        if self.raw_data and isinstance(self.raw_data, dict):
            exchange_rate = self.raw_data.get('ExchangeRate')
            if exchange_rate is not None:
                try:
                    return Decimal(str(exchange_rate))
                except:
                    pass
        
        return Decimal('1.0')  # Default to 1.00
    
    @property
    def is_foreign_currency(self):
        """Check if credit note is in foreign currency (not KES)"""
        return self.effective_currency != 'KES'
    
    @property
    def total_amt_kes(self):
        """Calculate total amount in KES on-the-fly"""
        if self.is_foreign_currency:
            return self.total_amt * self.effective_exchange_rate
        return self.total_amt
    
    @property
    def balance_kes(self):
        """Calculate balance in KES on-the-fly"""
        if self.is_foreign_currency:
            return self.balance * self.effective_exchange_rate
        return self.balance
    
    @property
    def subtotal_kes(self):
        """Calculate subtotal in KES on-the-fly"""
        if self.is_foreign_currency:
            return self.subtotal * self.effective_exchange_rate
        return self.subtotal
    
    @property
    def tax_total_kes(self):
        """Calculate tax total in KES on-the-fly"""
        if self.is_foreign_currency:
            return self.tax_total * self.effective_exchange_rate
        return self.tax_total
    
    @property
    def original_invoice_currency_match(self):
        """Check if credit note currency matches original invoice currency"""
        if not self.related_invoice:
            return True  # No invoice to match against
        
        # Get invoice currency (using Invoice's effective_currency property)
        invoice_currency = self.related_invoice.effective_currency if hasattr(self.related_invoice, 'effective_currency') else 'KES'
        credit_note_currency = self.effective_currency
        
        return invoice_currency == credit_note_currency
    
    @property
    def currency_conversion_warning(self):
        """Get warning message if currencies don't match"""
        if not self.original_invoice_currency_match and self.related_invoice:
            invoice_currency = self.related_invoice.effective_currency if hasattr(self.related_invoice, 'effective_currency') else 'KES'
            return f"Warning: Credit note in {self.effective_currency}, invoice in {invoice_currency}"
        return None
    
    def __str__(self):
        currency = self.effective_currency
        return f"Credit Note {self.doc_number or self.qb_credit_id} - {self.customer_name} ({currency})"

    @property
    def status(self):
        """Calculate status based on balance - same logic as needed for frontend"""
        if self.balance == 0:
            return 'applied'
        elif self.balance > 0:
            return 'pending'
        return 'void'
    
    @property
    def status_kes(self):
        """Calculate status based on KES balance"""
        if self.balance_kes == 0:
            return 'applied'
        elif self.balance_kes > 0:
            return 'pending'
        return 'void'
    
    # Helper method for bulk operations
    @classmethod
    def bulk_calculate_kes_amounts(cls, credit_notes):
        """
        Calculate KES amounts for a list of credit notes efficiently.
        Useful for reports or bulk operations.
        """
        results = []
        for credit_note in credit_notes:
            results.append({
                'credit_note': credit_note,
                'total_amt_kes': credit_note.total_amt_kes,
                'balance_kes': credit_note.balance_kes,
                'currency': credit_note.effective_currency,
                'exchange_rate': float(credit_note.effective_exchange_rate),
                'currency_match': credit_note.original_invoice_currency_match,
            })
        return results


class CreditNoteLine(TimeStampModel):
    """Credit Note line items"""
    
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='line_items')
    line_num = models.IntegerField()
    
    # Item details
    item_ref_value = models.CharField(max_length=50, blank=True, null=True)
    item_name = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Quantities and amounts (original currency)
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # Enhanced tax information
    tax_code_ref = models.CharField(max_length=50, blank=True, null=True)
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('credit_note', 'line_num')
        ordering = ['line_num']
    
    # LAZY CALCULATION PROPERTIES FOR KES AMOUNTS (SAME AS INVOICE LINE)
    
    @property
    def amount_kes(self):
        """Calculate line amount in KES on-the-fly"""
        if self.credit_note.is_foreign_currency:
            return self.amount * self.credit_note.effective_exchange_rate
        return self.amount
    
    @property
    def tax_amount_kes(self):
        """Calculate tax amount in KES on-the-fly"""
        if self.credit_note.is_foreign_currency:
            return self.tax_amount * self.credit_note.effective_exchange_rate
        return self.tax_amount
    
    @property
    def unit_price_kes(self):
        """Calculate unit price in KES on-the-fly"""
        if self.credit_note.is_foreign_currency:
            return self.unit_price * self.credit_note.effective_exchange_rate
        return self.unit_price
    
    def __str__(self):
        currency = self.credit_note.effective_currency if hasattr(self.credit_note, 'effective_currency') else 'KES'
        return f"{self.item_name} - {self.amount} ({currency})"