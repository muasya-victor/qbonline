from django.db import models
from companies.models import Company
from common.models import TimeStampModel

class Customer(TimeStampModel):
    """QuickBooks Customer model with stub tracking"""
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='customers')
    qb_customer_id = models.CharField(max_length=50, db_index=True)
    
    # Basic Info
    display_name = models.CharField(max_length=255, db_index=True)
    given_name = models.CharField(max_length=255, blank=True, null=True)
    family_name = models.CharField(max_length=255, blank=True, null=True)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Contact Info
    email = models.EmailField(blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    mobile = models.CharField(max_length=50, blank=True, null=True)
    fax = models.CharField(max_length=50, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    
    # Billing Address
    bill_addr_line1 = models.CharField(max_length=255, blank=True, null=True)
    bill_addr_line2 = models.CharField(max_length=255, blank=True, null=True)
    bill_addr_city = models.CharField(max_length=100, blank=True, null=True)
    bill_addr_state = models.CharField(max_length=100, blank=True, null=True)
    bill_addr_postal_code = models.CharField(max_length=20, blank=True, null=True)
    bill_addr_country = models.CharField(max_length=100, blank=True, null=True)
    
    # Shipping Address
    ship_addr_line1 = models.CharField(max_length=255, blank=True, null=True)
    ship_addr_line2 = models.CharField(max_length=255, blank=True, null=True)
    ship_addr_city = models.CharField(max_length=100, blank=True, null=True)
    ship_addr_state = models.CharField(max_length=100, blank=True, null=True)
    ship_addr_postal_code = models.CharField(max_length=20, blank=True, null=True)
    ship_addr_country = models.CharField(max_length=100, blank=True, null=True)
    
    # Financial Info
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    balance_with_jobs = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Status and Metadata
    active = models.BooleanField(default=True)
    sync_token = models.CharField(max_length=50)
    notes = models.TextField(blank=True, null=True)
    
    # Tax Info
    taxable = models.BooleanField(default=True)
    tax_code_ref_value = models.CharField(max_length=50, blank=True, null=True)
    tax_code_ref_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Stub tracking
    is_stub = models.BooleanField(default=False, help_text="Whether this is a stub customer created from invoice context")
    
    # Raw data
    raw_data = models.JSONField(blank=True, null=True)

    class Meta:
        unique_together = ('company', 'qb_customer_id')
        indexes = [
            models.Index(fields=['company', 'display_name']),
            models.Index(fields=['company', 'email']),
            models.Index(fields=['company', 'active']),
            models.Index(fields=['company', 'is_stub']),  # Add index for stub tracking
            models.Index(fields=['display_name']),
        ]
        ordering = ['display_name']
    
    def __str__(self):
        stub_indicator = " [STUB]" if self.is_stub else ""
        return f"{self.display_name}{stub_indicator} - {self.company.name}"
    
    @property
    def primary_contact(self):
        """Get primary contact name"""
        if self.given_name and self.family_name:
            return f"{self.given_name} {self.family_name}"
        return self.display_name
    
    @property
    def billing_address(self):
        """Formatted billing address"""
        addr_parts = [
            self.bill_addr_line1,
            self.bill_addr_line2,
            self.bill_addr_city,
            self.bill_addr_state,
            self.bill_addr_postal_code,
            self.bill_addr_country
        ]
        return ", ".join(filter(None, addr_parts)) or "No billing address"
    
    @property
    def shipping_address(self):
        """Formatted shipping address"""
        addr_parts = [
            self.ship_addr_line1,
            self.ship_addr_line2,
            self.ship_addr_city,
            self.ship_addr_state,
            self.ship_addr_postal_code,
            self.ship_addr_country
        ]
        return ", ".join(filter(None, addr_parts)) or "No shipping address"
    
    def enhance_from_quickbooks(self):
        """Enhance stub customer with real data from QuickBooks"""
        if not self.is_stub:
            return self
            
        from .services import QuickBooksCustomerService
        customer_service = QuickBooksCustomerService(self.company)
        
        try:
            real_customer_data = customer_service.fetch_customer_from_qb(self.qb_customer_id)
            if real_customer_data:
                enhanced_customer = customer_service.sync_customer_to_db(real_customer_data)
                return enhanced_customer
        except Exception as e:
            print(f"Failed to enhance stub customer {self.qb_customer_id}: {str(e)}")
        
        return self