from django.db import models
from django.conf import settings
from common.models import TimeStampModel
from companies.models import Company
from customers.models import Customer
from django.db.models import Sum
from decimal import Decimal
import json


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
    
    # Original currency amounts (as they come from QuickBooks)
    total_amt = models.DecimalField(max_digits=15, decimal_places=2)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_kra_validated = models.BooleanField(default=False)
    
    # Enhanced tax information
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    
    # Currency information (NEW - but optional for backward compatibility)
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
    
    # Exchange rate at transaction time (NEW - but optional)
    exchange_rate = models.DecimalField(
        max_digits=15, 
        decimal_places=6, 
        default=1.0,
        help_text="Exchange rate to KES at transaction time"
    )
    
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
            models.Index(fields=['currency_ref_value']),  # Optional: add if you want to query by currency
        ]
    
    # LAZY CALCULATION PROPERTIES FOR KES AMOUNTS
    # These are calculated on-the-fly without storing in database
    
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
        """Check if invoice is in foreign currency (not KES)"""
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
    def calculated_total_credits(self):
        """
        Calculate total amount of all credit notes linked to this invoice.
        """
        result = self.credit_notes.aggregate(
            total=Sum('total_amt')
        )
        return result['total'] or Decimal('0.00')
    
    @property
    def calculated_total_credits_kes(self):
        """
        Calculate total amount of all credit notes linked to this invoice in KES.
        Uses lazy calculation for credit notes too.
        """
        total_credits_kes = Decimal('0.00')
        for credit_note in self.credit_notes.all():
            # If credit note has its own currency handling, use it
            if hasattr(credit_note, 'total_amt_kes'):
                total_credits_kes += credit_note.total_amt_kes
            else:
                # Assume credit note is in same currency as invoice
                if self.is_foreign_currency:
                    total_credits_kes += credit_note.total_amt * self.effective_exchange_rate
                else:
                    total_credits_kes += credit_note.total_amt
        return total_credits_kes
        
    @property
    def available_credit_balance(self):
        """
        Calculate available balance for new credit notes.
        Returns the amount that can still be credited.
        """
        total_credits = self.calculated_total_credits
        available = self.total_amt - total_credits
        
        # Ensure we don't return negative values
        return max(Decimal('0.00'), available)
    
    @property
    def available_credit_balance_kes(self):
        """
        Calculate available balance for new credit notes in KES.
        Returns the amount that can still be credited.
        """
        total_credits_kes = self.calculated_total_credits_kes
        available_kes = self.total_amt_kes - total_credits_kes
        
        # Ensure we don't return negative values
        return max(Decimal('0.00'), available_kes)
    
    @property
    def is_fully_credited(self):
        """
        Check if invoice is fully credited (with tolerance for rounding errors).
        Uses 1 cent tolerance to handle decimal precision issues.
        """
        return self.available_credit_balance <= Decimal('0.01')
    
    @property
    def is_fully_credited_kes(self):
        """
        Check if invoice is fully credited in KES (with tolerance for rounding errors).
        Uses 1 cent tolerance to handle decimal precision issues.
        """
        return self.available_credit_balance_kes <= Decimal('0.01')
    
    @property
    def credit_utilization_percentage(self):
        """
        Get percentage of invoice that has been credited.
        Useful for reporting.
        """
        if self.total_amt == Decimal('0.00'):
            return Decimal('0.00')
        
        return (self.calculated_total_credits / self.total_amt) * Decimal('100')
    
    @property
    def credit_utilization_percentage_kes(self):
        """
        Get percentage of invoice that has been credited in KES.
        Useful for reporting.
        """
        if self.total_amt_kes == Decimal('0.00'):
            return Decimal('0.00')
        
        return (self.calculated_total_credits_kes / self.total_amt_kes) * Decimal('100')
    
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
    
    def can_accept_credit_note_kes(self, credit_amount_kes: Decimal) -> bool:
        """
        Check if invoice can accept a credit note of the given amount in KES.
        
        Args:
            credit_amount_kes: The amount in KES to check
            
        Returns:
            bool: True if invoice can accept the credit note
        """
        if credit_amount_kes <= Decimal('0.00'):
            return False
        
        return credit_amount_kes <= self.available_credit_balance_kes
    
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
            'invoice_total_kes': float(self.total_amt_kes),
            'currency': self.effective_currency,
            'exchange_rate': float(self.effective_exchange_rate),
            'calculated_total_credits': float(self.calculated_total_credits),
            'calculated_total_credits_kes': float(self.calculated_total_credits_kes),
            'available_credit_balance': float(self.available_credit_balance),
            'available_credit_balance_kes': float(self.available_credit_balance_kes),
            'is_fully_credited': self.is_fully_credited,
            'is_fully_credited_kes': self.is_fully_credited_kes,
            'credit_utilization_percentage': float(self.credit_utilization_percentage),
            'credit_utilization_percentage_kes': float(self.credit_utilization_percentage_kes),
            'linked_credit_notes_count': self.credit_notes.count(),
        }
    
    def __str__(self):
        currency = self.effective_currency
        return f"Invoice {self.doc_number or self.qb_invoice_id} - {self.customer_name} ({currency})"

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
        
    def get_annotated_available_balance(self):
        """Safely get available balance from annotated field or calculate"""
        if hasattr(self, 'available_balance'):
            return self.available_balance
        return self.available_credit_balance
    
    def get_annotated_available_balance_kes(self):
        """Safely get available balance in KES from annotated field or calculate"""
        if hasattr(self, 'available_balance_kes'):
            return self.available_balance_kes
        return self.available_credit_balance_kes

    def get_annotated_calculated_total_credits(self):
        """Safely get total credits applied from annotated field or calculate"""
        # Check for the annotated field (instance attribute)
        if hasattr(self, '_calculated_total_credits') or 'calculated_total_credits' in self.__dict__:
            return self.calculated_total_credits  
        return self.calculated_total_credits
    
    def get_annotated_calculated_total_credits_kes(self):
        """Safely get total credits applied in KES from annotated field or calculate"""
        # Check for the annotated field (instance attribute)
        if hasattr(self, '_calculated_total_credits_kes') or 'calculated_total_credits_kes' in self.__dict__:
            return self.calculated_total_credits_kes  
        return self.calculated_total_credits_kes
    
    # Helper method for bulk operations
    @classmethod
    def bulk_calculate_kes_amounts(cls, invoices):
        """
        Calculate KES amounts for a list of invoices efficiently.
        Useful for reports or bulk operations.
        """
        results = []
        for invoice in invoices:
            results.append({
                'invoice': invoice,
                'total_amt_kes': invoice.total_amt_kes,
                'balance_kes': invoice.balance_kes,
                'currency': invoice.effective_currency,
                'exchange_rate': float(invoice.effective_exchange_rate),
            })
        return results


