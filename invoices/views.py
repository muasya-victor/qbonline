# invoices/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch
from .models import Invoice, InvoiceLine
from .serializers import InvoiceSerializer, CompanyInfoSerializer, InvoiceLineSerializer
from .services import QuickBooksInvoiceService
from companies.models import ActiveCompany
from kra.models import KRAInvoiceSubmission
from kra.services import KRAInvoiceService


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
        """Filter invoices by user's active company with optimized prefetching"""
        active_company = get_active_company(self.request.user)
        if not active_company:
            return Invoice.objects.none()

        # Prefetch KRA submissions to avoid N+1 queries
        kra_submissions_prefetch = Prefetch(
            'kra_submissions',
            queryset=KRAInvoiceSubmission.objects.select_related('company').order_by('-created_at')
        )

        queryset = Invoice.objects.filter(
            company=active_company
        ).select_related('company').prefetch_related(
            'line_items',
            kra_submissions_prefetch
        ).order_by('-txn_date')

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

        # Apply KRA validation filter
        kra_validated = self.request.query_params.get('kra_validated')
        if kra_validated is not None:
            if kra_validated.lower() == 'true':
                # Invoices with at least one successful KRA submission
                queryset = queryset.filter(
                    kra_submissions__status__in=['success', 'signed']
                ).distinct()
            elif kra_validated.lower() == 'false':
                # Invoices without successful KRA submissions
                queryset = queryset.exclude(
                    kra_submissions__status__in=['success', 'signed']
                )

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

        # Add KRA submission stats
        kra_stats = {
            'total_submissions': KRAInvoiceSubmission.objects.filter(company=active_company).count(),
            'successful_submissions': KRAInvoiceSubmission.objects.filter(
                company=active_company, 
                status__in=['success', 'signed']
            ).count(),
            'failed_submissions': KRAInvoiceSubmission.objects.filter(
                company=active_company, 
                status='failed'
            ).count(),
            'pending_submissions': KRAInvoiceSubmission.objects.filter(
                company=active_company, 
                status__in=['pending', 'submitted']
            ).count()
        }

        return Response({
            'success': True,
            'invoices': serializer.data,
            'pagination': pagination_info,
            'company_info': company_serializer.data,
            'kra_stats': kra_stats
        })

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a single invoice with detailed information"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        
        # Get additional KRA submission details
        kra_submissions = instance.kra_submissions.all().order_by('-created_at')
        kra_serializer = KRASubmissionSerializer(kra_submissions, many=True)
        
        response_data = serializer.data
        response_data['kra_submissions_detail'] = kra_serializer.data
        response_data['kra_submissions_count'] = kra_submissions.count()
        
        return Response({
            'success': True,
            'invoice': response_data
        })

    @action(detail=True, methods=['get'])
    def kra_submissions(self, request, pk=None):
        """Get all KRA submissions for a specific invoice"""
        invoice = self.get_object()
        submissions = invoice.kra_submissions.all().order_by('-created_at')
        
        serializer = KRASubmissionSerializer(submissions, many=True)
        
        return Response({
            'success': True,
            'invoice_id': str(invoice.id),
            'invoice_number': invoice.doc_number,
            'customer_name': invoice.customer_name,
            'kra_submissions': serializer.data,
            'total_submissions': submissions.count(),
            'latest_submission': KRASubmissionSerializer(submissions.first()).data if submissions.exists() else None
        })

    @action(detail=True, methods=['post'])
    def submit_to_kra(self, request, pk=None):
        """Submit a specific invoice to KRA"""
        try:
            invoice = self.get_object()
            company = invoice.company
            
            # Verify user has access to this company
            if not request.user.company_memberships.filter(company=company).exists():
                return Response({
                    'success': False,
                    'error': 'You do not have access to this company'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Check if KRA config exists
            if not hasattr(company, 'kra_config'):
                return Response({
                    'success': False,
                    'error': 'KRA configuration not found for this company'
                }, status=status.HTTP_400_BADDEN_REQUEST)
            
            # Submit to KRA
            kra_service = KRAInvoiceService(company.id)
            result = kra_service.submit_to_kra(invoice.id)
            
            if result['success']:
                submission = result['submission']
                response_data = {
                    'success': True,
                    'message': 'Invoice successfully submitted to KRA',
                    'submission_id': str(submission.id),
                    'kra_invoice_number': submission.kra_invoice_number,
                    'receipt_signature': submission.receipt_signature,
                    'qr_code_data': submission.qr_code_data,
                    'status': submission.status,
                    'kra_response': result.get('kra_response', {})
                }
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                return Response({
                    'success': False, 
                    'error': result['error']
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            return Response({
                'success': False, 
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def line_items(self, request, pk=None):
        """Get all line items for a specific invoice"""
        invoice = self.get_object()
        line_items = invoice.line_items.all().order_by('line_num')
        
        serializer = InvoiceLineSerializer(line_items, many=True)
        
        return Response({
            'success': True,
            'invoice_id': str(invoice.id),
            'invoice_number': invoice.doc_number,
            'line_items': serializer.data,
            'total_line_items': line_items.count(),
            'total_amount': float(invoice.total_amt),
            'subtotal': float(invoice.subtotal),
            'tax_total': float(invoice.tax_total)
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

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get invoice statistics for the active company"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Invoice statistics
        total_invoices = Invoice.objects.filter(company=active_company).count()
        paid_invoices = Invoice.objects.filter(company=active_company, balance=0).count()
        unpaid_invoices = Invoice.objects.filter(company=active_company, balance__gt=0).count()
        
        # Amount statistics
        total_amount = Invoice.objects.filter(company=active_company).aggregate(
            total=Sum('total_amt')
        )['total'] or 0
        outstanding_balance = Invoice.objects.filter(company=active_company).aggregate(
            total=Sum('balance')
        )['total'] or 0
        
        # KRA statistics
        kra_validated_invoices = Invoice.objects.filter(
            company=active_company,
            kra_submissions__status__in=['success', 'signed']
        ).distinct().count()

        return Response({
            'success': True,
            'stats': {
                'total_invoices': total_invoices,
                'paid_invoices': paid_invoices,
                'unpaid_invoices': unpaid_invoices,
                'total_amount': float(total_amount),
                'outstanding_balance': float(outstanding_balance),
                'kra_validated_invoices': kra_validated_invoices,
                'validation_rate': round((kra_validated_invoices / total_invoices * 100), 2) if total_invoices > 0 else 0
            },
            'company': active_company.name
        })

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent invoices (last 10)"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected'
            }, status=status.HTTP_400_BAD_REQUEST)

        recent_invoices = Invoice.objects.filter(
            company=active_company
        ).select_related('company').prefetch_related(
            Prefetch('kra_submissions', queryset=KRAInvoiceSubmission.objects.order_by('-created_at'))
        ).order_by('-created_at')[:10]

        serializer = self.get_serializer(recent_invoices, many=True)

        return Response({
            'success': True,
            'invoices': serializer.data,
            'total_count': recent_invoices.count()
        })

    def create(self, request, *args, **kwargs):
        """Create is not allowed via API - invoices come from QuickBooks"""
        return Response({
            'success': False,
            'error': 'Invoices can only be created via QuickBooks sync'
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        """Update is not allowed via API - invoices come from QuickBooks"""
        return Response({
            'success': False,
            'error': 'Invoices can only be updated via QuickBooks sync'
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        """Delete is not allowed via API - invoices come from QuickBooks"""
        return Response({
            'success': False,
            'error': 'Invoices can only be deleted via QuickBooks sync'
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)
    

# views.py
import os
import qrcode
from io import BytesIO
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from weasyprint import HTML
import tempfile
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.shortcuts import render
from django.http import HttpResponse


@csrf_exempt
# In your views.py - remove the QR code generation part
def generate_invoice_pdf(request, invoice_id):
    """Generate PDF version of invoice"""
    try:
        invoice = Invoice.objects.get(id=invoice_id)
        company = invoice.company
        
        # Get KRA submission if exists
        kra_submission = None
        if hasattr(invoice, 'kra_submission'):
            kra_submission = invoice.kra_submission
        
        context = {
            'invoice': invoice,
            'company': company,
            'kra_submission': kra_submission,
        }
    
        
        html_string = render_to_string('invoices/invoice_template.html', context)
        
        # Create PDF using WeasyPrint
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="invoice_{invoice.doc_number or invoice.qb_invoice_id}.pdf"'
        
        HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
        
        return response
        
    except Invoice.DoesNotExist:
        return HttpResponse("Invoice not found", status=404)


@csrf_exempt
def invoice_detail(request, invoice_id):
    """HTML view of invoice (no login required)"""
    try:
        invoice = Invoice.objects.get(id=invoice_id)
        company = invoice.company

        kra_submission = getattr(invoice, 'kra_submission', None)

        context = {
            'invoice': invoice,
            'company': company,
            'kra_submission': kra_submission,
        }

        return render(request, 'invoices/invoice_template.html', context)

    except Invoice.DoesNotExist:
        return HttpResponse("Invoice not found", status=404)