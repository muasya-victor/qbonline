from rest_framework import serializers
from decimal import Decimal
from .models import CreditNote, CreditNoteLine
from invoices.models import Invoice
from companies.models import Company
from kra.models import KRAInvoiceSubmission
from invoices.serializers import KRASubmissionSerializer
from customers.models import Customer

class SimpleCustomerSerializer(serializers.ModelSerializer):
    """Simplified customer serializer for invoice dropdown"""
    
    class Meta:
        model = Customer
        fields = ['id', 'display_name', 'company_name', 'kra_pin']

from customers.serializers import CustomerSerializer

class InvoiceDropdownSerializer(serializers.ModelSerializer):
    """Serializer for invoice dropdown with customer info"""
    customer = CustomerSerializer(read_only=True)  # Changed from SimpleCustomerSerializer
    customer_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'doc_number', 'qb_invoice_id', 'txn_date', 
            'total_amt', 'customer', 'customer_display'
        ]
    
    def get_customer_display(self, obj):
        """Get display name for customer"""
        if obj.customer:
            return obj.customer.display_name or obj.customer.company_name
        return obj.customer_name or "Unknown Customer"

class RelatedInvoiceSerializer(serializers.ModelSerializer):
    """Enhanced serializer for related invoice information with customer details"""
    customer = CustomerSerializer(read_only=True)  # Changed from SimpleCustomerSerializer
    customer_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Invoice
        fields = [
            "id", "doc_number", "qb_invoice_id", "customer_name", 
            "customer", "customer_display", "total_amt",
            "subtotal", "tax_total", "txn_date", "balance",
            "due_date"
        ]
    
    def get_customer_display(self, obj):
        """Get display name for customer"""
        if obj.customer:
            return obj.customer.display_name or obj.customer.company_name
        return obj.customer_name or "Unknown Customer"

