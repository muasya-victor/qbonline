from rest_framework import serializers
from .models import Company, CompanyMembership, ActiveCompany
from django.conf import settings

User = settings.AUTH_USER_MODEL


class CompanySerializer(serializers.ModelSerializer):
    """Serializer for Company model - used for read operations"""
    is_connected = serializers.ReadOnlyField()
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)
    
    class Meta:
        model = Company
        fields = [
            'id', 'name', 'realm_id', 'is_connected',
            'qb_company_name', 'qb_legal_name', 'qb_country', 
            'qb_address', 'qb_phone', 'qb_email', 'qb_website',
            'qb_fiscal_year_start', 'qb_supported_languages',
            'qb_name_value', 'currency_code', 'logo_url',
            'invoice_template_id', 'invoice_template_name',
            'invoice_logo_enabled', 'brand_color', 'invoice_footer_text',
            'created_by', 'created_by_email', 'created_at', 'updated_at','kra_pin'
        ]
        read_only_fields = [
            'id', 'is_connected', 'created_at', 'updated_at',
            'qb_company_name', 'qb_legal_name', 'qb_country',
            'qb_address', 'qb_phone', 'qb_email', 'qb_website',
            'qb_fiscal_year_start', 'qb_supported_languages',
            'qb_name_value', 'currency_code', 'logo_url',
            'invoice_template_id', 'invoice_template_name'
        ]


class CompanyCreateSerializer(serializers.ModelSerializer):
    """Serializer for Company creation - requires realm_id"""
    class Meta:
        model = Company
        fields = ['name', 'realm_id']
        extra_kwargs = {
            'realm_id': {'required': True},
            'name': {'required': True}
        }

    def validate_realm_id(self, value):
        """Ensure realm_id is unique"""
        if Company.objects.filter(realm_id=value).exists():
            raise serializers.ValidationError("A company with this Realm ID already exists.")
        return value

    def create(self, validated_data):
        """Set created_by to the current user"""
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class CompanyUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating company settings"""
    class Meta:
        model = Company
        fields = [
            'name', 'invoice_logo_enabled', 'brand_color', 
            'invoice_footer_text','kra_pin'
        ]


class CompanyMembershipSerializer(serializers.ModelSerializer):
    """Serializer for CompanyMembership model"""
    company_name = serializers.CharField(source='company.name', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = CompanyMembership
        fields = ['id', 'user', 'user_email', 'company', 'company_name', 'is_default', 'role', 'brand_color']
        read_only_fields = ['id', 'user', 'company']


class ActiveCompanySerializer(serializers.ModelSerializer):
    """Serializer for ActiveCompany model"""
    company_name = serializers.CharField(source='company.name', read_only=True)
    company_realm_id = serializers.CharField(source='company.realm_id', read_only=True)
    company_data = CompanySerializer(source='company', read_only=True)
    
    class Meta:
        model = ActiveCompany
        fields = ['id', 'user', 'company', 'company_name', 'company_realm_id', 'last_updated', 'company_data',]
        read_only_fields = ['id', 'user', 'last_updated', 'company_data']