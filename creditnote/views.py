# creditnote/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch, Sum, Count, Avg
from .models import CreditNote, CreditNoteLine
from .serializers import (
    CreditNoteSerializer, 
    CreditNoteLineSerializer, 
    CompanyInfoSerializer,
    KRACreditNoteSubmissionSerializer  # Add this import
)
from invoices.services import QuickBooksCreditNoteService
from companies.models import ActiveCompany
from kra.models import KRACreditNoteSubmission
import requests



def get_active_company(user):
    """Helper function to get active company with error handling"""
    try:
        active_company = ActiveCompany.objects.get(user=user)
        return active_company.company
    except ActiveCompany.DoesNotExist:
        return None


class CreditNoteViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for managing credit notes with pagination and KRA validation support"""

    serializer_class = CreditNoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter credit notes by user's active company with optimized prefetching"""
        active_company = get_active_company(self.request.user)
        if not active_company:
            return CreditNote.objects.none()

        # Prefetch KRA submissions to avoid N+1 queries
        kra_submissions_prefetch = Prefetch(
            'kra_submissions',
            queryset=KRACreditNoteSubmission.objects.select_related('company').order_by('-created_at')
        )

        queryset = CreditNote.objects.filter(
            company=active_company
        ).select_related('company', 'related_invoice').prefetch_related(
            'line_items',
            kra_submissions_prefetch
        ).order_by('-txn_date')

        # Apply search filter
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(doc_number__icontains=search) |
                Q(customer_name__icontains=search) |
                Q(qb_credit_id__icontains=search)
            )

        # Apply status filter
        status_filter = self.request.query_params.get('status')
        if status_filter == 'applied':
            queryset = queryset.filter(balance=0)
        elif status_filter == 'pending':
            queryset = queryset.filter(balance__gt=0)
        elif status_filter == 'void':
            queryset = queryset.filter(balance__lt=0)

        # Apply KRA validation filter
        kra_validated = self.request.query_params.get('kra_validated')
        if kra_validated is not None:
            if kra_validated.lower() == 'true':
                # Credit notes with at least one successful KRA submission
                queryset = queryset.filter(
                    kra_submissions__status__in=['success', 'signed']
                ).distinct()
            elif kra_validated.lower() == 'false':
                # Credit notes without successful KRA submissions
                queryset = queryset.exclude(
                    kra_submissions__status__in=['success', 'signed']
                )

        return queryset

    def list(self, request, *args, **kwargs):
        """List credit notes with pagination and company info"""
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

        # Serialize the credit notes
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
            'total_submissions': KRACreditNoteSubmission.objects.filter(company=active_company).count(),
            'successful_submissions': KRACreditNoteSubmission.objects.filter(
                company=active_company, 
                status__in=['success', 'signed']
            ).count(),
            'failed_submissions': KRACreditNoteSubmission.objects.filter(
                company=active_company, 
                status='failed'
            ).count(),
            'pending_submissions': KRACreditNoteSubmission.objects.filter(
                company=active_company, 
                status__in=['pending', 'submitted']
            ).count()
        }

        return Response({
            'success': True,
            'credit_notes': serializer.data,
            'pagination': pagination_info,
            'company_info': company_serializer.data,
            'kra_stats': kra_stats
        })

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a single credit note with detailed information"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        
        # Get additional KRA submission details
        kra_submissions = instance.kra_submissions.all().order_by('-created_at')
        kra_serializer = KRACreditNoteSubmissionSerializer(kra_submissions, many=True)
        
        response_data = serializer.data
        response_data['kra_submissions_detail'] = kra_serializer.data
        response_data['kra_submissions_count'] = kra_submissions.count()
        
        return Response({
            'success': True,
            'credit_note': response_data
        })

    @action(detail=True, methods=['get'])
    def kra_submissions(self, request, pk=None):
        """Get all KRA submissions for a specific credit note"""
        credit_note = self.get_object()
        submissions = credit_note.kra_submissions.all().order_by('-created_at')
        
        serializer = KRACreditNoteSubmissionSerializer(submissions, many=True)
        
        return Response({
            'success': True,
            'credit_note_id': str(credit_note.id),
            'credit_note_number': credit_note.doc_number,
            'customer_name': credit_note.customer_name,
            'kra_submissions': serializer.data,
            'total_submissions': submissions.count(),
            'latest_submission': KRACreditNoteSubmissionSerializer(submissions.first()).data if submissions.exists() else None
        })

    @action(detail=True, methods=['post'])
    def submit_to_kra(self, request, pk=None):
        """Submit a specific credit note to KRA"""
        try:
            credit_note = self.get_object()  # This gets the credit note using the pk parameter
            company = credit_note.company
            
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
                }, status=status.HTTP_400_BAD_REQUEST)
            
            from kra.services import KRACreditNoteService
            
            # Initialize KRA service with company
            kra_service = KRACreditNoteService(company.id)
            
            # Submit to KRA - actual implementation (use credit_note.id, not credit_note_id)
            result = kra_service.submit_to_kra(credit_note.id)
            
            if result['success']:
                submission = result['submission']
                response_data = {
                    'success': True,
                    'message': 'Credit note successfully submitted to KRA',
                    'submission_id': str(submission.id),
                    'kra_credit_note_number': submission.kra_credit_note_number,
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
        """Get all line items for a specific credit note"""
        credit_note = self.get_object()
        line_items = credit_note.line_items.all().order_by('line_num')
        
        serializer = CreditNoteLineSerializer(line_items, many=True)
        
        return Response({
            'success': True,
            'credit_note_id': str(credit_note.id),
            'credit_note_number': credit_note.doc_number,
            'line_items': serializer.data,
            'total_line_items': line_items.count(),
            'total_amount': float(credit_note.total_amt),
            'subtotal': float(credit_note.subtotal),
            'tax_total': float(credit_note.tax_total)
        })

    @action(detail=False, methods=['post'])
    def sync_from_quickbooks(self, request):
        """Sync credit notes from QuickBooks API"""
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
            service = QuickBooksCreditNoteService(active_company)
            synced_count = service.sync_all_credit_notes()

            company_serializer = CompanyInfoSerializer(active_company)

            return Response({
                'success': True,
                'message': f'Successfully synced credit notes for {active_company.name}',
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
        """Get credit note statistics for the active company"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Credit note statistics
        total_credit_notes = CreditNote.objects.filter(company=active_company).count()
        applied_credit_notes = CreditNote.objects.filter(company=active_company, balance=0).count()
        pending_credit_notes = CreditNote.objects.filter(company=active_company, balance__gt=0).count()
        void_credit_notes = CreditNote.objects.filter(company=active_company, balance__lt=0).count()
        
        # Amount statistics
        total_amount = CreditNote.objects.filter(company=active_company).aggregate(
            total=Sum('total_amt')
        )['total'] or 0
        outstanding_balance = CreditNote.objects.filter(company=active_company).aggregate(
            total=Sum('balance')
        )['total'] or 0
        
        # KRA statistics
        kra_validated_credit_notes = CreditNote.objects.filter(
            company=active_company,
            kra_submissions__status__in=['success', 'signed']
        ).distinct().count()

        return Response({
            'success': True,
            'stats': {
                'total_credit_notes': total_credit_notes,
                'applied_credit_notes': applied_credit_notes,
                'pending_credit_notes': pending_credit_notes,
                'void_credit_notes': void_credit_notes,
                'total_amount': float(total_amount),
                'outstanding_balance': float(outstanding_balance),
                'kra_validated_credit_notes': kra_validated_credit_notes,
                'validation_rate': round((kra_validated_credit_notes / total_credit_notes * 100), 2) if total_credit_notes > 0 else 0
            },
            'company': active_company.name
        })

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent credit notes (last 10)"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected'
            }, status=status.HTTP_400_BAD_REQUEST)

        recent_credit_notes = CreditNote.objects.filter(
            company=active_company
        ).select_related('company', 'related_invoice').prefetch_related(
            Prefetch('kra_submissions', queryset=KRACreditNoteSubmission.objects.order_by('-created_at'))
        ).order_by('-created_at')[:10]

        serializer = self.get_serializer(recent_credit_notes, many=True)

        return Response({
            'success': True,
            'credit_notes': serializer.data,
            'total_count': recent_credit_notes.count()
        })

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get summary information for credit notes"""
        active_company = get_active_company(request.user)
        if not active_company:
            return Response({
                'success': False,
                'error': 'No active company selected'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get date range from query params
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        queryset = CreditNote.objects.filter(company=active_company)
        
        if from_date:
            queryset = queryset.filter(txn_date__gte=from_date)
        if to_date:
            queryset = queryset.filter(txn_date__lte=to_date)

        summary = queryset.aggregate(
            total_count=Count('id'),
            total_amount=Sum('total_amt'),
            total_tax=Sum('tax_total'),
            total_subtotal=Sum('subtotal'),
            average_amount=Avg('total_amt')
        )

        # Top customers by credit note count
        top_customers = queryset.values('customer_name').annotate(
            count=Count('id'),
            total_amount=Sum('total_amt')
        ).order_by('-count')[:5]

        return Response({
            'success': True,
            'summary': summary,
            'top_customers': list(top_customers),
            'date_range': {
                'from_date': from_date,
                'to_date': to_date
            }
        })