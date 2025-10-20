import uuid
from django.db import models
from django.conf import settings
from companies.models import Company
from invoices.models import Invoice
from common.models import TimeStampModel

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