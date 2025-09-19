# invoices/serializers.py
from rest_framework import serializers
from .models import Invoice, InvoiceLine
from users.models import Company

class InvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for invoice line items"""

    class Meta:
        model = InvoiceLine
        fields = [
            'id', 'line_num', 'item_ref_value', 'item_name',
            'description', 'qty', 'unit_price', 'amount',
            'tax_code_ref', 'tax_amount', 'tax_rate'
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Serializer for invoices with company currency support"""

    line_items = InvoiceLineSerializer(many=True, read_only=True)
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'qb_invoice_id', 'doc_number', 'txn_date', 'due_date',
            'customer_name', 'total_amt', 'balance', 'subtotal', 'tax_total',
            'private_note', 'customer_memo', 'currency_code', 'status', 'line_items'
        ]

    def get_status(self, obj):
        """Determine invoice status based on balance"""
        if obj.balance == 0:
            return 'paid'
        elif obj.balance == obj.total_amt:
            return 'unpaid'
        else:
            return 'partial'


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

