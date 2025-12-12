from rest_framework import serializers
from .models import Customer
import re

class CustomerSerializer(serializers.ModelSerializer):
    """Serializer for Customer model with read-only fields"""
    
    class Meta:
        model = Customer
        fields = [
            'id', 'qb_customer_id', 'display_name', 'given_name', 'family_name',
            'company_name', 'email', 'phone', 'mobile', 'fax', 'website',
            'kra_pin',
            'balance', 'balance_with_jobs', 'active', 'notes', 'taxable',
            'tax_code_ref_value', 'tax_code_ref_name', 'is_stub',
            'bill_addr_line1', 'bill_addr_line2', 'bill_addr_city',
            'bill_addr_state', 'bill_addr_postal_code', 'bill_addr_country',
            'ship_addr_line1', 'ship_addr_line2', 'ship_addr_city',
            'ship_addr_state', 'ship_addr_postal_code', 'ship_addr_country',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'qb_customer_id', 'created_at', 'updated_at']

class CustomerCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating Customer model"""
    
    class Meta:
        model = Customer
        fields = [
            'display_name', 'given_name', 'family_name', 'company_name',
            'email', 'phone', 'mobile', 'fax', 'website',
            'kra_pin',
            'active', 'notes', 'taxable', 'tax_code_ref_value', 'tax_code_ref_name',
            'bill_addr_line1', 'bill_addr_line2', 'bill_addr_city',
            'bill_addr_state', 'bill_addr_postal_code', 'bill_addr_country',
            'ship_addr_line1', 'ship_addr_line2', 'ship_addr_city',
            'ship_addr_state', 'ship_addr_postal_code', 'ship_addr_country'
        ]
    
    def validate(self, data):
        """Validate that either company name or individual name is provided"""
        company_name = data.get('company_name')
        given_name = data.get('given_name')
        family_name = data.get('family_name')
        
        # Check if we have either company name OR individual name
        has_company_name = bool(company_name and company_name.strip())
        has_individual_name = bool(given_name and given_name.strip()) or bool(family_name and family_name.strip())
        
        if not has_company_name and not has_individual_name:
            raise serializers.ValidationError({
                'non_field_errors': 'Either company name or individual name (given/family) must be provided.'
            })
        
        return data
    
    def validate_kra_pin(self, value):
        """Validate KRA PIN format"""
        if value:
            # Convert to uppercase
            value = value.upper().strip()
            
            # KRA PIN format: One letter, nine digits, one letter
            pattern = r'^[A-Z]{1}\d{9}[A-Z]{1}$'
            if not re.match(pattern, value):
                raise serializers.ValidationError(
                    'KRA PIN must be in the format A000000000B (one letter, nine digits, one letter)'
                )
        return value
    

class SimpleCustomerSerializer(serializers.ModelSerializer):
    """Simplified customer serializer for dropdowns and related fields"""
    
    class Meta:
        model = Customer
        fields = [
            'id', 'qb_customer_id', 'display_name', 'company_name', 'kra_pin',
            'email', 'phone', 'is_stub', 'taxable'
        ]
        read_only_fields = ['id', 'qb_customer_id', 'is_stub']