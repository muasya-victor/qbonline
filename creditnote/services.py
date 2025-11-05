# kra/services.py - Add these methods to existing KRAService
import logging
from typing import Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from .models import KRAInvoiceCounter, KRAInvoiceSubmission, KRACompanyConfig
from invoices.models import Invoice
from creditnote.models import CreditNote
from companies.models import Company

logger = logging.getLogger(__name__)

class KRAService:
    """Service for handling KRA submissions for both invoices and credit notes"""
    
    def __init__(self, company_id: str):
        self.company = Company.objects.get(id=company_id)
        self.config = getattr(self.company, 'kra_config', None)
    
    def _get_next_kra_invoice_number(self) -> int:
        """Get next sequential KRA invoice number - used for both invoices and credit notes"""
        with transaction.atomic():
            counter, created = KRAInvoiceCounter.objects.select_for_update().get_or_create(
                company=self.company,
                defaults={'last_invoice_number': 1}
            )
            
            next_number = counter.last_invoice_number + 1
            counter.last_invoice_number = next_number
            counter.save()
            
            return next_number
    
    def submit_credit_note_to_kra(self, credit_note_id: str) -> Dict[str, Any]:
        """Submit credit note to KRA using the same invoice counter"""
        try:
            credit_note = CreditNote.objects.select_related('company').get(
                id=credit_note_id, 
                company=self.company
            )
            
            # Check if already submitted
            existing_submission = credit_note.kra_submissions.filter(
                status__in=['success', 'signed', 'submitted']
            ).first()
            
            if existing_submission:
                return {
                    'success': False,
                    'error': f'Credit note already has a KRA submission: {existing_submission.kra_invoice_number}'
                }
            
            # Get next KRA number from invoice counter
            kra_invoice_number = self._get_next_kra_invoice_number()
            
            # Prepare credit note data for KRA
            kra_data = self._prepare_credit_note_data(credit_note, kra_invoice_number)
            
            # Create submission record
            submission = KRAInvoiceSubmission.objects.create(
                company=self.company,
                credit_note=credit_note,
                kra_invoice_number=kra_invoice_number,
                trd_invoice_no=credit_note.doc_number or f"CN-{credit_note.qb_credit_id}",
                document_type='credit_note',
                submitted_data=kra_data,
                status='submitted'
            )
            
            # Submit to KRA API (mock for now - integrate with actual KRA API)
            kra_response = self._submit_to_kra_api(kra_data, 'credit_note')
            
            if kra_response.get('success'):
                submission.mark_success(
                    response_data=kra_response,
                    receipt_signature=kra_response.get('receipt_signature'),
                    qr_code_data=kra_response.get('qr_code_data')
                )
                
                logger.info(f"✅ Credit note {credit_note.doc_number} submitted to KRA successfully. KRA Number: {kra_invoice_number}")
                
                return {
                    'success': True,
                    'submission': submission,
                    'kra_response': kra_response,
                    'kra_invoice_number': kra_invoice_number
                }
            else:
                submission.mark_failed(
                    error_message=kra_response.get('error', 'KRA submission failed'),
                    response_data=kra_response
                )
                
                return {
                    'success': False,
                    'error': kra_response.get('error', 'KRA submission failed'),
                    'submission': submission
                }
                
        except CreditNote.DoesNotExist:
            error_msg = f"Credit note {credit_note_id} not found for company {self.company.name}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}
        except Exception as e:
            error_msg = f"Failed to submit credit note to KRA: {str(e)}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}
    
    def _prepare_credit_note_data(self, credit_note: CreditNote, kra_invoice_number: int) -> Dict[str, Any]:
        """Prepare credit note data for KRA submission"""
        line_items = []
        
        for line in credit_note.line_items.all():
            line_items.append({
                "item_name": line.item_name or "Credit Item",
                "description": line.description or "",
                "quantity": float(line.qty),
                "unit_price": float(line.unit_price),
                "amount": float(line.amount),
                "tax_amount": float(line.tax_amount),
                "tax_rate": float(line.tax_percent)
            })
        
        return {
            "document_type": "credit_note",
            "kra_invoice_number": kra_invoice_number,
            "trd_document_no": credit_note.doc_number or f"CN-{credit_note.qb_credit_id}",
            "document_date": credit_note.txn_date.isoformat(),
            "company_info": {
                "tin": self.config.tin if self.config else "",
                "trade_name": self.config.trade_name if self.config else self.company.name,
                "bhf_id": self.config.bhf_id if self.config else "00"
            },
            "customer_info": {
                "name": credit_note.customer_name or "Customer",
                "pin": ""  # Would come from customer model if available
            },
            "amounts": {
                "subtotal": float(credit_note.subtotal),
                "tax_total": float(credit_note.tax_total),
                "total_amount": float(credit_note.total_amt)
            },
            "line_items": line_items,
            "tax_info": {
                "tax_rate_ref": credit_note.tax_rate_ref,
                "tax_percent": float(credit_note.tax_percent)
            },
            "metadata": {
                "credit_note_id": str(credit_note.id),
                "qb_credit_id": credit_note.qb_credit_id,
                "company_id": str(self.company.id)
            }
        }
    
    def _submit_to_kra_api(self, data: Dict[str, Any], document_type: str) -> Dict[str, Any]:
        """Submit data to KRA API - mock implementation"""
        # TODO: Integrate with actual KRA eTIMS API
        try:
            # Mock successful response
            if document_type == 'credit_note':
                # Simulate KRA API call for credit note
                logger.info(f"📤 Submitting {document_type} to KRA: {data['kra_invoice_number']}")
                
                # Mock response - replace with actual KRA API integration
                return {
                    'success': True,
                    'receipt_signature': f"KRA-RCPT-{data['kra_invoice_number']}",
                    'qr_code_data': f"KRA_CREDIT_NOTE|{data['kra_invoice_number']}|{data['trd_document_no']}",
                    'timestamp': timezone.now().isoformat(),
                    'kra_reference': f"KRA-REF-{data['kra_invoice_number']}"
                }
            else:
                # Similar logic for invoices
                return {
                    'success': True,
                    'receipt_signature': f"KRA-RCPT-{data['kra_invoice_number']}",
                    'qr_code_data': f"KRA_INVOICE|{data['kra_invoice_number']}|{data['trd_document_no']}",
                    'timestamp': timezone.now().isoformat()
                }
                
        except Exception as e:
            logger.error(f"KRA API submission failed: {str(e)}")
            return {
                'success': False,
                'error': f"KRA API error: {str(e)}"
            }
    
    def get_credit_note_submissions(self, credit_note_id: str) -> Dict[str, Any]:
        """Get all KRA submissions for a credit note"""
        try:
            credit_note = CreditNote.objects.get(id=credit_note_id, company=self.company)
            submissions = credit_note.kra_submissions.all().order_by('-created_at')
            
            from kra.serializers import KRASubmissionSerializer
            serializer = KRASubmissionSerializer(submissions, many=True)
            
            return {
                'success': True,
                'submissions': serializer.data,
                'total_count': submissions.count()
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }