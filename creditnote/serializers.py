from rest_framework import serializers
from .models import CreditNote, CreditNoteLine
from invoices.models import Invoice
from companies.models import Company
from kra.models import KRACreditNoteSubmission


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


class KRACreditNoteSubmissionSerializer(serializers.ModelSerializer):
    """Serializer for KRA credit note submissions"""
    
    class Meta:
        model = KRACreditNoteSubmission
        fields = [
            "id", "kra_credit_note_number", "receipt_signature", "qr_code_data",
            "status", "error_message", "response_data", "submitted_at",
            "validated_at", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class RelatedInvoiceSerializer(serializers.ModelSerializer):
    """Serializer for related invoice information"""
    
    class Meta:
        model = Invoice
        fields = [
            "id", "doc_number", "customer_name", "total_amt",
            "subtotal", "tax_total", "txn_date", "balance"
        ]


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
    kra_submissions = KRACreditNoteSubmissionSerializer(many=True, read_only=True)
    status = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    has_kra_submission = serializers.SerializerMethodField()
    latest_kra_status = serializers.SerializerMethodField()
    kra_validation_status = serializers.SerializerMethodField()

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
            "related_invoice", "line_items", "kra_submissions",
            
            # Status fields
            "status", "is_kra_validated", "has_kra_submission",
            "latest_kra_status", "kra_validation_status",
            
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

    def get_has_kra_submission(self, obj):
        """Check if credit note has any KRA submissions"""
        return obj.kra_submissions.exists()

    def get_latest_kra_status(self, obj):
        """Get the status of the latest KRA submission"""
        latest_submission = obj.kra_submissions.order_by('-created_at').first()
        return latest_submission.status if latest_submission else None

    def get_kra_validation_status(self, obj):
        """Get comprehensive KRA validation status"""
        if not obj.kra_submissions.exists():
            return 'not_submitted'
        
        latest_submission = obj.kra_submissions.order_by('-created_at').first()
        if latest_submission.status in ['success', 'signed']:
            return 'validated'
        elif latest_submission.status == 'failed':
            return 'failed'
        elif latest_submission.status in ['pending', 'submitted']:
            return 'pending'
        return 'unknown'


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