class CreditNoteLineSerializer(serializers.ModelSerializer):
    """Serializer for credit note line items with tax information and KES equivalents"""
    
    amount_kes = serializers.SerializerMethodField()
    tax_amount_kes = serializers.SerializerMethodField()
    unit_price_kes = serializers.SerializerMethodField()
    
    class Meta:
        model = CreditNoteLine
        fields = [
            "id", "credit_note", "line_num", "item_ref_value", "item_name", "description",
            "qty", "unit_price", "amount", "amount_kes", "unit_price_kes",
            "tax_code_ref", "tax_rate_ref", "tax_percent", "tax_amount", "tax_amount_kes",
            "raw_data", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
    
    def get_amount_kes(self, obj):
        """Get line amount in KES"""
        if hasattr(obj, 'amount_kes'):
            return float(obj.amount_kes)
        else:
            # Fallback calculation
            credit_note = obj.credit_note
            if hasattr(credit_note, 'is_foreign_currency') and credit_note.is_foreign_currency:
                return float(obj.amount * credit_note.effective_exchange_rate)
            return float(obj.amount)
    
    def get_tax_amount_kes(self, obj):
        """Get tax amount in KES"""
        if hasattr(obj, 'tax_amount_kes'):
            return float(obj.tax_amount_kes)
        else:
            # Fallback calculation
            credit_note = obj.credit_note
            if hasattr(credit_note, 'is_foreign_currency') and credit_note.is_foreign_currency:
                tax_amount = obj.tax_amount or Decimal('0.00')
                return float(tax_amount * credit_note.effective_exchange_rate)
            return float(obj.tax_amount or 0)
    
    def get_unit_price_kes(self, obj):
        """Get unit price in KES"""
        if hasattr(obj, 'unit_price_kes'):
            return float(obj.unit_price_kes)
        else:
            # Fallback calculation
            credit_note = obj.credit_note
            if hasattr(credit_note, 'is_foreign_currency') and credit_note.is_foreign_currency:
                return float(obj.unit_price * credit_note.effective_exchange_rate)
            return float(obj.unit_price)

class CompanyInfoSerializer(serializers.ModelSerializer):
    """Serializer for company information"""
    
    class Meta:
        model = Company
        fields = [
            "id", "name", "qb_company_name", "qb_legal_name",
            "currency_code", "realm_id", "logo_url", "brand_color",
            "invoice_footer_text"
        ]

class CreditNoteSerializer(serializers.ModelSerializer):
    """Serializer for credit notes with comprehensive related data and currency support"""
    
    line_items = CreditNoteLineSerializer(many=True, read_only=True)
    related_invoice = RelatedInvoiceSerializer(read_only=True)
    
    # Use the same KRA submission relationship as invoices
    kra_submissions = KRASubmissionSerializer(many=True, read_only=True)
    kra_submission = serializers.SerializerMethodField()
    
    # CURRENCY FIELDS - credit note specific (not company)
    currency_code = serializers.SerializerMethodField()
    currency_name = serializers.SerializerMethodField()
    exchange_rate = serializers.SerializerMethodField()
    is_foreign_currency = serializers.SerializerMethodField()
    
    # KES EQUIVALENTS
    total_amt_kes = serializers.SerializerMethodField()
    balance_kes = serializers.SerializerMethodField()
    subtotal_kes = serializers.SerializerMethodField()
    tax_total_kes = serializers.SerializerMethodField()
    
    # Status and validation
    status = serializers.SerializerMethodField()
    status_kes = serializers.SerializerMethodField()
    company_name = serializers.CharField(source='company.name', read_only=True)
    is_kra_validated = serializers.SerializerMethodField()

    class Meta:
        model = CreditNote
        fields = [
            # Basic identification
            "id", "company", "company_name", "qb_credit_id", "doc_number",
            
            # Dates
            "txn_date", "created_at", "updated_at",
            
            # ORIGINAL CURRENCY AMOUNTS
            "total_amt", "balance", "subtotal", "tax_total", 
            
            # CURRENCY INFORMATION
            "currency_code", "currency_name", "exchange_rate", "is_foreign_currency",
            
            # KES EQUIVALENTS
            "total_amt_kes", "balance_kes", "subtotal_kes", "tax_total_kes",
            
            # Tax information
            "tax_rate_ref", "tax_percent",
            
            # Customer information
            "customer_ref_value", "customer_name",
            
            # Notes and metadata
            "private_note", "customer_memo", "sync_token",
            
            # Template information
            "template_id", "template_name",
            
            # Related data
            "related_invoice", "line_items", 
            
            # KRA fields - same as invoices
            "kra_submissions", "kra_submission", "is_kra_validated",
            
            # Status fields
            "status", "status_kes",
            
            # Raw data
            "raw_data"
        ]
        read_only_fields = [
            "qb_credit_id", "sync_token", "raw_data", "created_at",
            "updated_at", "is_kra_validated"
        ]
    
    # CURRENCY METHODS
    def get_currency_code(self, obj):
        """Get the original currency code (USD, KES, EUR, etc.)"""
        if hasattr(obj, 'effective_currency'):
            return obj.effective_currency  # From lazy calculation property
        elif hasattr(obj, 'currency_ref_value') and obj.currency_ref_value:
            return obj.currency_ref_value  # From database field
        else:
            return 'KES'  # Default

    def get_currency_name(self, obj):
        """Get the currency name (United States Dollar, Kenyan Shilling, etc.)"""
        if hasattr(obj, 'currency_name') and obj.currency_name:
            return obj.currency_name  # From database field
        elif hasattr(obj, 'effective_currency'):
            # Map common currency codes to names
            currency_map = {
                'KES': 'Kenyan Shilling',
                'USD': 'United States Dollar',
                'EUR': 'Euro',
                'GBP': 'British Pound',
                # Add more as needed
            }
            return currency_map.get(obj.effective_currency, obj.effective_currency)
        else:
            return 'Kenyan Shilling'  # Default

    def get_exchange_rate(self, obj):
        """Get exchange rate used for this credit note"""
        if hasattr(obj, 'effective_exchange_rate'):
            return float(obj.effective_exchange_rate)
        elif hasattr(obj, 'exchange_rate'):
            return float(obj.exchange_rate)
        else:
            return 1.0  # Default for KES or missing rate

    def get_is_foreign_currency(self, obj):
        """Check if credit note is in foreign currency (not KES)"""
        if hasattr(obj, 'is_foreign_currency'):
            return obj.is_foreign_currency
        else:
            currency = self.get_currency_code(obj)
            return currency != 'KES'

    # KES EQUIVALENT METHODS
    def get_total_amt_kes(self, obj):
        """Get total amount in KES (calculated)"""
        if hasattr(obj, 'total_amt_kes'):
            return float(obj.total_amt_kes)
        else:
            # Calculate on the fly if lazy property not available
            currency = self.get_currency_code(obj)
            exchange_rate = self.get_exchange_rate(obj)
            if currency != 'KES':
                return float(obj.total_amt * Decimal(str(exchange_rate)))
            return float(obj.total_amt)

    def get_balance_kes(self, obj):
        """Get balance in KES (calculated)"""
        if hasattr(obj, 'balance_kes'):
            return float(obj.balance_kes)
        else:
            # Calculate on the fly
            currency = self.get_currency_code(obj)
            exchange_rate = self.get_exchange_rate(obj)
            if currency != 'KES':
                return float(obj.balance * Decimal(str(exchange_rate)))
            return float(obj.balance)

    def get_subtotal_kes(self, obj):
        """Get subtotal in KES (calculated)"""
        if hasattr(obj, 'subtotal_kes'):
            return float(obj.subtotal_kes)
        else:
            # Calculate on the fly
            currency = self.get_currency_code(obj)
            exchange_rate = self.get_exchange_rate(obj)
            if currency != 'KES':
                return float(obj.subtotal * Decimal(str(exchange_rate)))
            return float(obj.subtotal)

    def get_tax_total_kes(self, obj):
        """Get tax total in KES (calculated)"""
        if hasattr(obj, 'tax_total_kes'):
            return float(obj.tax_total_kes)
        else:
            # Calculate on the fly
            currency = self.get_currency_code(obj)
            exchange_rate = self.get_exchange_rate(obj)
            if currency != 'KES':
                return float(obj.tax_total * Decimal(str(exchange_rate)))
            return float(obj.tax_total)

    def get_status(self, obj):
        """Calculate status based on balance in original currency"""
        if obj.balance == 0:
            return 'applied'
        elif obj.balance > 0:
            return 'pending'
        return 'void'

    def get_status_kes(self, obj):
        """Calculate status based on KES balance"""
        balance_kes = self.get_balance_kes(obj)
        if balance_kes == 0:
            return 'applied'
        elif balance_kes > 0:
            return 'pending'
        return 'void'

    def get_is_kra_validated(self, obj):
        """Check if credit note has been successfully validated with KRA"""
        return obj.kra_submissions.filter(status__in=['success', 'signed']).exists()

    def get_kra_submission(self, obj):
        """Get the latest KRA submission for this credit note"""
        latest_submission = obj.kra_submissions.order_by('-created_at').first()
        if latest_submission:
            return KRASubmissionSerializer(latest_submission).data
        return None
    
class CreditNoteUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating credit note related_invoice field WITH VALIDATION"""
    
    class Meta:
        model = CreditNote
        fields = ['related_invoice']
    
    def validate_related_invoice(self, value):
        """Validate that the invoice belongs to the same company AND has available balance"""
        if not value:
            return value
        
        # Get the credit note instance (for partial updates, self.instance exists)
        credit_note = self.instance
        
        if not credit_note:
            # This is a create operation, validation will happen in validate()
            return value
        
        # Check company match
        if value.company != credit_note.company:
            raise serializers.ValidationError("Invoice does not belong to the same company")
        
        # Import validation service
        from creditnote.custom_services.credit_validation_service import CreditNoteValidationService
        
        # Validate credit amount against invoice
        is_valid, error, details = CreditNoteValidationService.validate_credit_amount(
            credit_note.total_amt,
            str(value.id),
            str(credit_note.company.id)
        )
        
        if not is_valid:
            raise serializers.ValidationError(error)
        
        return value
    
    def validate(self, attrs):
        """Additional validation for the entire update"""
        attrs = super().validate(attrs)
        
        # If related_invoice is being set, and this is a create operation,
        # we need to validate the credit amount
        if 'related_invoice' in attrs and not self.instance:
            # This is a create operation
            related_invoice = attrs['related_invoice']
            credit_amount = attrs.get('total_amt', Decimal('0.00'))
            
            if credit_amount <= Decimal('0.00'):
                raise serializers.ValidationError({
                    'total_amt': 'Credit note amount must be greater than zero'
                })
            
            # Import validation service
            from creditnote.custom_services.credit_validation_service import CreditNoteValidationService
            
            is_valid, error, details = CreditNoteValidationService.validate_credit_amount(
                credit_amount,
                str(related_invoice.id),
                str(attrs.get('company', None))
            )
            
            if not is_valid:
                raise serializers.ValidationError({
                    'related_invoice': error
                })
        
        return attrs

class CreditNoteDetailSerializer(CreditNoteSerializer):
    """Extended serializer for detailed credit note view with additional computed fields"""
    
    total_line_items = serializers.SerializerMethodField()
    tax_breakdown = serializers.SerializerMethodField()
    tax_breakdown_kes = serializers.SerializerMethodField()
    applied_amount = serializers.SerializerMethodField()
    applied_amount_kes = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    remaining_amount_kes = serializers.SerializerMethodField()
    original_invoice_currency_match = serializers.SerializerMethodField()
    currency_conversion_warning = serializers.SerializerMethodField()

    class Meta(CreditNoteSerializer.Meta):
        fields = CreditNoteSerializer.Meta.fields + [
            "total_line_items", "tax_breakdown", "tax_breakdown_kes",
            "applied_amount", "applied_amount_kes", "remaining_amount", "remaining_amount_kes",
            "original_invoice_currency_match", "currency_conversion_warning"
        ]

    def get_total_line_items(self, obj):
        """Get total number of line items"""
        return obj.line_items.count()

    def get_tax_breakdown(self, obj):
        """Get tax breakdown information in original currency"""
        return {
            "subtotal": float(obj.subtotal),
            "tax_total": float(obj.tax_total),
            "total_amount": float(obj.total_amt),
            "tax_rate_percentage": float(obj.tax_percent)
        }

    def get_tax_breakdown_kes(self, obj):
        """Get tax breakdown information in KES"""
        return {
            "subtotal_kes": self.get_subtotal_kes(obj),
            "tax_total_kes": self.get_tax_total_kes(obj),
            "total_amount_kes": self.get_total_amt_kes(obj),
            "tax_rate_percentage": float(obj.tax_percent)
        }

    def get_applied_amount(self, obj):
        """Get the amount that has been applied in original currency"""
        return float(obj.total_amt - obj.balance)

    def get_applied_amount_kes(self, obj):
        """Get the amount that has been applied in KES"""
        return self.get_total_amt_kes(obj) - self.get_balance_kes(obj)

    def get_remaining_amount(self, obj):
        """Get the remaining balance in original currency"""
        return float(obj.balance)

    def get_remaining_amount_kes(self, obj):
        """Get the remaining balance in KES"""
        return self.get_balance_kes(obj)

    def get_original_invoice_currency_match(self, obj):
        """Check if credit note currency matches original invoice currency"""
        if hasattr(obj, 'original_invoice_currency_match'):
            return obj.original_invoice_currency_match
        return True  # Default to True if property not available

    def get_currency_conversion_warning(self, obj):
        """Get warning message if currencies don't match"""
        if hasattr(obj, 'currency_conversion_warning'):
            return obj.currency_conversion_warning
        return None

class CreditNoteSummarySerializer(serializers.ModelSerializer):
    """Serializer for credit note summary/list views with currency support"""
    
    status = serializers.SerializerMethodField()
    currency_code = serializers.SerializerMethodField()  # CHANGED: from company.currency_code
    has_kra_submission = serializers.SerializerMethodField()
    kra_status = serializers.SerializerMethodField()
    is_foreign_currency = serializers.SerializerMethodField()
    total_amt_kes = serializers.SerializerMethodField()

    class Meta:
        model = CreditNote
        fields = [
            "id", "qb_credit_id", "doc_number", "txn_date", "customer_name",
            "total_amt", "total_amt_kes", "balance", "subtotal", "tax_total",
            "tax_percent", "status", "currency_code", "is_foreign_currency",
            "is_kra_validated", "has_kra_submission",
            "kra_status", "created_at"
        ]

    def get_currency_code(self, obj):
        """Get the credit note's currency code"""
        if hasattr(obj, 'effective_currency'):
            return obj.effective_currency
        elif hasattr(obj, 'currency_ref_value') and obj.currency_ref_value:
            return obj.currency_ref_value
        else:
            return 'KES'

    def get_is_foreign_currency(self, obj):
        """Check if credit note is in foreign currency"""
        if hasattr(obj, 'is_foreign_currency'):
            return obj.is_foreign_currency
        else:
            currency = self.get_currency_code(obj)
            return currency != 'KES'

    def get_total_amt_kes(self, obj):
        """Get total amount in KES"""
        if hasattr(obj, 'total_amt_kes'):
            return float(obj.total_amt_kes)
        else:
            # Calculate on the fly
            currency = self.get_currency_code(obj)
            if hasattr(obj, 'effective_exchange_rate'):
                exchange_rate = obj.effective_exchange_rate
            elif hasattr(obj, 'exchange_rate'):
                exchange_rate = obj.exchange_rate
            else:
                exchange_rate = Decimal('1.0')
            
            if currency != 'KES':
                return float(obj.total_amt * exchange_rate)
            return float(obj.total_amt)

    def get_status(self, obj):
        """Calculate status based on balance"""
        if obj.balance == 0:
            return 'applied'
        elif obj.balance > 0:
            return 'pending'
        return 'void'

    def get_has_kra_submission(self, obj):
        """Check if credit note has any KRA submissions"""
        return obj.kra_submissions.exists()

    def get_kra_status(self, obj):
        """Get simplified KRA status"""
        if not obj.kra_submissions.exists():
            return 'not_submitted'
        
        latest_submission = obj.kra_submissions.order_by('-created_at').first()
        return latest_submission.status if latest_submission else 'not_submitted'

class InvoiceCreditSummarySerializer(serializers.ModelSerializer):
    """Serializer for invoice with credit summary information"""
    
    customer_display = serializers.SerializerMethodField()
    calculated_total_credits = serializers.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        read_only=True,
        source='get_calculated_total_credits_value'  # Use a method instead of property
    )
    available_balance = serializers.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        read_only=True,
        source='get_available_balance_value'  # Use a method instead of property
    )
    is_fully_credited = serializers.BooleanField(read_only=True, source='get_is_fully_credited_value')
    credit_utilization_percentage = serializers.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        read_only=True,
        source='get_credit_utilization_percentage_value'
    )
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'doc_number', 'qb_invoice_id', 'txn_date', 'due_date',
            'total_amt', 'balance', 'customer_name', 'customer_display',
            'calculated_total_credits', 'available_balance', 'is_fully_credited',
            'credit_utilization_percentage'
        ]
    
    def get_customer_display(self, obj):
        """Get display name for customer"""
        if obj.customer:
            return obj.customer.display_name or obj.customer.company_name
        return obj.customer_name or "Unknown Customer"
    
    # Add these methods to the Invoice model to avoid property setter issues
    # OR use these helper methods in the serializer
    
    def get_calculated_total_credits_value(self, obj):
        """Safe method to get total credits applied"""
        return obj.calculated_total_credits
    
    def get_available_balance_value(self, obj):
        """Safe method to get available balance"""
        return obj.available_credit_balance
    
    def get_is_fully_credited_value(self, obj):
        """Safe method to check if fully credited"""
        return obj.is_fully_credited
    
    def get_credit_utilization_percentage_value(self, obj):
        """Safe method to get credit utilization percentage"""
        return obj.credit_utilization_percentage

