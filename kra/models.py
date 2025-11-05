# kra/models.py
import uuid
from django.db import models
from django.conf import settings
from companies.models import Company
from invoices.models import Invoice
from common.models import TimeStampModel
from django.utils import timezone
from creditnote.models import CreditNote


class KRACompanyConfig(TimeStampModel):
    """KRA configuration for each company"""
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='kra_config')
    tin = models.CharField(max_length=11)  # P051139031T
    bhf_id = models.CharField(max_length=2, default="00")
    cmc_key = models.TextField(help_text="KRA CMC Key for authentication")
    trade_name = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    top_message = models.CharField(max_length=10, blank=True)
    bottom_message = models.CharField(max_length=10, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "KRA Company Configuration"
        verbose_name_plural = "KRA Company Configurations"

    def __str__(self):
        return f"KRA Config - {self.company.name}"

class KRAInvoiceCounter(models.Model):
    """Sequential invoice counter for KRA submissions per company - used for both invoices and credit notes"""
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='kra_invoice_counter')
    last_invoice_number = models.IntegerField(default=0)
    last_credit_note_number = models.IntegerField(default=0)
    last_used_date = models.DateField(auto_now=True)
    
    class Meta:
        verbose_name = "KRA Invoice Counter"
        verbose_name_plural = "KRA Invoice Counters"

    def __str__(self):
        return f"Counter - {self.company.name}: {self.last_invoice_number}"

class KRAInvoiceSubmission(TimeStampModel):
    """Tracks KRA submissions for both invoices and credit notes"""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='kra_submissions')
    invoice = models.ForeignKey(
        Invoice, 
        on_delete=models.CASCADE, 
        related_name='kra_submissions',
        null=True,
        blank=True
    )
    credit_note = models.ForeignKey(
        CreditNote,
        on_delete=models.CASCADE,
        related_name='kra_submissions', 
        null=True,
        blank=True
    )
    kra_invoice_number = models.IntegerField() 
    trd_invoice_no = models.CharField(max_length=100)  
    document_type = models.CharField(
        max_length=20,
        choices=[
            ('invoice', 'Invoice'),
            ('credit_note', 'Credit Note')
        ],
        default='invoice'
    )
    submitted_data = models.JSONField() 
    response_data = models.JSONField(null=True, blank=True)  
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('submitted', 'Submitted'),
            ('success', 'Success'),
            ('signed', 'Signed'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled')
        ],
        default='pending'
    )
    error_message = models.TextField(blank=True)
    qr_code_data = models.TextField(blank=True)  # QR code content
    receipt_signature = models.CharField(max_length=255, blank=True)  # KRA receipt signature
    
    # Timestamps for tracking
    submitted_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ('company', 'kra_invoice_number')
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['invoice']),
            models.Index(fields=['credit_note']),
            models.Index(fields=['kra_invoice_number']),
            models.Index(fields=['document_type']),
            models.Index(fields=['trd_invoice_no']),
        ]
        verbose_name = "KRA Submission"
        verbose_name_plural = "KRA Submissions"

    def __str__(self):
        doc_type = self.document_type.upper()
        doc_number = self.trd_invoice_no
        return f"KRA {doc_type} {self.kra_invoice_number} - {doc_number} ({self.status})"

    def clean(self):
        """Ensure either invoice or credit_note is set, but not both"""
        from django.core.exceptions import ValidationError
        if not self.invoice and not self.credit_note:
            raise ValidationError("Either invoice or credit_note must be set.")
        if self.invoice and self.credit_note:
            raise ValidationError("Cannot set both invoice and credit_note.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def mark_submitted(self):
        """Mark as submitted to KRA"""
        self.status = 'submitted'
        self.submitted_at = timezone.now()
        self.save()

    def mark_success(self, response_data, receipt_signature=None, qr_code_data=None):
        """Mark as successfully submitted to KRA"""
        self.status = 'success'
        self.validated_at = timezone.now()
        self.response_data = response_data
        self.receipt_signature = receipt_signature or ''
        self.qr_code_data = qr_code_data or ''
        self.save()
        
        # Mark the linked document as KRA validated
        if self.invoice:
            self.invoice.is_kra_validated = True
            self.invoice.save(update_fields=['is_kra_validated', 'updated_at'])
        elif self.credit_note:
            self.credit_note.is_kra_validated = True
            self.credit_note.save(update_fields=['is_kra_validated', 'updated_at'])

    def mark_signed(self):
        """Mark as signed by customer"""
        self.status = 'signed'
        self.save()

    def mark_failed(self, error_message, response_data=None):
        """Mark as failed submission"""
        self.status = 'failed'
        self.error_message = error_message
        if response_data:
            self.response_data = response_data
        self.save()

    @property
    def document(self):
        """Get the linked document (invoice or credit note)"""
        return self.invoice or self.credit_note

    @property
    def is_successful(self):
        """Check if submission was successful"""
        return self.status in ['success', 'signed']

    @property
    def can_retry(self):
        """Check if submission can be retried"""
        return self.status in ['failed', 'pending']
    

    