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
    """Serializer for credit note line items with tax information"""
    
    class Meta:
        model = CreditNoteLine
        fields = [
            "id", "credit_note", "line_num", "item_ref_value", "item_name", "description",
            "qty", "unit_price", "amount",
            "tax_code_ref", "tax_rate_ref", "tax_percent", "tax_amount",
            "raw_data", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

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
    """Serializer for credit notes with comprehensive related data"""
    
    line_items = CreditNoteLineSerializer(many=True, read_only=True)
    related_invoice = RelatedInvoiceSerializer(read_only=True)
    
    # Use the same KRA submission relationship as invoices
    kra_submissions = KRASubmissionSerializer(many=True, read_only=True)
    kra_submission = serializers.SerializerMethodField()
    
    status = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    is_kra_validated = serializers.SerializerMethodField()

    class Meta:
        model = CreditNote
        fields = [
            # Basic identification
            "id", "company", "company_name", "qb_credit_id", "doc_number",
            
            # Dates
            "txn_date", "created_at", "updated_at",
            
            # Amounts and financial data
            "total_amt", "balance", "subtotal", "tax_total", 
            "tax_rate_ref", "tax_percent", "currency_code",
            
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
            "status",
            
            # Raw data
            "raw_data"
        ]
        read_only_fields = [
            "qb_credit_id", "sync_token", "raw_data", "created_at",
            "updated_at", "is_kra_validated"
        ]

    def get_status(self, obj):
        """Calculate status based on balance"""
        if obj.balance == 0:
            return 'applied'
        elif obj.balance > 0:
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
    applied_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()

    class Meta(CreditNoteSerializer.Meta):
        fields = CreditNoteSerializer.Meta.fields + [
            "total_line_items", "tax_breakdown", "applied_amount", "remaining_amount"
        ]

    def get_total_line_items(self, obj):
        """Get total number of line items"""
        return obj.line_items.count()

    def get_tax_breakdown(self, obj):
        """Get tax breakdown information"""
        return {
            "subtotal": float(obj.subtotal),
            "tax_total": float(obj.tax_total),
            "total_amount": float(obj.total_amt),
            "tax_rate_percentage": float(obj.tax_percent)
        }

    def get_applied_amount(self, obj):
        """Get the amount that has been applied"""
        return float(obj.total_amt - obj.balance)

    def get_remaining_amount(self, obj):
        """Get the remaining balance"""
        return float(obj.balance)


class CreditNoteSummarySerializer(serializers.ModelSerializer):
    """Serializer for credit note summary/list views"""
    
    status = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    has_kra_submission = serializers.SerializerMethodField()
    kra_status = serializers.SerializerMethodField()

    class Meta:
        model = CreditNote
        fields = [
            "id", "qb_credit_id", "doc_number", "txn_date", "customer_name",
            "total_amt", "balance", "subtotal", "tax_total",
            "tax_percent", "status", "currency_code",
            "is_kra_validated", "has_kra_submission",
            "kra_status", "created_at"
        ]

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


