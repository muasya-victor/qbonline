"""
Invoice Filtering Service
Filters and annotates invoices for credit note linking.
"""

from django.db.models import Q, F, Value, DecimalField, Case, When, Subquery, OuterRef
from django.db.models.functions import Coalesce
from django.db import models
from decimal import Decimal

from invoices.models import Invoice
from creditnote.models import CreditNote
from creditnote.custom_services.credit_validation_service import CreditNoteValidationService


class InvoiceFilterService:
    """
    Service for filtering and annotating invoices for credit note linking.
    """
    
    @staticmethod
    def get_invoices_available_for_credit(
        company,
        search: str = None,
        customer_name: str = None,
        min_available_balance: Decimal = None,
        exclude_fully_credited: bool = True,
        page: int = 1,  # Add page parameter
        page_size: int = 20  # Add page_size parameter
    ):
        """
        Get invoices that are available for credit note linking WITH PAGINATION SUPPORT.
        
        Args:
            company: Company instance
            search: Search term for invoice number or ID
            customer_name: Filter by customer name
            min_available_balance: Minimum available balance required
            exclude_fully_credited: Whether to exclude fully credited invoices
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            dict: Paginated results with annotations preserved
        """
        # Start with base queryset
        queryset = Invoice.objects.filter(company=company)
        
        # Apply search filter
        if search:
            queryset = queryset.filter(
                Q(doc_number__icontains=search) |
                Q(qb_invoice_id__icontains=search) |
                Q(id__icontains=search)
            )
        
        # Filter by customer name
        if customer_name:
            queryset = queryset.filter(
                Q(customer_name__icontains=customer_name) |
                Q(customer__display_name__icontains=customer_name) |
                Q(customer__company_name__icontains=customer_name)
            )
        
        # Annotate with credit information using subquery for better performance
        # Calculate total credits applied to each invoice
        credit_sum_subquery = CreditNote.objects.filter(
            related_invoice=OuterRef('pk')
        ).values('related_invoice').annotate(
            total_credits=models.Sum('total_amt')
        ).values('total_credits')
        
        queryset = queryset.annotate(
            total_credits_applied=Coalesce(
                Subquery(credit_sum_subquery),
                Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
            ),
            available_balance=Case(
                When(
                    total_amt__isnull=False,
                    then=F('total_amt') - Coalesce(
                        Subquery(credit_sum_subquery),
                        Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
                    )
                ),
                default=Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
            )
        )
        
        # Apply available balance filter
        if min_available_balance is not None:
            queryset = queryset.filter(available_balance__gte=min_available_balance)
        
        # Exclude fully credited invoices (with tolerance)
        if exclude_fully_credited:
            queryset = queryset.filter(available_balance__gt=Decimal('0.01'))
        
        # Order by available balance (descending) and date
        queryset = queryset.order_by('-available_balance', '-txn_date')
        
        # Manually handle pagination to preserve annotations
        total_count = queryset.count()
        
        # Calculate pagination
        start_index = (page - 1) * page_size
        end_index = page * page_size
        
        # Get the paginated subset
        invoices = list(queryset[start_index:end_index])
        
        # Calculate pagination metadata
        has_next = end_index < total_count
        has_previous = page > 1
        
        return {
            'results': invoices,
            'total_count': total_count,
            'page': page,
            'page_size': page_size,
            'has_next': has_next,
            'has_previous': has_previous,
            'total_pages': (total_count + page_size - 1) // page_size
        }
    
    @staticmethod
    def get_invoice_with_credit_details(invoice_id: str):
        """
        Get a single invoice with detailed credit information.
        
        Args:
            invoice_id: ID of the invoice
            
        Returns:
            dict: Invoice data with credit details
        """
        try:
            invoice = Invoice.objects.get(id=invoice_id)
            
            # Use the validation service to calculate credit summary
            summary = CreditNoteValidationService.calculate_invoice_credit_summary(invoice)
            
            return {
                'id': invoice.id,
                'doc_number': invoice.doc_number,
                'qb_invoice_id': invoice.qb_invoice_id,
                'txn_date': invoice.txn_date,
                'total_amt': invoice.total_amt,
                'customer_name': invoice.customer_name,
                'customer_display': invoice.customer.display_name if invoice.customer else invoice.customer_name,
                **summary,  # Include all summary fields
            }
            
        except Invoice.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error getting invoice credit details: {str(e)}")
            return None
    
    @staticmethod
    def get_fully_credited_invoices(company, limit: int = None):
        """
        Get invoices that are fully credited.
        
        Args:
            company: Company instance
            limit: Maximum number of results
            
        Returns:
            QuerySet: Fully credited invoices
        """
        # Get all invoices with credit annotation
        invoices = Invoice.objects.filter(company=company).annotate(
            calculated_total_credits=Coalesce(
                models.Sum('credit_notes__total_amt'),
                Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
            ),
            available_balance=Case(
                When(
                    total_amt__isnull=False,
                    then=F('total_amt') - Coalesce(
                        models.Sum('credit_notes__total_amt'),
                        Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
                    )
                ),
                default=Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))
            )
        )
        
        # Filter for fully credited
        fully_credited = invoices.filter(
            available_balance__lte=Decimal('0.01')
        ).order_by('-txn_date')
        
        if limit:
            fully_credited = fully_credited[:limit]
        
        return fully_credited
    
    @staticmethod
    def get_invoices_summary(company):
        """
        Get summary statistics for invoices and credits.
        
        Args:
            company: Company instance
            
        Returns:
            Dict: Summary statistics
        """
        # Get all invoices for the company
        invoices = Invoice.objects.filter(company=company)
        total_invoices = invoices.count()
        
        # Get invoices with credits
        invoices_with_credits = invoices.filter(credit_notes__isnull=False).distinct().count()
        
        # Get total invoice amount
        total_invoice_amount = invoices.aggregate(
            total=models.Sum('total_amt')
        )['total'] or Decimal('0.00')
        
        # Get total credits applied
        total_credits = CreditNote.objects.filter(
            related_invoice__company=company
        ).aggregate(
            total=models.Sum('total_amt')
        )['total'] or Decimal('0.00')
        
        # Get fully credited invoices count
        fully_credited_count = InvoiceFilterService.get_fully_credited_invoices(company).count()
        
        return {
            'total_invoices': total_invoices,
            'invoices_with_credits': invoices_with_credits,
            'invoices_without_credits': total_invoices - invoices_with_credits,
            'fully_credited_invoices': fully_credited_count,
            'total_invoice_amount': float(total_invoice_amount),
            'total_credits_applied': float(total_credits),
            'credit_utilization_percentage': float(
                (total_credits / total_invoice_amount * 100) if total_invoice_amount > 0 else 0
            ),
        }