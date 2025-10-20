import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
from common.models import TimeStampModel  

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
    is_connected_db = models.BooleanField(default=False)  # Database flag for connection status

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

    # Enhanced company details
    preferences_data = models.JSONField(null=True, blank=True)  # Store complete preferences
    
    # Company type and industry
    company_type = models.CharField(max_length=100, null=True, blank=True)
    industry_type = models.CharField(max_length=100, null=True, blank=True)
    company_start_date = models.DateField(null=True, blank=True)
    ein = models.CharField(max_length=50, null=True, blank=True)  # Employer Identification Number
    
    # Communication addresses
    customer_communication_addr = models.JSONField(null=True, blank=True)
    legal_addr = models.JSONField(null=True, blank=True)

    # Currency and financial settings
    currency_code = models.CharField(max_length=3, default='USD')  # Company currency from QB
    multi_currency_enabled = models.BooleanField(default=False)
    
    # Tax preferences
    tax_enabled = models.BooleanField(default=False)
    tax_calculation = models.CharField(
        max_length=20, 
        default="TaxExcluded", 
        choices=[("TaxExcluded", "Tax Excluded"), ("TaxIncluded", "Tax Included")]
    )
    
    # Invoice and billing preferences
    invoice_template_id = models.CharField(max_length=50, null=True, blank=True)  # QB template ID
    invoice_template_name = models.CharField(max_length=255, null=True, blank=True)
    auto_invoice_number = models.BooleanField(default=False)
    default_payment_terms = models.CharField(max_length=100, null=True, blank=True)
    
    # Invoice branding and template fields
    logo_url = models.URLField(null=True, blank=True)  # Company logo URL
    invoice_logo_enabled = models.BooleanField(default=True)  # Show logo on invoices
    brand_color = models.CharField(max_length=7, default='#0077C5')  # Hex color for branding
    invoice_footer_text = models.TextField(null=True, blank=True)  # Custom footer text
    
    # Feature flags from preferences
    time_tracking_enabled = models.BooleanField(default=False)
    inventory_enabled = models.BooleanField(default=False)
    class_tracking_enabled = models.BooleanField(default=False)
    department_tracking_enabled = models.BooleanField(default=False)
    customer_tracking_enabled = models.BooleanField(default=True)
    vendor_tracking_enabled = models.BooleanField(default=True)
    
    # Payment methods and terms
    default_delivery_method = models.CharField(max_length=100, null=True, blank=True)
    default_ship_method = models.CharField(max_length=100, null=True, blank=True)
    
    # Email preferences
    email_when_sent = models.BooleanField(default=False)
    email_when_opened = models.BooleanField(default=False)
    email_when_paid = models.BooleanField(default=False)

    # Token information
    access_token = models.TextField(null=True, blank=True)
    refresh_token = models.TextField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    token_data = models.JSONField(null=True, blank=True)

    # Optional: who initially created this company
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="created_companies"
    )

    class Meta:
        indexes = [
            models.Index(fields=["realm_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["currency_code"]),
            models.Index(fields=["is_connected_db"]),
        ]
        verbose_name = "Company"
        verbose_name_plural = "Companies"

    def __str__(self):
        return f"{self.name} ({self.realm_id})" if self.realm_id else self.name

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
        refresh_token_expires_in = token_response.get("x_refresh_token_expires_in")
        
        if expires_in:
            self.access_token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        if refresh_token_expires_in:
            self.refresh_token_expires_at = timezone.now() + timedelta(seconds=int(refresh_token_expires_in))
            
        self.token_data = token_response
        self.is_connected_db = True

        self.save(update_fields=[
            "access_token",
            "refresh_token",
            "access_token_expires_at",
            "refresh_token_expires_at",
            "token_data",
            "is_connected_db",
        ])

    def update_company_basic_info(self, company_info: dict):
        """
        Update company with basic information from QuickBooks CompanyInfo API response
        (This endpoint does NOT contain logo information)
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

        # Company type and identifiers
        self.company_type = company_info.get("CompanyType")
        company_start_date = company_info.get("CompanyStartDate")
        if company_start_date:
            try:
                from datetime import datetime
                if isinstance(company_start_date, str):
                    self.company_start_date = datetime.strptime(company_start_date, '%Y-%m-%d').date()
                else:
                    self.company_start_date = company_start_date
            except (ValueError, TypeError):
                logger.warning(f"Could not parse company start date: {company_start_date}")
        
        self.ein = company_info.get("EIN")

        # Handle address
        address = company_info.get("CompanyAddr")
        if address:
            self.qb_address = address

        # Handle communication addresses
        customer_communication_addr = company_info.get("CustomerCommunicationAddr")
        if customer_communication_addr:
            self.customer_communication_addr = customer_communication_addr

        legal_addr = company_info.get("LegalAddr")
        if legal_addr:
            self.legal_addr = legal_addr

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

        # Update the display name to use QB company name if available
        if self.qb_company_name and self.name == "default":
            self.name = self.qb_company_name

        update_fields = [
            "name", "qb_company_name", "qb_legal_name", "qb_country", "qb_address",
            "qb_phone", "qb_email", "qb_website", "qb_fiscal_year_start", 
            "qb_supported_languages", "qb_name_value", "qb_company_info", 
            "company_type", "company_start_date", "ein", "customer_communication_addr", 
            "legal_addr"
        ]

        self.save(update_fields=update_fields)

    def update_company_preferences(self, preferences_data: dict):
        """
        Update company with comprehensive preferences from QuickBooks including logo
        """
        if not preferences_data:
            return

        self.preferences_data = preferences_data
        
        # Extract logo first
        self._extract_logo_from_preferences(preferences_data)
        
        # Sales form preferences
        sales_prefs = preferences_data.get("SalesFormsPrefs", {})
        if sales_prefs:
            # Invoice template
            template_ref = sales_prefs.get("DefaultInvoiceTemplateRef", {})
            if template_ref:
                self.invoice_template_id = template_ref.get("value")
                self.invoice_template_name = template_ref.get("name")
            
            # Invoice customization
            self.invoice_logo_enabled = sales_prefs.get("AllowInvoiceLogo", True)
            self.brand_color = sales_prefs.get("BrandingColor", "#0077C5")
            
            # Invoice numbering and terms
            self.auto_invoice_number = sales_prefs.get("AutoInvoiceNumber", False)
            
            default_terms = sales_prefs.get("DefaultTerms")
            if default_terms:
                self.default_payment_terms = default_terms.get("value")
            
            # Shipping and delivery
            self.default_delivery_method = sales_prefs.get("DefaultDeliveryMethod")
            self.default_ship_method = sales_prefs.get("DefaultShipMethod")

        # Currency preferences
        currency_prefs = preferences_data.get("CurrencyPrefs", {})
        if currency_prefs:
            home_currency = currency_prefs.get("HomeCurrency", {})
            self.currency_code = home_currency.get("value", "USD")
            self.multi_currency_enabled = currency_prefs.get("MultiCurrencyEnabled", False)

        # Tax preferences
        tax_prefs = preferences_data.get("TaxPrefs", {})
        if tax_prefs:
            self.tax_enabled = tax_prefs.get("UsingSalesTax", False)
            self.tax_calculation = tax_prefs.get("TaxGroupCodePref", "TaxExcluded")

        # Other important preferences
        other_prefs = preferences_data.get("OtherPrefs", {})
        if other_prefs:
            # Feature flags
            self.time_tracking_enabled = other_prefs.get("TimeTrackingEnabled", False)
            self.inventory_enabled = other_prefs.get("InventoryEnabled", False)
            self.class_tracking_enabled = other_prefs.get("ClassTrackingPerTxn", False)
            self.department_tracking_enabled = other_prefs.get("DepartmentTracking", False)
            
            # Customer/Vendor tracking
            self.customer_tracking_enabled = other_prefs.get("CustomerTracking", True)
            self.vendor_tracking_enabled = other_prefs.get("VendorTracking", True)

        # Email preferences
        email_prefs = preferences_data.get("EmailMessagesPrefs", {})
        if email_prefs:
            self.email_when_sent = email_prefs.get("InvoiceEmailWhenSent", False)
            self.email_when_opened = email_prefs.get("InvoiceEmailWhenOpened", False)
            self.email_when_paid = email_prefs.get("InvoiceEmailWhenPaid", False)

        update_fields = [
            "preferences_data", "currency_code", "multi_currency_enabled",
            "invoice_template_id", "invoice_template_name", "invoice_logo_enabled", 
            "brand_color", "auto_invoice_number", "default_payment_terms", 
            "tax_enabled", "tax_calculation", "time_tracking_enabled", 
            "inventory_enabled", "class_tracking_enabled", "department_tracking_enabled",
            "customer_tracking_enabled", "vendor_tracking_enabled", 
            "default_delivery_method", "default_ship_method", "email_when_sent", 
            "email_when_opened", "email_when_paid"
        ]

        # Add logo_url if it was updated
        if hasattr(self, '_logo_url_updated') and self._logo_url_updated:
            update_fields.append("logo_url")

        self.save(update_fields=update_fields)

    def _extract_logo_from_preferences(self, preferences_data: dict):
        """
        Extract logo URL from preferences data
        """
        try:
            sales_prefs = preferences_data.get("SalesFormsPrefs", {})
            
            # Check if logo is allowed
            allow_logo = sales_prefs.get("AllowInvoiceLogo", False)
            if not allow_logo:
                return
            
            # Look for logo in CustomFormStyles
            custom_form_styles = sales_prefs.get("CustomFormStyles", [])
            
            for form_style in custom_form_styles:
                logo_ref = form_style.get("LogoRef")
                if logo_ref and logo_ref.get("Value"):
                    self.logo_url = logo_ref.get("Value")
                    self._logo_url_updated = True
                    return
            
            # Alternative: Check direct logo reference
            direct_logo_ref = sales_prefs.get("LogoRef")
            if direct_logo_ref and direct_logo_ref.get("Value"):
                self.logo_url = direct_logo_ref.get("Value")
                self._logo_url_updated = True
                
        except Exception as e:
            logger.error(f"Error extracting logo from preferences: {str(e)}")

            
    def get_company_details(self):
        """
        Return comprehensive company details for API responses
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "realm_id": self.realm_id,
            "is_connected": self.is_connected,
            
            # Basic company info
            "qb_company_name": self.qb_company_name,
            "qb_legal_name": self.qb_legal_name,
            "country": self.qb_country,
            "email": self.qb_email,
            "phone": self.qb_phone,
            "website": self.qb_website,
            "address": self.qb_address,
            
            # Company metadata
            "company_type": self.company_type,
            "industry_type": self.industry_type,
            "company_start_date": self.company_start_date,
            "ein": self.ein,
            "fiscal_year_start": self.qb_fiscal_year_start,
            "supported_languages": self.qb_supported_languages,
            
            # Currency and financial settings
            "currency_code": self.currency_code,
            "multi_currency_enabled": self.multi_currency_enabled,
            "tax_enabled": self.tax_enabled,
            "tax_calculation": self.tax_calculation,
            
            # Invoice and branding
            "invoice_template_id": self.invoice_template_id,
            "invoice_template_name": self.invoice_template_name,
            "invoice_logo_enabled": self.invoice_logo_enabled,
            "brand_color": self.brand_color,
            "logo_url": self.logo_url,
            "invoice_footer_text": self.invoice_footer_text,
            "auto_invoice_number": self.auto_invoice_number,
            "default_payment_terms": self.default_payment_terms,
            
            # Features and tracking
            "time_tracking_enabled": self.time_tracking_enabled,
            "inventory_enabled": self.inventory_enabled,
            "class_tracking_enabled": self.class_tracking_enabled,
            "department_tracking_enabled": self.department_tracking_enabled,
            "customer_tracking_enabled": self.customer_tracking_enabled,
            "vendor_tracking_enabled": self.vendor_tracking_enabled,
            
            # Communication addresses
            "customer_communication_addr": self.customer_communication_addr,
            "legal_addr": self.legal_addr,
            
            # Additional preferences
            "default_delivery_method": self.default_delivery_method,
            "default_ship_method": self.default_ship_method,
            "email_when_sent": self.email_when_sent,
            "email_when_opened": self.email_when_opened,
            "email_when_paid": self.email_when_paid,
        }

    def disconnect(self):
        """
        Disconnect company from QuickBooks by clearing tokens
        """
        self.access_token = None
        self.refresh_token = None
        self.access_token_expires_at = None
        self.refresh_token_expires_at = None
        self.token_data = None
        self.is_connected_db = False
        
        self.save(update_fields=[
            "access_token",
            "refresh_token", 
            "access_token_expires_at",
            "refresh_token_expires_at",
            "token_data",
            "is_connected_db",
        ])


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

