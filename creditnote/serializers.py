from rest_framework import serializers
from .models import CreditNote, CreditNoteLine
from invoices.models import Invoice

class CreditNoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreditNoteLine
        fields = [
            "id", "line_num", "item_name", "description",
            "qty", "unit_price", "amount", "raw_data"
        ]


class RelatedInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "doc_number", "customer_name", "total_amt", "txn_date"]


class CreditNoteSerializer(serializers.ModelSerializer):
    line_items = CreditNoteLineSerializer(many=True, read_only=True)
    related_invoice = RelatedInvoiceSerializer(read_only=True)
    status = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)

    class Meta:
        model = CreditNote
        fields = [
            "id", "company", "qb_credit_id", "doc_number", "txn_date",
            "total_amt", "balance", "customer_name", "private_note",
            "customer_memo", "sync_token", "template_id", "template_name",
            "related_invoice", "line_items", "raw_data", "created_at", "updated_at",
            "status", "currency_code"
        ]
        read_only_fields = ["qb_credit_id", "sync_token", "raw_data", "created_at", "updated_at"]

    def get_status(self, obj):
        """Calculate status based on balance"""
        if obj.balance == 0:
            return 'applied'
        elif obj.balance > 0:
            return 'pending'
        return 'void'