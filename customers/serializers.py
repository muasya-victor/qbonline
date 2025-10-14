# customers/serializers.py
from rest_framework import serializers
from .models import Customer

class CustomerSerializer(serializers.ModelSerializer):
    primary_contact = serializers.ReadOnlyField()
    billing_address = serializers.ReadOnlyField()
    shipping_address = serializers.ReadOnlyField()
    currency_code = serializers.CharField(source='company.currency_code', read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            "id", "company", "qb_customer_id", "display_name", "given_name", 
            "family_name", "company_name", "email", "phone", "mobile", "fax", 
            "website", "bill_addr_line1", "bill_addr_line2", "bill_addr_city",
            "bill_addr_state", "bill_addr_postal_code", "bill_addr_country",
            "ship_addr_line1", "ship_addr_line2", "ship_addr_city", 
            "ship_addr_state", "ship_addr_postal_code", "ship_addr_country",
            "balance", "balance_with_jobs", "active", "sync_token", "notes",
            "taxable", "tax_code_ref_value", "tax_code_ref_name", "raw_data",
            "created_at", "updated_at", "primary_contact", "billing_address",
            "shipping_address", "currency_code"
        ]
        read_only_fields = [
            "qb_customer_id", "sync_token", "balance", "balance_with_jobs", 
            "raw_data", "created_at", "updated_at"
        ]

class CustomerCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating customers (includes writeable fields for QB sync)"""
    
    class Meta:
        model = Customer
        fields = [
            "display_name", "given_name", "family_name", "company_name",
            "email", "phone", "mobile", "fax", "website",
            "bill_addr_line1", "bill_addr_line2", "bill_addr_city",
            "bill_addr_state", "bill_addr_postal_code", "bill_addr_country",
            "ship_addr_line1", "ship_addr_line2", "ship_addr_city",
            "ship_addr_state", "ship_addr_postal_code", "ship_addr_country",
            "active", "notes", "taxable", "tax_code_ref_value", "tax_code_ref_name"
        ]
    
    def validate(self, data):
        """Validate that either company_name or given_name/family_name is provided"""
        if not data.get('company_name') and not (data.get('given_name') or data.get('family_name')):
            raise serializers.ValidationError(
                "Either company name or individual name (given/family) must be provided."
            )
        return data