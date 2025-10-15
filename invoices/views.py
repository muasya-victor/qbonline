# invoices/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Invoice
from .serializers import InvoiceSerializer, CompanyInfoSerializer
from .services import QuickBooksInvoiceService
from companies.models import ActiveCompany


def get_active_company(user):
    """Helper function to get active company with error handling"""
    try:
        active_company = ActiveCompany.objects.get(user=user)
        return active_company.company
    except ActiveCompany.DoesNotExist:
        return None


class InvoiceViewSet(viewsets.ModelViewSet):
    """ViewSet for managing invoices with pagination support"""

    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter invoices by user's active company"""
        active_company = get_active_company(self.request.user)
        if not active_company:
            return Invoice.objects.none()

        queryset = Invoice.objects.filter(
            company=active_company
        ).select_related('company').prefetch_related('line_items').order_by('-txn_date')

        # Apply search filter
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(doc_number__icontains=search) |
                Q(customer_name__icontains=search)
            )

        # Apply status filter
        status_filter = self.request.query_params.get('status')
        if status_filter == 'paid':
            queryset = queryset.filter(balance=0)
        elif status_filter == 'unpaid':
            queryset = queryset.filter(balance__gt=0)

        return queryset

    def list(self, request, *args, **kwargs):
        """List invoices with pagination and company info"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected',
                'message': 'Please select a company first'
            }, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset()

        # Get pagination parameters
        page = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', 20)), 100)

        # Apply pagination
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)

        # Serialize the invoices
        serializer = self.get_serializer(page_obj.object_list, many=True)

        # Serialize company info
        company_serializer = CompanyInfoSerializer(active_company)

        # Build pagination info
        pagination_info = {
            'count': paginator.count,
            'next': page_obj.next_page_number() if page_obj.has_next() else None,
            'previous': page_obj.previous_page_number() if page_obj.has_previous() else None,
            'page_size': page_size,
            'current_page': page,
            'total_pages': paginator.num_pages
        }

        return Response({
            'success': True,
            'invoices': serializer.data,
            'pagination': pagination_info,
            'company_info': company_serializer.data
        })

    @action(detail=False, methods=['post'])
    def sync_from_quickbooks(self, request):
        """Sync invoices from QuickBooks API"""
        try:
            active_company_record = ActiveCompany.objects.select_related("company").filter(user=request.user).first()

            if not active_company_record:
                return Response({
                    'success': False,
                    'error': 'No active company selected',
                    'message': 'Please select or set an active company first.'
                }, status=status.HTTP_400_BAD_REQUEST)

            active_company = active_company_record.company

            # Proceed with sync
            service = QuickBooksInvoiceService(active_company)
            synced_count = service.sync_all_invoices()

            company_serializer = CompanyInfoSerializer(active_company)

            return Response({
                'success': True,
                'message': f'Successfully synced invoices for {active_company.name}',
                'synced_count': synced_count,
                'company_info': company_serializer.data
            })

        except ValueError as e:
            return Response({
                'success': False,
                'error': str(e),
                'message': 'Sync validation failed'
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Sync failed: {str(e)}',
                'message': 'An error occurred during sync'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)