class InvoiceLine(TimeStampModel):
    """Invoice line items"""
    
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
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
    tax_code_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxCodeRef value")
    tax_rate_ref = models.CharField(max_length=50, blank=True, null=True, help_text="QuickBooks TaxRateRef value")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Actual tax percentage")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Raw QB data
    raw_data = models.JSONField(blank=True, null=True)
    
    class Meta:
        unique_together = ('invoice', 'line_num')
        ordering = ['line_num']
    
    # LAZY CALCULATION PROPERTIES FOR KES AMOUNTS
    
    @property
    def amount_kes(self):
        """Calculate line amount in KES on-the-fly"""
        if self.invoice.is_foreign_currency:
            return self.amount * self.invoice.effective_exchange_rate
        return self.amount
    
    @property
    def tax_amount_kes(self):
        """Calculate tax amount in KES on-the-fly"""
        if self.invoice.is_foreign_currency:
            return self.tax_amount * self.invoice.effective_exchange_rate
        return self.tax_amount
    
    @property
    def unit_price_kes(self):
        """Calculate unit price in KES on-the-fly"""
        if self.invoice.is_foreign_currency:
            return self.unit_price * self.invoice.effective_exchange_rate
        return self.unit_price
    
    def __str__(self):
        currency = self.invoice.effective_currency if hasattr(self.invoice, 'effective_currency') else 'KES'
        return f"{self.item_name} - {self.amount} ({currency})"