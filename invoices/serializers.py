# invoices/serializers.py
from rest_framework import serializers
from .models import Invoice, InvoiceLine
from companies.models import Company
from kra.models import KRAInvoiceSubmission
from customers.models import Customer  
from decimal import Decimal

class CustomerSerializer(serializers.ModelSerializer):
    """Serializer for customer information inside invoice"""

    class Meta:
        model = Customer
        fields = [
            'id', 'display_name', 'email', 'phone', 'mobile', 'kra_pin',
            'company_name', 'billing_address', 'shipping_address',
            'is_stub', 'active', 'taxable', 'tax_code_ref_name'
        ]


class InvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for invoice line items with original currency amounts"""
    
    # Optional: KES equivalents for reporting
    amount_kes = serializers.SerializerMethodField()
    tax_amount_kes = serializers.SerializerMethodField()
    
    def get_amount_kes(self, obj):
        """Get line amount in KES"""
        if hasattr(obj, 'amount_kes'):
            return float(obj.amount_kes)
        else:
            # Fallback calculation
            invoice = obj.invoice
            if hasattr(invoice, 'is_foreign_currency') and invoice.is_foreign_currency:
                return float(obj.amount * invoice.effective_exchange_rate)
            return float(obj.amount)
    
    def get_tax_amount_kes(self, obj):
        """Get tax amount in KES"""
        if hasattr(obj, 'tax_amount_kes'):
            return float(obj.tax_amount_kes)
        else:
            # Fallback calculation
            invoice = obj.invoice
            if hasattr(invoice, 'is_foreign_currency') and invoice.is_foreign_currency:
                tax_amount = obj.tax_amount or Decimal('0.00')
                return float(tax_amount * invoice.effective_exchange_rate)
            return float(obj.tax_amount or 0)
    
    class Meta:
        model = InvoiceLine
        fields = [
            'id', 'line_num', 'item_ref_value', 'item_name',
            'description', 'qty', 'unit_price', 'amount',
            'amount_kes', 'tax_amount_kes',  # KES equivalents
            'tax_code_ref', 'tax_rate_ref', 'tax_percent', 'tax_amount'
        ]


class KRASubmissionSerializer(serializers.ModelSerializer):
    """Serializer for KRA invoice submissions"""

    class Meta:
        model = KRAInvoiceSubmission
        fields = [
            'id', 'kra_invoice_number', 'trd_invoice_no', 'status',
            'submitted_data', 'response_data', 'error_message',
            'qr_code_data', 'receipt_signature', 'created_at', 'updated_at'
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Serializer for invoices with original currency support"""

    line_items = InvoiceLineSerializer(many=True, read_only=True)
    
    # CURRENCY FIELDS - invoice specific (not company)
    currency_code = serializers.SerializerMethodField()
    currency_name = serializers.SerializerMethodField()
    exchange_rate = serializers.SerializerMethodField()
    is_foreign_currency = serializers.SerializerMethodField()
    
    # KES EQUIVALENTS (optional, for reporting)
    total_amt_kes = serializers.SerializerMethodField()
    balance_kes = serializers.SerializerMethodField()
    subtotal_kes = serializers.SerializerMethodField()
    tax_total_kes = serializers.SerializerMethodField()
    
    # Status and validation fields
    status = serializers.SerializerMethodField()
    is_kra_validated = serializers.SerializerMethodField()
    customer = CustomerSerializer(read_only=True)
    
    # For backward compatibility - latest submission
    kra_submission = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            # Basic invoice info
            'id', 'qb_invoice_id', 'doc_number', 'txn_date', 'due_date',
            'customer_name', 'customer_ref_value', 'customer',
            
            # ORIGINAL CURRENCY AMOUNTS
            'total_amt', 'balance', 'subtotal', 'tax_total',
            
            # CURRENCY INFORMATION
            'currency_code', 'currency_name', 'exchange_rate', 'is_foreign_currency',
            
            # KES EQUIVALENTS
            'total_amt_kes', 'balance_kes', 'subtotal_kes', 'tax_total_kes',
            
            # Tax information
            'tax_rate_ref', 'tax_percent',
            
            # Status and notes
            'private_note', 'customer_memo', 'status', 'is_kra_validated',
            
            # Line items and submissions
            'line_items', 'kra_submission',
            
            # Template and sync info
            'template_id', 'template_name', 'sync_token', 'raw_data',
            
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

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
        """Get exchange rate used for this invoice"""
        if hasattr(obj, 'effective_exchange_rate'):
            return float(obj.effective_exchange_rate)
        elif hasattr(obj, 'exchange_rate'):
            return float(obj.exchange_rate)
        else:
            return 1.0  # Default for KES or missing rate

    def get_is_foreign_currency(self, obj):
        """Check if invoice is in foreign currency (not KES)"""
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

    # STATUS METHODS
    def get_status(self, obj):
        """Determine invoice status based on balance"""
        if obj.balance == 0:
            return 'paid'
        elif obj.balance == obj.total_amt:
            return 'unpaid'
        else:
            return 'partial'

    def get_is_kra_validated(self, obj):
        """Check if invoice has been successfully validated with KRA"""
        return obj.kra_submissions.filter(status__in=['success', 'signed']).exists()

    def get_kra_submission(self, obj):
        """Get the latest KRA submission for this invoice (backward compatibility)"""
        latest_submission = obj.kra_submissions.order_by('-created_at').first()
        if latest_submission:
            return KRASubmissionSerializer(latest_submission).data
        return None
    
    def to_representation(self, instance):
        """Custom representation to handle tax field names and formatting"""
        representation = super().to_representation(instance)
        
        # Ensure all Decimal fields are properly formatted as floats
        decimal_fields = ['tax_percent', 'tax_total', 'subtotal', 'total_amt', 'balance',
                         'total_amt_kes', 'balance_kes', 'subtotal_kes', 'tax_total_kes',
                         'exchange_rate']
        
        for field in decimal_fields:
            if field in representation and representation[field] is not None:
                representation[field] = float(representation[field])
            
        return representation


class CompanyInfoSerializer(serializers.ModelSerializer):
    """Serializer for company information in API responses"""

    formatted_address = serializers.SerializerMethodField()
    contact_info = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'name', 'qb_company_name', 'qb_legal_name', 'realm_id',
            'logo_url', 'invoice_logo_enabled',
            'brand_color', 'invoice_footer_text', 'formatted_address',
            'contact_info', 'qb_email', 'qb_phone', 'qb_website'
        ]

    def get_formatted_address(self, obj):
        """Format company address for display"""
        if not obj.qb_address:
            return None

        address = obj.qb_address
        parts = []

        if address.get('Line1'):
            parts.append(address['Line1'])
        if address.get('Line2'):
            parts.append(address['Line2'])
        if address.get('City'):
            parts.append(address['City'])
        if address.get('CountrySubDivisionCode'):
            parts.append(address['CountrySubDivisionCode'])
        if address.get('PostalCode'):
            parts.append(address['PostalCode'])
        if address.get('Country'):
            parts.append(address['Country'])

        return ', '.join(parts) if parts else None

    def get_contact_info(self, obj):
        """Get formatted contact information"""
        contact = {}
        if obj.qb_email:
            contact['email'] = obj.qb_email
        if obj.qb_phone:
            contact['phone'] = obj.qb_phone
        if obj.qb_website:
            contact['website'] = obj.qb_website
        return contact if contact else None