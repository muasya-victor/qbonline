# invoices/serializers.py
from rest_framework import serializers
from .models import Invoice, InvoiceLine


class InvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for invoice line items"""
    
    class Meta:
        model = InvoiceLine
        fields = [
            'id', 'line_num', 'item_ref_value', 'item_name', 
            'description', 'qty', 'unit_price', 'amount'
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Serializer for invoices with line items"""
    
    line_items = InvoiceLineSerializer(many=True, read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'qb_invoice_id', 'doc_number', 'txn_date', 'due_date',
            'customer_name', 'total_amt', 'balance', 'private_note', 
            'customer_memo', 'line_items'
        ]


# invoices/services.py
import requests
from typing import List, Dict, Optional
from django.utils import timezone
from datetime import datetime
from .models import Invoice, InvoiceLine
from users.models import Company


class QuickBooksInvoiceService:
    """Service to fetch and sync invoices from QuickBooks API"""
    
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
    
    def __init__(self, company: Company):
        self.company = company
        if not company.is_connected:
            raise ValueError("Company is not connected to QuickBooks")
    
    def get_headers(self) -> Dict[str, str]:
        """Get authorization headers for QB API"""
        return {
            'Authorization': f'Bearer {self.company.access_token}',
            'Accept': 'application/json'
        }
    
    def fetch_invoices_from_qb(self) -> List[Dict]:
        """Fetch all invoices from QuickBooks API"""
        url = f"{self.BASE_URL}/v3/company/{self.company.realm_id}/query"
        query = "SELECT * FROM Invoice MAXRESULTS 1000"
        
        response = requests.get(
            url,
            headers=self.get_headers(),
            params={'query': query},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        return data.get('QueryResponse', {}).get('Invoice', [])
    
    def sync_invoice_to_db(self, invoice_data: Dict) -> Invoice:
        """Sync single invoice to database"""
        invoice, created = Invoice.objects.update_or_create(
            company=self.company,
            qb_invoice_id=invoice_data['Id'],
            defaults={
                'doc_number': invoice_data.get('DocNumber'),
                'txn_date': datetime.strptime(invoice_data['TxnDate'], '%Y-%m-%d').date(),
                'due_date': datetime.strptime(invoice_data['DueDate'], '%Y-%m-%d').date() if invoice_data.get('DueDate') else None,
                'customer_ref_value': invoice_data.get('CustomerRef', {}).get('value'),
                'customer_name': invoice_data.get('CustomerRef', {}).get('name'),
                'total_amt': invoice_data.get('TotalAmt', 0),
                'balance': invoice_data.get('Balance', 0),
                'private_note': invoice_data.get('PrivateNote'),
                'customer_memo': invoice_data.get('CustomerMemo', {}).get('value'),
                'sync_token': invoice_data.get('SyncToken'),
                'raw_data': invoice_data
            }
        )
        
        # Clear existing line items if updating
        if not created:
            invoice.line_items.all().delete()
        
        # Create line items
        for line_data in invoice_data.get('Line', []):
            if line_data.get('DetailType') == 'SalesItemLineDetail':
                detail = line_data.get('SalesItemLineDetail', {})
                InvoiceLine.objects.create(
                    invoice=invoice,
                    line_num=line_data.get('LineNum', 0),
                    item_ref_value=detail.get('ItemRef', {}).get('value'),
                    item_name=detail.get('ItemRef', {}).get('name'),
                    description=line_data.get('Description', ''),
                    qty=detail.get('Qty', 0),
                    unit_price=detail.get('UnitPrice', 0),
                    amount=line_data.get('Amount', 0),
                    raw_data=line_data
                )
        
        return invoice
    
    def sync_all_invoices(self) -> int:
        """Sync all invoices from QuickBooks to database"""
        invoices_data = self.fetch_invoices_from_qb()
        synced_count = 0
        
        for invoice_data in invoices_data:
            self.sync_invoice_to_db(invoice_data)
            synced_count += 1
        
        return synced_count


# invoices/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import Invoice
from .serializers import InvoiceSerializer
from .services import QuickBooksInvoiceService
from users.models import ActiveCompany


class InvoiceViewSet(viewsets.ModelViewSet):
    """ViewSet for managing invoices"""
    
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter invoices by user's active company"""
        active_company = get_object_or_404(ActiveCompany, user=self.request.user)
        return Invoice.objects.filter(company=active_company.company).select_related('company').prefetch_related('line_items')
    
    @action(detail=False, methods=['post'])
    def sync_from_quickbooks(self, request):
        """Sync invoices from QuickBooks API"""
        active_company = get_object_or_404(ActiveCompany, user=request.user)
        
        try:
            service = QuickBooksInvoiceService(active_company.company)
            synced_count = service.sync_all_invoices()
            
            return Response({
                'success': True,
                'message': f'Successfully synced {synced_count} invoices',
                'synced_count': synced_count
            })
        except ValueError as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Sync failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# invoices/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoices')

urlpatterns = [
    path('', include(router.urls)),
]


# invoices/apps.py
from django.apps import AppConfig

class InvoicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'invoices'


# invoices/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    """Inline for invoice line items"""
    model = InvoiceLine
    extra = 0
    readonly_fields = ('id', 'created_at', 'updated_at')
    fields = ('line_num', 'item_name', 'description', 'qty', 'unit_price', 'amount')
    
    def has_add_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin interface for invoices"""
    
    list_display = [
        'doc_number', 'customer_name', 'txn_date', 'due_date', 
        'total_amt', 'balance', 'company_name', 'connection_status'
    ]
    
    list_filter = [
        'txn_date', 'due_date', 'company', 'created_at'
    ]
    
    search_fields = [
        'doc_number', 'customer_name', 'qb_invoice_id', 
        'private_note', 'customer_memo'
    ]
    
    readonly_fields = [
        'id', 'qb_invoice_id', 'sync_token', 'created_at', 
        'updated_at', 'raw_data_formatted'
    ]
    
    fieldsets = (
        ('Invoice Details', {
            'fields': ('qb_invoice_id', 'doc_number', 'txn_date', 'due_date')
        }),
        ('Customer Information', {
            'fields': ('customer_ref_value', 'customer_name')
        }),
        ('Financial Information', {
            'fields': ('total_amt', 'balance')
        }),
        ('Notes', {
            'fields': ('private_note', 'customer_memo'),
            'classes': ('collapse',)
        }),
        ('System Information', {
            'fields': ('company', 'sync_token', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
        ('QuickBooks Data', {
            'fields': ('raw_data_formatted',),
            'classes': ('collapse',)
        })
    )
    
    inlines = [InvoiceLineInline]
    date_hierarchy = 'txn_date'
    
    def company_name(self, obj):
        """Display company name with link"""
        if obj.company:
            url = reverse('admin:users_company_change', args=[obj.company.id])
            return format_html('<a href="{}">{}</a>', url, obj.company.name)
        return '-'
    company_name.short_description = 'Company'
    company_name.admin_order_field = 'company__name'
    
    def connection_status(self, obj):
        """Display QB connection status"""
        if obj.company and obj.company.is_connected:
            return format_html(
                '<span style="color: green;">●</span> Connected'
            )
        return format_html(
            '<span style="color: red;">●</span> Disconnected'
        )
    connection_status.short_description = 'QB Status'
    
    def raw_data_formatted(self, obj):
        """Display formatted raw QB data"""
        if obj.raw_data:
            import json
            formatted = json.dumps(obj.raw_data, indent=2)
            return format_html(
                '<pre style="background: #f8f8f8; padding: 10px; overflow: auto; max-height: 400px;">{}</pre>',
                formatted
            )
        return 'No data'
    raw_data_formatted.short_description = 'Raw QB Data'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('company')
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    """Admin interface for invoice line items"""
    
    list_display = [
        'invoice_doc_number', 'line_num', 'item_name', 
        'qty', 'unit_price', 'amount', 'invoice_customer'
    ]
    
    list_filter = [
        'invoice__company', 'invoice__txn_date', 'created_at'
    ]
    
    search_fields = [
        'item_name', 'description', 'invoice__doc_number',
        'invoice__customer_name'
    ]
    
    readonly_fields = [
        'id', 'created_at', 'updated_at', 'raw_data_formatted'
    ]
    
    fieldsets = (
        ('Line Item Details', {
            'fields': ('invoice', 'line_num', 'item_ref_value', 'item_name')
        }),
        ('Description & Amounts', {
            'fields': ('description', 'qty', 'unit_price', 'amount')
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
        ('Raw Data', {
            'fields': ('raw_data_formatted',),
            'classes': ('collapse',)
        })
    )
    
    def invoice_doc_number(self, obj):
        """Display invoice doc number with link"""
        url = reverse('admin:invoices_invoice_change', args=[obj.invoice.id])
        return format_html(
            '<a href="{}">{}</a>', 
            url, 
            obj.invoice.doc_number or f'Invoice #{obj.invoice.qb_invoice_id}'
        )
    invoice_doc_number.short_description = 'Invoice'
    invoice_doc_number.admin_order_field = 'invoice__doc_number'
    
    def invoice_customer(self, obj):
        """Display customer name"""
        return obj.invoice.customer_name or '-'
    invoice_customer.short_description = 'Customer'
    invoice_customer.admin_order_field = 'invoice__customer_name'
    
    def raw_data_formatted(self, obj):
        """Display formatted raw QB data"""
        if obj.raw_data:
            import json
            formatted = json.dumps(obj.raw_data, indent=2)
            return format_html(
                '<pre style="background: #f8f8f8; padding: 10px; overflow: auto; max-height: 400px;">{}</pre>',
                formatted
            )
        return 'No data'
    raw_data_formatted.short_description = 'Raw QB Data'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('invoice', 'invoice__company')
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

