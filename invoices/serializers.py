# invoices/serializers.py
from rest_framework import serializers
from .models import Invoice, InvoiceLine
from companies.models import Company
from kra.models import KRAInvoiceSubmission

class InvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for invoice line items with enhanced tax fields"""

    class Meta:
        model = InvoiceLine
        fields = [
            'id', 'line_num', 'item_ref_value', 'item_name',
            'description', 'qty', 'unit_price', 'amount',
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
    """Serializer for invoices with company currency support and enhanced tax fields"""

    line_items = InvoiceLineSerializer(many=True, read_only=True)
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    status = serializers.SerializerMethodField()
    is_kra_validated = serializers.SerializerMethodField()
    
    # New field for KRA submissions
    # kra_submissions = KRASubmissionSerializer(many=True, read_only=True)
    
    # For backward compatibility - latest submission
    kra_submission = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'qb_invoice_id', 'doc_number', 'txn_date', 'due_date',
            'customer_name', 'customer_ref_value', 'total_amt', 'balance', 
            'subtotal', 'tax_total', 'tax_rate_ref', 'tax_percent',
            'private_note', 'customer_memo', 'currency_code', 'status', 
            'line_items', 'is_kra_validated', 'kra_submission',
            'template_id', 'template_name', 'sync_token', 'raw_data',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

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
        
        # Ensure tax fields are properly formatted
        if representation.get('tax_percent') is not None:
            representation['tax_percent'] = float(representation['tax_percent'])
        if representation.get('tax_total') is not None:
            representation['tax_total'] = float(representation['tax_total'])
        if representation.get('subtotal') is not None:
            representation['subtotal'] = float(representation['subtotal'])
        if representation.get('total_amt') is not None:
            representation['total_amt'] = float(representation['total_amt'])
        if representation.get('balance') is not None:
            representation['balance'] = float(representation['balance'])
            
        return representation


class CompanyInfoSerializer(serializers.ModelSerializer):
    """Serializer for company information in API responses"""

    formatted_address = serializers.SerializerMethodField()
    contact_info = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'name', 'qb_company_name', 'qb_legal_name', 'realm_id',
            'currency_code', 'logo_url', 'invoice_logo_enabled',
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