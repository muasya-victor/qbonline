from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from companies.models import Company
from invoices.models import Invoice
from .services import KRAInvoiceService
from .models import KRACompanyConfig, KRAInvoiceSubmission

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

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_submission_status(request, submission_id):
    """
    Get status of KRA submission
    """
    submission = get_object_or_404(KRAInvoiceSubmission, id=submission_id)
    company = submission.company
    
    # Verify user has access to this company
    if not request.user.company_memberships.filter(company=company).exists():
        return Response(
            {'error': 'You do not have access to this company'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    response_data = {
        'submission_id': str(submission.id),
        'invoice_id': str(submission.invoice.id),
        'kra_invoice_number': submission.kra_invoice_number,
        'status': submission.status,
        'submitted_at': submission.created_at,
        'error_message': submission.error_message,
        'receipt_signature': submission.receipt_signature,
        'qr_code_data': submission.qr_code_data,
        'kra_response': submission.response_data
    }
    
    return Response(response_data, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_company_kra_submissions(request, company_id):
    """
    Get all KRA submissions for a company
    """
    company = get_object_or_404(Company, id=company_id)
    
    # Verify user has access to this company
    if not request.user.company_memberships.filter(company=company).exists():
        return Response(
            {'error': 'You do not have access to this company'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    submissions = KRAInvoiceSubmission.objects.filter(company=company).order_by('-created_at')
    
    submission_data = []
    for submission in submissions:
        # Get document details based on document type
        if submission.document_type == 'invoice' and submission.invoice:
            doc_number = submission.invoice.doc_number
            customer_name = submission.invoice.customer_name
            total_amount = float(submission.invoice.total_amt)
        elif submission.document_type == 'credit_note' and submission.credit_note:
            doc_number = submission.credit_note.doc_number
            customer_name = submission.credit_note.customer_name
            total_amount = float(submission.credit_note.total_amt)
        else:
            # Fallback for invalid records
            doc_number = "N/A"
            customer_name = "Unknown"
            total_amount = 0.0
        
        submission_data.append({
            'id': str(submission.id),
            'invoice_number': doc_number,
            'kra_invoice_number': submission.kra_invoice_number,
            'customer_name': customer_name,
            'total_amount': total_amount,
            'status': submission.status,
            'document_type': submission.document_type,
            'submitted_at': submission.created_at,
            'error_message': submission.error_message,
            'trd_invoice_no': submission.trd_invoice_no
        })
    
    return Response({
        'company': company.name,
        'submissions': submission_data
    }, status=status.HTTP_200_OK)

