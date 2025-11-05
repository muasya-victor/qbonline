from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db.models import Q

from .models import Invoice, InvoiceLine
from .serializers import InvoiceSerializer, InvoiceLineSerializer
from .services import QuickBooksInvoiceService
from companies.models import Company, ActiveCompany


class InvoiceViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing invoices with smart customer sync.
    """
    permission_classes = [IsAuthenticated]

    def get_active_company(self):
        """Get the user's active company"""
        try:
            active_company = ActiveCompany.objects.get(user=self.request.user)
            return active_company.company
        except ActiveCompany.DoesNotExist:
            try:
                membership = self.request.user.company_memberships.filter(is_default=True).first()
                return membership.company if membership else None
            except:
                return None

    def get_queryset(self):
        """Filter invoices by the user's active company"""
        active_company = self.get_active_company()
        if not active_company:
            return Invoice.objects.none()
        
        queryset = Invoice.objects.filter(company=active_company).order_by('-txn_date')
        
        # Apply search filter
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(doc_number__icontains=search) |
                Q(customer_name__icontains=search) |
                Q(customer_memo__icontains=search)
            )
        
        # Apply status filter
        status_filter = self.request.query_params.get("status")
        if status_filter:
            if status_filter.lower() == 'paid':
                queryset = queryset.filter(balance=0)
            elif status_filter.lower() == 'unpaid':
                queryset = queryset.filter(balance__gt=0)
            elif status_filter.lower() == 'overdue':
                from datetime import date
                queryset = queryset.filter(balance__gt=0, due_date__lt=date.today())
        
        # Apply customer quality filter
        customer_quality = self.request.query_params.get("customer_quality")
        if customer_quality:
            if customer_quality == 'missing':
                queryset = queryset.filter(customer__isnull=True)
            elif customer_quality == 'stub':
                queryset = queryset.filter(customer__is_stub=True)
            elif customer_quality == 'complete':
                queryset = queryset.filter(customer__isnull=False, customer__is_stub=False)
        
        return queryset

    def get_serializer_class(self):
        return InvoiceSerializer
    
    from rest_framework.pagination import PageNumberPagination

    class InvoicePagination(PageNumberPagination):
        page_size = 20
        page_size_query_param = 'page_size'
    max_page_size = 100

    def list(self, request, *args, **kwargs):
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            # Get the base queryset with all filters applied
            queryset = self.get_queryset()
            
            # Calculate statistics from the filtered queryset
            total_invoices = queryset.count()
            paid_invoices = queryset.filter(balance=0).count()
            unpaid_invoices = queryset.filter(balance__gt=0).count()
            invoices_with_customers = queryset.filter(customer__isnull=False).count()
            invoices_with_stub_customers = queryset.filter(customer__is_stub=True).count()
            
            # Use DRF pagination
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                
                # Create the paginated response
                response_data = self.get_paginated_response(serializer.data)
                
                # Add custom data to the response
                response_data.data.update({
                    "success": True,
                    "company_info": {
                        "name": active_company.name,
                        "qb_company_name": active_company.qb_company_name,
                        "currency_code": active_company.currency_code,
                        "realm_id": active_company.realm_id,
                    },
                    "stats": {
                        "total_invoices": total_invoices,
                        "paid_invoices": paid_invoices,
                        "unpaid_invoices": unpaid_invoices,
                        "invoices_with_customers": invoices_with_customers,
                        "invoices_with_stub_customers": invoices_with_stub_customers,
                        "invoices_without_customers": total_invoices - invoices_with_customers,
                        "customer_link_quality": round((invoices_with_customers / total_invoices * 100), 2) if total_invoices > 0 else 0
                    }
                })
                return response_data

            # Fallback if no pagination (shouldn't normally happen with pagination_class set)
            serializer = self.get_serializer(queryset, many=True)
            return Response({
                "success": True,
                "invoices": serializer.data,
                "company_info": {
                    "name": active_company.name,
                    "qb_company_name": active_company.qb_company_name,
                    "currency_code": active_company.currency_code,
                    "realm_id": active_company.realm_id,
                },
                "stats": {
                    "total_invoices": total_invoices,
                    "paid_invoices": paid_invoices,
                    "unpaid_invoices": unpaid_invoices,
                    "invoices_with_customers": invoices_with_customers,
                    "invoices_with_stub_customers": invoices_with_stub_customers,
                    "invoices_without_customers": total_invoices - invoices_with_customers,
                    "customer_link_quality": round((invoices_with_customers / total_invoices * 100), 2) if total_invoices > 0 else 0
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=["post"], url_path="sync-from-quickbooks", url_name="sync-invoices")
    def sync_from_quickbooks(self, request):
        """Sync invoices from QuickBooks (legacy method)"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            service = QuickBooksInvoiceService(active_company)
            success_count, failed_count = service.sync_all_invoices()
            
            return Response({
                "success": True,
                "message": f"Synced {success_count} invoices successfully. {failed_count} failed.",
                "synced_count": success_count,
                "failed_count": failed_count
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="smart-sync", url_name="smart-sync-invoices")
    def smart_sync_invoices(self, request):
        """Smart sync invoices with automatic customer resolution"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            service = QuickBooksInvoiceService(active_company)
            success_count, failed_count, stub_customers_created = service.sync_all_invoices()
            
            return Response({
                "success": True,
                "message": f"Smart sync completed: {success_count} invoices processed, {stub_customers_created} stub customers created.",
                "synced_count": success_count,
                "failed_count": failed_count,
                "stub_customers_created": stub_customers_created
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=False, methods=["get"], url_path="analyze-customer-links", url_name="analyze-customer-links")
    def analyze_customer_links(self, request):
        """Analyze customer link quality for invoices"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            # Get all invoices for the company
            invoices = Invoice.objects.filter(company=active_company)
            total_invoices = invoices.count()
            
            # Calculate customer link statistics
            invoices_with_customers = invoices.filter(customer__isnull=False).count()
            invoices_with_stub_customers = invoices.filter(customer__is_stub=True).count()
            
            # Get stub customers count
            from customers.models import Customer
            stub_customers = Customer.objects.filter(company=active_company, is_stub=True).count()
            
            quality_score = (invoices_with_customers / total_invoices * 100) if total_invoices > 0 else 0

            return Response({
                "success": True,
                "analysis": {
                    "total_invoices": total_invoices,
                    "invoices_with_customers": invoices_with_customers,
                    "invoices_without_customers": total_invoices - invoices_with_customers,
                    "stub_customers": stub_customers,
                    "invoices_with_stub_customers": invoices_with_stub_customers,
                    "quality_score": quality_score
                }
            })
            
        except Exception as e:
            print(f"Error in analyze_customer_links: {str(e)}")
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

    @action(detail=False, methods=["post"], url_path="enhance-stub-customers", url_name="enhance-stub-customers")
    def enhance_stub_customers(self, request):
        """Enhance stub customers with real QuickBooks data"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            from customers.services import QuickBooksCustomerService
            customer_service = QuickBooksCustomerService(active_company)
            enhanced_count, failed_count = customer_service.enhance_stub_customers()
            
            return Response({
                "success": True,
                "message": f"Enhanced {enhanced_count} stub customers. {failed_count} failed.",
                "enhanced_count": enhanced_count,
                "failed_count": failed_count
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["get"], url_path="lines", url_name="invoice-lines")
    def invoice_lines(self, request, pk=None):
        """Get line items for a specific invoice"""
        try:
            invoice = self.get_object()
            lines = invoice.line_items.all().order_by('line_num')
            
            serializer = InvoiceLineSerializer(lines, many=True)
            
            return Response({
                "success": True,
                "lines": serializer.data,
                "invoice": {
                    "id": invoice.id,
                    "doc_number": invoice.doc_number,
                    "customer_name": invoice.customer_name
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        """Delete is not allowed via API - invoices come from QuickBooks"""
        return Response({
            'success': False,
            'error': 'Invoices can only be deleted via QuickBooks sync'
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)
   
   
from kra.services import KRAInvoiceService
from kra.models import KRACompanyConfig, KRAInvoiceSubmission
from rest_framework.decorators import api_view, permission_classes
from django.shortcuts import get_object_or_404

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_invoice_to_kra(request, invoice_id):
    """
    Validate and submit invoice to KRA
    """
    try:
        invoice = get_object_or_404(Invoice, id=invoice_id)
        company = invoice.company
        
        # Verify user has access to this company
        if not request.user.company_memberships.filter(company=company).exists():
            return Response(
                {'error': 'You do not have access to this company'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if KRA config exists
        if not hasattr(company, 'kra_config'):
            return Response(
                {'error': 'KRA configuration not found for this company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Submit to KRA
        kra_service = KRAInvoiceService(company.id)
        result = kra_service.submit_to_kra(invoice_id)
        
        if result['success']:
            submission = result['submission']
            response_data = {
                'success': True,
                'message': 'Invoice successfully submitted to KRA',
                'submission_id': str(submission.id),
                'kra_invoice_number': submission.kra_invoice_number,
                'receipt_signature': submission.receipt_signature,
                'qr_code_data': submission.qr_code_data,
                'kra_response': result.get('kra_response', {})
            }
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response(
                {'success': False, 'error': result['error']},
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except Exception as e:
        return Response(
            {'success': False, 'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    

# views.py
import os
import qrcode
from io import BytesIO
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from weasyprint import HTML
import tempfile
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from django.http import HttpResponse
import base64

# views.py
@csrf_exempt
def generate_invoice_pdf(request, invoice_id):
    """Generate PDF version of invoice"""
    try:
        invoice = Invoice.objects.get(id=invoice_id)
        company = invoice.company
        
        # FIX: Get the most recent SUCCESSFUL KRA submission
        kra_submission = invoice.kra_submissions.filter(
            status__in=['success', 'signed', 'completed']  # Include all possible success statuses
        ).order_by('-created_at').first()  # Get the NEWEST submission
        
        print(f"Invoice: {invoice.id}")
        print(f"KRA Submission found: {kra_submission is not None}")
        if kra_submission:
            print(f"KRA Submission ID: {kra_submission.id}")
            print(f"KRA Status: {kra_submission.status}")
            print(f"QR Code Data: {kra_submission.qr_code_data}")
            print(f"QR Code Data Length: {len(kra_submission.qr_code_data) if kra_submission.qr_code_data else 0}")
        else:
            print("No successful KRA submission found")
            # Debug: Check all submissions
            all_submissions = invoice.kra_submissions.all()
            print(f"Total KRA submissions: {all_submissions.count()}")
            for sub in all_submissions:
                print(f"  - ID: {sub.id}, Status: {sub.status}, QR Data: {bool(sub.qr_code_data)}")
        
        context = {
            'invoice': invoice,
            'company': company,
            'kra_submission': kra_submission,
        }
        
        # Generate QR code if KRA data exists
        if kra_submission and kra_submission.qr_code_data:
            try:
                # Validate QR code data
                if not kra_submission.qr_code_data.strip():
                    print("QR code data is empty")
                elif len(kra_submission.qr_code_data) > 1000:
                    print(f"QR code data might be too long: {len(kra_submission.qr_code_data)} chars")
                
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(kra_submission.qr_code_data)
                qr.make(fit=True)
                
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                qr_img.save(buffer, format='PNG')
                buffer.seek(0)
                
                qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
                context['qr_code'] = qr_code_base64
                print(f"QR code generated successfully, base64 length: {len(qr_code_base64)}")
                
            except Exception as e:
                print(f"Error generating QR code: {e}")
                import traceback
                traceback.print_exc()
        
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

        # Get KRA submission - use the related_name 'kra_submissions'
        kra_submission = invoice.kra_submissions.first()  # Get the most recent submission

        context = {
            'invoice': invoice,
            'company': company,
            'kra_submission': kra_submission,
        }

        # Generate QR code for HTML view too
        if kra_submission and kra_submission.qr_code_data:
            try:
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(kra_submission.qr_code_data)
                qr.make(fit=True)
                
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                qr_img.save(buffer, format='PNG')
                buffer.seek(0)
                
                qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
                context['qr_code'] = qr_code_base64
                
            except Exception as e:
                print(f"Error generating QR code: {e}")

        return render(request, 'invoices/invoice_template.html', context)

    except Invoice.DoesNotExist:
        return HttpResponse("Invoice not found", status=404)
    
