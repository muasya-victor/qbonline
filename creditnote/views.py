from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db.models import Q

from .models import CreditNote
from .serializers import CreditNoteSerializer
from invoices.services import QuickBooksCreditNoteService
from users.models import Company, ActiveCompany


class CreditNoteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint to view credit notes synced from QuickBooks.
    """
    serializer_class = CreditNoteSerializer
    permission_classes = [IsAuthenticated]

    def get_active_company(self):
        """Get the user's active company"""
        try:
            active_company = ActiveCompany.objects.get(user=self.request.user)
            return active_company.company
        except ActiveCompany.DoesNotExist:
            # Fallback to user's default company if no active company
            try:
                membership = self.request.user.company_memberships.filter(is_default=True).first()
                return membership.company if membership else None
            except:
                return None

    def get_queryset(self):
        """Filter credit notes by the user's active company"""
        active_company = self.get_active_company()
        if not active_company:
            return CreditNote.objects.none()
        
        # Use created_at instead of created
        queryset = CreditNote.objects.filter(company=active_company).select_related(
            "related_invoice", "company"
        ).prefetch_related("line_items").order_by('-txn_date', '-created_at')
        
        # Apply search filter
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(doc_number__icontains=search) |
                Q(customer_name__icontains=search) |
                Q(qb_credit_id__icontains=search)
            )
        
        # Apply status filter
        status_filter = self.request.query_params.get("status")
        if status_filter:
            if status_filter.lower() == 'applied':
                queryset = queryset.filter(balance=0)
            elif status_filter.lower() == 'pending':
                queryset = queryset.filter(balance__gt=0)
            elif status_filter.lower() == 'void':
                queryset = queryset.filter(balance__lt=0)
        
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            queryset = self.get_queryset()
            
            # Pagination
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 20))
            
            paginator = Paginator(queryset, page_size)
            page_obj = paginator.get_page(page)
            
            serializer = self.get_serializer(page_obj, many=True)
            
            return Response({
                "success": True,
                "credit_notes": serializer.data,
                "pagination": {
                    "count": paginator.count,
                    "next": page_obj.next_page_number() if page_obj.has_next() else None,
                    "previous": page_obj.previous_page_number() if page_obj.has_previous() else None,
                    "page_size": page_size,
                    "current_page": page,
                    "total_pages": paginator.num_pages
                },
                "company_info": {
                    "name": active_company.name,
                    "qb_company_name": active_company.qb_company_name,
                    "qb_legal_name": active_company.qb_legal_name,
                    "currency_code": active_company.currency_code,
                    "realm_id": active_company.realm_id,
                    "logo_url": active_company.logo_url,
                    "brand_color": active_company.brand_color,
                    "invoice_footer_text": active_company.invoice_footer_text
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="sync", url_name="sync-credit-notes")
    def sync_from_qbo(self, request):
        """Trigger credit note sync from QuickBooks API"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            service = QuickBooksCreditNoteService(active_company)
            synced_count = service.sync_all_credit_notes()
            
            return Response({
                "success": True,
                "message": f"✅ Synced {synced_count} credit notes successfully.",
                "synced_count": synced_count
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)