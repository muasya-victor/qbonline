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
        print('active users',request.user)
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
