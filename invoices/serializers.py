# invoices/serializers.py
from rest_framework import serializers
from .models import Invoice, InvoiceLine

class InvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for invoice line items"""
    
    class Meta:
        model = InvoiceLine
        fields = [
            'id', 'line_num', 'item_ref_value', 'item_name', 
            'description', 'qty', 'unit_price', 'amount'
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Serializer for invoices with line items"""
    
    line_items = InvoiceLineSerializer(many=True, read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'qb_invoice_id', 'doc_number', 'txn_date', 'due_date',
            'customer_name', 'total_amt', 'balance', 'private_note', 
            'customer_memo', 'line_items'
        ]

