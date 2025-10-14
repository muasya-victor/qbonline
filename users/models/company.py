import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
from common.models import TimeStampModel  # base model with created/updated fields

User = settings.AUTH_USER_MODEL


class Company(TimeStampModel):
    """
    Represents a tenant (company) in the system. A company can be associated with many users.
    QuickBooks connection data is stored here (one connection per company).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="default")

    # QuickBooks fields
    realm_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    is_connected = models.BooleanField(default=False)  # keep for admin / filtering

    # QuickBooks Company Metadata
    qb_company_name = models.CharField(max_length=255, null=True, blank=True)
    qb_legal_name = models.CharField(max_length=255, null=True, blank=True)
    qb_country = models.CharField(max_length=100, null=True, blank=True)
    qb_address = models.JSONField(null=True, blank=True)  # Store full address object
    qb_phone = models.CharField(max_length=50, null=True, blank=True)
    qb_email = models.EmailField(null=True, blank=True)
    qb_website = models.URLField(null=True, blank=True)
    qb_fiscal_year_start = models.CharField(max_length=20, null=True, blank=True)
    qb_supported_languages = models.CharField(max_length=100, null=True, blank=True)
    qb_name_value = models.CharField(max_length=255, null=True, blank=True)  # Company display name from QB
    qb_company_info = models.JSONField(null=True, blank=True)  # Store full company info response
    currency_code = models.CharField(max_length=3, default='USD')  # Company currency from QB

    # Invoice branding and template fields
    logo_url = models.URLField(null=True, blank=True)  # Company logo URL
    invoice_template_id = models.CharField(max_length=50, null=True, blank=True)  # QB template ID
    invoice_template_name = models.CharField(max_length=255, null=True, blank=True)
    invoice_logo_enabled = models.BooleanField(default=True)  # Show logo on invoices
    brand_color = models.CharField(max_length=7, default='#0077C5')  # Hex color for branding
    invoice_footer_text = models.TextField(null=True, blank=True)  # Custom footer text

    # Token information
    access_token = models.TextField(null=True, blank=True)
    refresh_token = models.TextField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    token_data = models.JSONField(null=True, blank=True)
    is_connected_db = models.BooleanField(default=False)

    # Optional: who initially created this company
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_companies")

    class Meta:
        indexes = [
            models.Index(fields=["realm_id"]),
            models.Index(fields=["name"]),
        ]

    @property
    def is_connected(self):
        """
        Returns True if access_token exists and has more than 50 minutes before expiry.
        """
        if self.access_token and self.access_token_expires_at:
            remaining = self.access_token_expires_at - timezone.now()
            return remaining > timedelta(minutes=50)
        return False

    def mark_connected(self, token_response: dict):
        """
        Helper: update tokens from QuickBooks token response dict.
        """
        self.access_token = token_response.get("access_token")
        self.refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in")
        if expires_in:
            self.access_token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        self.token_data = token_response

        self.is_connected_db = self.is_connected_db
        self.save(update_fields=[
            "access_token",
            "refresh_token",
            "access_token_expires_at",
            "token_data",
            "is_connected_db",
        ])

    def update_company_info(self, company_info: dict):
        """
        Update company metadata from QuickBooks CompanyInfo API response
        """
        if not company_info:
            return

        # Extract data from QB response
        self.qb_company_info = company_info
        self.qb_company_name = company_info.get("CompanyName")
        self.qb_legal_name = company_info.get("LegalName")
        self.qb_country = company_info.get("Country")
        self.qb_fiscal_year_start = company_info.get("FiscalYearStartMonth")
        self.qb_supported_languages = company_info.get("SupportedLanguages")
        self.qb_name_value = company_info.get("Name")

        # Handle currency - extract from preferences or default to USD
        preferences = company_info.get("Preferences")
        if preferences:
            currency_prefs = preferences.get("CurrencyPrefs")
            if currency_prefs:
                self.currency_code = currency_prefs.get("HomeCurrency", {}).get("value", "USD")

        # Handle address
        address = company_info.get("CompanyAddr")
        if address:
            self.qb_address = address

        # Handle email
        email = company_info.get("Email")
        if email and email.get("Address"):
            self.qb_email = email["Address"]

        # Handle phone
        phone = company_info.get("PrimaryPhone")
        if phone and phone.get("FreeFormNumber"):
            self.qb_phone = phone["FreeFormNumber"]

        # Handle website
        website = company_info.get("WebAddr")
        if website and website.get("URI"):
            self.qb_website = website["URI"]

        # Handle logo (if available in QB response)
        logo_info = company_info.get("Logo")
        if logo_info and logo_info.get("URI"):
            self.logo_url = logo_info["URI"]

        # Extract invoice template preferences (if available)
        template_prefs = company_info.get("TemplateRef")
        if template_prefs:
            self.invoice_template_id = template_prefs.get("value")

        # Update the display name to use QB company name if available
        if self.qb_company_name and self.name == "default":
            self.name = self.qb_company_name

        self.save(update_fields=[
            "name",
            "qb_company_name",
            "qb_legal_name",
            "qb_country",
            "qb_address",
            "qb_phone",
            "qb_email",
            "qb_website",
            "qb_fiscal_year_start",
            "qb_supported_languages",
            "qb_name_value",
            "qb_company_info",
            "currency_code",
            "logo_url",
            "invoice_template_id",
        ])

    def disconnect(self):
        self.access_token = None
        self.refresh_token = None
        self.access_token_expires_at = None
        self.refresh_token_expires_at = None
        self.token_data = None
        self.is_connected = False
        self.save()


class CompanyMembership(models.Model):
    """
    Through model relating users to companies.
    This allows per-user flags (e.g. is_default) for a company.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="company_memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    is_default = models.BooleanField(default=False)
    # role: admin/member/etc (optional)
    role = models.CharField(max_length=50, default="member")

    class Meta:
        unique_together = ("user", "company")
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["company"]),
            models.Index(fields=["is_default"]),
        ]

    def save(self, *args, **kwargs):
        # If this membership becomes default, unset other defaults for this user
        if self.is_default:
            CompanyMembership.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)



class ActiveCompany(models.Model):
    """
    Stores the current active company for a given user.
    Unlike 'is_default' in CompanyMembership, this is meant
    to reflect what the user is actively working on right now.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="active_company"
    )
    company = models.ForeignKey(
        "Company", 
        on_delete=models.CASCADE,
        related_name="active_users"
    )
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Active Company"
        verbose_name_plural = "Active Companies"

    def __str__(self):
        return f"{self.user} active in {self.company}"