class CreditValidationRequestSerializer(serializers.Serializer):
    """Serializer for credit validation requests"""
    
    invoice_id = serializers.UUIDField(required=True)
    credit_amount = serializers.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        required=True,
        min_value=Decimal('0.01')
    )
    
    def validate(self, attrs):
        """Additional validation"""
        credit_amount = attrs['credit_amount']
        
        if credit_amount <= Decimal('0.00'):
            raise serializers.ValidationError({
                'credit_amount': 'Credit amount must be greater than zero'
            })
        
        return attrs


class CreditValidationResponseSerializer(serializers.Serializer):
    """Serializer for credit validation responses"""
    
    valid = serializers.BooleanField()
    message = serializers.CharField()
    available_balance = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    invoice_number = serializers.CharField(required=False)
    invoice_total = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    calculated_total_credits = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    requested_amount = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    error = serializers.CharField(required=False)
    
    def to_representation(self, instance):
        """Custom representation"""
        representation = super().to_representation(instance)
        
        # Convert Decimal fields to float for JSON serialization
        decimal_fields = [
            'available_balance', 'invoice_total', 
            'calculated_total_credits', 'requested_amount'
        ]
        
        for field in decimal_fields:
            if field in representation and representation[field] is not None:
                representation[field] = float(representation[field])
        
        return representation


