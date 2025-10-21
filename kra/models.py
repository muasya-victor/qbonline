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
    """Sequential invoice counter for KRA submissions per company"""
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='kra_invoice_counter')
    last_invoice_number = models.IntegerField(default=0)
    last_used_date = models.DateField(auto_now=True)
    
    class Meta:
        verbose_name = "KRA Invoice Counter"
        verbose_name_plural = "KRA Invoice Counters"

    def __str__(self):
        return f"Counter - {self.company.name}: {self.last_invoice_number}"

class KRAInvoiceSubmission(TimeStampModel):
    """Tracks KRA invoice submissions"""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='kra_submissions')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='kra_submissions')
    kra_invoice_number = models.IntegerField()  # Sequential eTIMS number
    trd_invoice_no = models.CharField(max_length=15)  # Your system's INV-001
    submitted_data = models.JSONField()  # Payload sent to KRA
    response_data = models.JSONField(null=True, blank=True)  # KRA response
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('submitted', 'Submitted'),
            ('success', 'Success'),
            ('failed', 'Failed')
        ],
        default='pending'
    )
    error_message = models.TextField(blank=True)
    qr_code_data = models.TextField(blank=True)  # QR code content
    receipt_signature = models.CharField(max_length=100, blank=True)  # KRA receipt signature
    
    class Meta:
        unique_together = ('company', 'kra_invoice_number')
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['invoice']),
            models.Index(fields=['kra_invoice_number']),
        ]
        verbose_name = "KRA Invoice Submission"
        verbose_name_plural = "KRA Invoice Submissions"

    def __str__(self):
        return f"KRA Submission {self.kra_invoice_number} - {self.status}"
 

class KRACreditNoteSubmission(TimeStampModel):
    """Model to track KRA submissions for credit notes"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('submitted', 'Submitted to KRA'),
        ('success', 'Success'),
        ('signed', 'Signed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='kra_credit_note_submissions')
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='kra_submissions')
    
    # KRA submission details
    kra_credit_note_number = models.CharField(max_length=100, blank=True, null=True, help_text="KRA-assigned credit note number")
    
    # ADD THESE FIELDS to match invoice submission
    trd_credit_note_no = models.CharField(max_length=100, blank=True, null=True, help_text="Trading credit note number")
    submitted_data = models.JSONField(blank=True, null=True, help_text="Data submitted to KRA")
    response_data = models.JSONField(blank=True, null=True, help_text="Response from KRA")
    
    receipt_signature = models.TextField(blank=True, null=True, help_text="KRA receipt signature")
    qr_code_data = models.TextField(blank=True, null=True, help_text="QR code data from KRA")
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True, null=True, help_text="Error message if submission failed")
    
    # Timestamps
    submitted_at = models.DateTimeField(blank=True, null=True, help_text="When the submission was sent to KRA")
    validated_at = models.DateTimeField(blank=True, null=True, help_text="When KRA validation was completed")
    
    # Additional metadata
    attempt_count = models.IntegerField(default=1, help_text="Number of submission attempts")
    last_attempt_at = models.DateTimeField(auto_now=True, help_text="Last submission attempt")
    
    class Meta:
        db_table = 'kra_credit_note_submissions'
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['credit_note', 'status']),
            models.Index(fields=['submitted_at']),
            models.Index(fields=['kra_credit_note_number']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"KRA Submission for {self.credit_note.doc_number} - {self.status}"
    
    def mark_submitted(self, submitted_data=None, trd_credit_note_no=None):
        """Mark as submitted to KRA"""
        self.status = 'submitted'
        self.submitted_at = timezone.now()
        if submitted_data:
            self.submitted_data = submitted_data
        if trd_credit_note_no:
            self.trd_credit_note_no = trd_credit_note_no
        self.save()
    
    def mark_success(self, response_data, kra_credit_note_number=None, receipt_signature=None, qr_code_data=None):
        """Mark as successfully submitted to KRA"""
        self.status = 'success'
        self.validated_at = timezone.now()
        self.response_data = response_data
        self.kra_credit_note_number = kra_credit_note_number
        self.receipt_signature = receipt_signature
        self.qr_code_data = qr_code_data
        self.save()
        
        # Also mark the credit note as KRA validated
        self.credit_note.is_kra_validated = True
        self.credit_note.save(update_fields=['is_kra_validated', 'updated_at'])
    
    def mark_failed(self, error_message, response_data=None):
        """Mark as failed submission"""
        self.status = 'failed'
        self.error_message = error_message
        if response_data:
            self.response_data = response_data
        self.attempt_count += 1
        self.save()
    
    def mark_signed(self):
        """Mark as signed by customer"""
        self.status = 'signed'
        self.save()
    
    @property
    def is_successful(self):
        """Check if submission was successful"""
        return self.status in ['success', 'signed']
    
    @property
    def can_retry(self):
        """Check if submission can be retried"""
        return self.status in ['failed', 'pending']

# Add to kra/models.py
class KRACreditNoteCounter(models.Model):
    """Model to track sequential credit note numbers for KRA"""
    company = models.OneToOneField(
        Company, 
        on_delete=models.CASCADE, 
        related_name='kra_credit_note_counter'
    )
    last_credit_note_number = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'kra_credit_note_counter'

    def __str__(self):
        return f"KRA Credit Note Counter - {self.company.name}: {self.last_credit_note_number}"