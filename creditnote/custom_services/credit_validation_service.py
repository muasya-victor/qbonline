"""
Credit Note Validation Service
Handles validation logic for linking credit notes to invoices.
"""

from decimal import Decimal
from typing import Tuple, Dict, Any, Optional
from django.db import transaction
from django.db import models
from django.core.exceptions import ValidationError

# Import models
from invoices.models import Invoice
from creditnote.models import CreditNote
from companies.models import Company


class CreditNoteValidationError(Exception):
    """Custom exception for credit note validation errors"""
    pass


class CreditNoteValidationService:
    """
    Service for validating credit note linkages to invoices.
    This service is self-contained and doesn't affect existing code.
    """
    
    @staticmethod
    def calculate_invoice_credit_summary(invoice: Invoice) -> Dict[str, Any]:
        """
        Calculate credit summary for an invoice.
        
        Args:
            invoice: Invoice instance
            
        Returns:
            Dict: Credit summary information
        """
        # Calculate total credits applied
        total_credits = CreditNote.objects.filter(
            related_invoice=invoice
        ).aggregate(
            total=models.Sum('total_amt')
        )['total'] or Decimal('0.00')
        
        # Calculate available balance
        available_balance = max(Decimal('0.00'), invoice.total_amt - total_credits)
        is_fully_credited = available_balance <= Decimal('0.01')
        
        # Calculate credit utilization percentage
        credit_utilization_percentage = Decimal('0.00')
        if invoice.total_amt > Decimal('0.00'):
            credit_utilization_percentage = (total_credits / invoice.total_amt) * Decimal('100')
        
        return {
            'calculated_total_credits': total_credits,
            'available_credit_balance': available_balance,
            'is_fully_credited': is_fully_credited,
            'credit_utilization_percentage': credit_utilization_percentage,
        }
    
    @staticmethod
    def validate_credit_amount(
        credit_note_amount: Decimal, 
        invoice_id: str,
        company_id: Optional[str] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validate if credit note amount can be applied to invoice.
        
        Args:
            credit_note_amount: Amount of the credit note
            invoice_id: ID of the target invoice
            company_id: Optional company ID for additional validation
            
        Returns:
            Tuple[bool, str, Dict]: (is_valid, error_message, details)
        """
        try:
            # Get the invoice
            try:
                invoice = Invoice.objects.select_related('company').get(id=invoice_id)
            except Invoice.DoesNotExist:
                return False, f"Invoice not found with ID: {invoice_id}", {}
            
            # Optional company validation
            if company_id and str(invoice.company.id) != str(company_id):
                return False, f"Invoice does not belong to company {company_id}", {}
            
            # Basic validation
            if credit_note_amount <= Decimal('0.00'):
                return False, "Credit note amount must be greater than zero", {}
            
            # Calculate credit summary
            summary = CreditNoteValidationService.calculate_invoice_credit_summary(invoice)
            
            # Check if invoice is fully credited
            if summary['is_fully_credited']:
                details = {
                    'invoice_number': invoice.doc_number,
                    'invoice_total': float(invoice.total_amt),
                    'calculated_total_credits': float(summary['calculated_total_credits']),
                    'available_balance': float(summary['available_credit_balance']),
                }
                return False, f"Invoice {invoice.doc_number} is already fully credited", details
            
            # Check if credit amount is available
            if credit_note_amount > summary['available_credit_balance']:
                available_balance = summary['available_credit_balance']
                details = {
                    'invoice_number': invoice.doc_number,
                    'invoice_total': float(invoice.total_amt),
                    'calculated_total_credits': float(summary['calculated_total_credits']),
                    'available_balance': float(available_balance),
                    'requested_amount': float(credit_note_amount),
                    'shortfall': float(credit_note_amount - available_balance),
                }
                return False, f"Credit amount (${credit_note_amount}) exceeds available balance (${available_balance})", details
            
            # Success - return details
            details = {
                'invoice_number': invoice.doc_number,
                'invoice_total': float(invoice.total_amt),
                'calculated_total_credits': float(summary['calculated_total_credits']),
                'available_balance': float(summary['available_credit_balance']),
                'requested_amount': float(credit_note_amount),
                'remaining_balance_after': float(summary['available_credit_balance'] - credit_note_amount),
                'is_valid': True,
            }
            
            return True, f"Credit note of ${credit_note_amount} can be applied to invoice {invoice.doc_number}", details
            
        except Exception as e:
            error_msg = f"Validation error: {str(e)}"
            return False, error_msg, {}
    
    @staticmethod
    @transaction.atomic
    def validate_and_link_credit_note(
        credit_note: CreditNote, 
        invoice_id: str,
        skip_validation: bool = False
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validate and link credit note to invoice with optimistic locking.
        
        Args:
            credit_note: CreditNote instance to link
            invoice_id: ID of the target invoice
            skip_validation: Skip validation (use with caution)
            
        Returns:
            Tuple[bool, str, Dict]: (success, message, details)
        """
        try:
            # Use select_for_update to prevent concurrent modifications
            invoice = Invoice.objects.select_for_update().get(id=invoice_id)
            
            if not skip_validation:
                # Validate before linking
                is_valid, error, details = CreditNoteValidationService.validate_credit_amount(
                    credit_note.total_amt, 
                    invoice_id,
                    str(credit_note.company.id) if credit_note.company else None
                )
                
                if not is_valid:
                    return False, error, details
            
            # Link the credit note
            credit_note.related_invoice = invoice
            credit_note.save(update_fields=['related_invoice', 'updated_at'])
            
            # Calculate updated summary
            summary = CreditNoteValidationService.calculate_invoice_credit_summary(invoice)
            
            # Get updated details
            details = {
                'credit_note_id': str(credit_note.id),
                'credit_note_number': credit_note.doc_number,
                'credit_note_amount': float(credit_note.total_amt),
                'invoice_id': str(invoice.id),
                'invoice_number': invoice.doc_number,
                'invoice_total': float(invoice.total_amt),
                'total_credits_before': float(summary['calculated_total_credits'] - credit_note.total_amt),
                'total_credits_after': float(summary['calculated_total_credits']),
                'available_balance_before': float(summary['available_credit_balance'] + credit_note.total_amt),
                'available_balance_after': float(summary['available_credit_balance']),
                'link_successful': True,
            }
            
            return True, f"Successfully linked credit note to invoice {invoice.doc_number}", details
            
        except Invoice.DoesNotExist:
            return False, f"Invoice not found with ID: {invoice_id}", {}
        except Exception as e:
            return False, f"Failed to link credit note: {str(e)}", {}
    
    @staticmethod
    def validate_credit_note_update(
        credit_note_id: str,
        new_invoice_id: Optional[str] = None,
        new_amount: Optional[Decimal] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validate credit note update (changing invoice or amount).
        
        Args:
            credit_note_id: ID of the credit note
            new_invoice_id: New invoice to link to (None if not changing)
            new_amount: New amount (None if not changing)
            
        Returns:
            Tuple[bool, str, Dict]: (is_valid, error_message, details)
        """
        try:
            credit_note = CreditNote.objects.get(id=credit_note_id)
            current_invoice = credit_note.related_invoice
            
            # Determine which invoice to validate against
            target_invoice_id = new_invoice_id or (current_invoice.id if current_invoice else None)
            amount_to_validate = new_amount or credit_note.total_amt
            
            if not target_invoice_id:
                return True, "No invoice specified, validation not required", {}
            
            # If changing invoice, we need to validate against the new invoice
            if new_invoice_id and current_invoice and str(new_invoice_id) != str(current_invoice.id):
                # We're moving to a different invoice
                # First, check if the new invoice can accept the credit
                return CreditNoteValidationService.validate_credit_amount(
                    amount_to_validate,
                    new_invoice_id,
                    str(credit_note.company.id) if credit_note.company else None
                )
            else:
                # Same invoice, just checking if amount change is valid
                return CreditNoteValidationService.validate_credit_amount(
                    amount_to_validate,
                    target_invoice_id,
                    str(credit_note.company.id) if credit_note.company else None
                )
                
        except CreditNote.DoesNotExist:
            return False, f"Credit note not found with ID: {credit_note_id}", {}
        except Exception as e:
            return False, f"Validation error: {str(e)}", {}
    
    @staticmethod
    def get_invoice_credit_summary(invoice_id: str) -> Dict[str, Any]:
        """
        Get comprehensive credit summary for an invoice.
        
        Args:
            invoice_id: ID of the invoice
            
        Returns:
            Dict: Credit summary information
        """
        try:
            # Get the invoice
            invoice = Invoice.objects.get(id=invoice_id)
            
            # Calculate credit summary
            summary = CreditNoteValidationService.calculate_invoice_credit_summary(invoice)
            
            # Get all linked credit notes
            credit_notes = CreditNote.objects.filter(
                related_invoice=invoice
            ).order_by('-txn_date')
            
            credit_notes_data = [
                {
                    'id': str(cn.id),
                    'doc_number': cn.doc_number,
                    'txn_date': cn.txn_date.isoformat(),
                    'amount': float(cn.total_amt),
                    'customer_name': cn.customer_name,
                }
                for cn in credit_notes
            ]
            
            # Build complete summary
            result = {
                'invoice_id': str(invoice.id),
                'invoice_number': invoice.doc_number,
                'customer_name': invoice.customer_name,
                'customer_display': invoice.customer.display_name if invoice.customer else invoice.customer_name,
                'invoice_total': float(invoice.total_amt),
                'calculated_total_credits': float(summary['calculated_total_credits']),
                'available_credit_balance': float(summary['available_credit_balance']),
                'is_fully_credited': summary['is_fully_credited'],
                'credit_utilization_percentage': float(summary['credit_utilization_percentage']),
                'linked_credit_notes_count': len(credit_notes_data),
                'linked_credit_notes': credit_notes_data,
            }
            
            return result
            
        except Invoice.DoesNotExist:
            return {'error': f'Invoice not found with ID: {invoice_id}'}
        except Exception as e:
            return {'error': f'Failed to get credit summary: {str(e)}'}