class InvoiceWithCreditInfoSerializer(serializers.ModelSerializer):
    """Enhanced invoice serializer with credit information for dropdowns"""
    
    customer_display = serializers.SerializerMethodField()
    available_balance = serializers.SerializerMethodField()
    is_fully_credited = serializers.SerializerMethodField()
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'doc_number', 'qb_invoice_id', 'txn_date', 
            'total_amt', 'customer', 'customer_display',
            'available_balance', 'is_fully_credited'
        ]
    
    def get_customer_display(self, obj):
        """Get display name for customer"""
        if obj.customer:
            return obj.customer.display_name or obj.customer.company_name
        return obj.customer_name or "Unknown Customer"
    
    def get_available_balance(self, obj):
        """Get available credit balance"""
        if hasattr(obj, 'available_balance'):
            return obj.available_balance
        return obj.total_amt  # Default to full amount if no credits
    
    def get_is_fully_credited(self, obj):
        """Check if invoice is fully credited"""
        if hasattr(obj, 'is_fully_credited'):
            return obj.is_fully_credited
        
        # Calculate if not annotated
        if hasattr(obj, 'calculated_total_credits'):
            available = obj.total_amt - obj.calculated_total_credits
            return available <= Decimal('0.01')
        
        return False  # Default to not fully credited


