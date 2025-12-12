
import requests
import json
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from companies.models import Company
from invoices.models import Invoice
from customers.models import Customer
from .models import KRACompanyConfig, KRAInvoiceCounter, KRAInvoiceSubmission
import logging
from typing import Dict, Any, Optional
from creditnote.models import CreditNote


class KRAInvoiceService:
    """Service for handling KRA invoice submissions"""
    
    def __init__(self, company_id):
        self.company = Company.objects.get(id=company_id)
        self.kra_config = getattr(self.company, 'kra_config', None)
        if not self.kra_config:
            raise ValueError(f"KRA configuration not found for company: {self.company.name}")
    
    def get_next_invoice_number(self):
        """Get next sequential invoice number for KRA"""
        with transaction.atomic():
            counter, created = KRAInvoiceCounter.objects.select_for_update().get_or_create(
                company=self.company,
                defaults={'last_invoice_number': 0}
            )
            counter.last_invoice_number += 1
            counter.save()
            return counter.last_invoice_number
        
    def map_tax_category(self, tax_code_ref, tax_percent):
        """
        Map QuickBooks tax information to KRA tax categories (A-D only, never E)
        HARDCODED MAPPING FOR CURRENT COMPANY:
        - "13" → Category B (Standard VAT 16%)
        - "14" → Category A (Exempt 0%)
        - "15" → Category D (Non-VAT 0%)
        - "16" → Category C (Zero-rated 0%)
        - "23" → Category A (Exempt 0%)
        - "0"  → Category C (Zero-rated 0%)
        - Default: Category D (Non-VAT 0%)
        """
        tax_code_ref = str(tax_code_ref or "").upper()
        
        # Convert tax_percent to Decimal if it's not already
        if tax_percent is not None:
            try:
                tax_percent = Decimal(str(tax_percent))
            except (ValueError, TypeError):
                tax_percent = Decimal('0.00')
        else:
            tax_percent = Decimal('0.00')
        
        # HARDCODED MAPPING FOR CURRENT COMPANY'S QUICKBOOKS
        qb_tax_code_mapping = {
            # Category B: Standard VAT 16%
            '13': 'B',
            
            # Category A: Exempt (0%)
            '14': 'A',
            '23': 'A',
            
            # Category C: Zero-rated (0%)
            '16': 'C',
            '0': 'C',
            
            # Category D: Non-VAT (0%)
            '15': 'D',
        }
        
        # First try to map by QuickBooks tax code
        if tax_code_ref in qb_tax_code_mapping:
            return qb_tax_code_mapping[tax_code_ref]
        
        # Fall back to tax percent mapping as secondary check
        if tax_percent == Decimal('16') or tax_percent == Decimal('16.00'):
            return 'B'  # 16% VAT
        elif tax_percent == Decimal('0') or tax_percent == Decimal('0.00'):
            # For 0% tax codes not in our mapping, default to Category D
            return 'D'
        else:
            # For any other tax rate (like 8% if it exists), default to Category D
            # NOT Category E - we don't use category E anymore
            return 'D'
        
    def calculate_tax_summary(self, line_items):
        """Calculate tax summary for categories A-E"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},  # ADDED: Category E with 8% rate but 0 amounts
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_percent)
            
            # Use the actual taxable amount from the line item
            taxable_amount = item.amount
            
            # Only accumulate amounts for categories A-D (E remains 0)
            if tax_category in ['A', 'B', 'C', 'D']:
                tax_summary[tax_category]['taxable_amount'] += taxable_amount
                
                # Use the actual tax_amount from the line item if available, otherwise calculate
                if item.tax_amount and item.tax_amount > 0:
                    tax_amount = item.tax_amount
                else:
                    # Calculate tax amount based on category rate as fallback
                    if tax_category == 'B':  # 16%
                        tax_amount = taxable_amount * Decimal('0.16')
                    else:
                        tax_amount = Decimal('0.00')
                
                tax_summary[tax_category]['tax_amount'] += tax_amount
            # Category E items are not expected, but if they appear, they would have 0 taxable amount
            # and 0 tax amount since we don't use category E anymore
        
        return tax_summary
    
    def transform_date_format(self, date_obj, format_type='full'):
        """Transform date to KRA required format"""
        if format_type == 'full':  # yyyyMMddhhmmss
            return date_obj.strftime('%Y%m%d%H%M%S')
        elif format_type == 'date_only':  # yyyyMMdd
            return date_obj.strftime('%Y%m%d')
        else:
            return date_obj.strftime('%Y%m%d%H%M%S')
    
    def get_customer_kra_pin(self, invoice):
        """Get customer KRA PIN from Customer model with robust lookup"""
        customer_kra_pin = ""
        
        # Try to find the customer by customer_ref_value
        if invoice.customer_ref_value:
            try:
                customer = Customer.objects.filter(
                    company=self.company,
                    qb_customer_id=invoice.customer_ref_value
                ).first()
                
                if customer and customer.kra_pin:
                    customer_kra_pin = customer.kra_pin
            except Customer.DoesNotExist:
                pass
        
        # If no customer found via customer_ref_value, try direct customer relationship
        if not customer_kra_pin and invoice.customer and invoice.customer.kra_pin:
            customer_kra_pin = invoice.customer.kra_pin
        
        # Final fallback - check if invoice has direct kra_pin field
        if not customer_kra_pin and hasattr(invoice, 'kra_pin') and invoice.kra_pin:
            customer_kra_pin = invoice.kra_pin
        
        return customer_kra_pin
    
    def build_kra_payload(self, invoice, kra_invoice_number):
        """Build KRA API payload from QuickBooks invoice with all tax categories (A-E)"""
        
        # Calculate tax summary with improved logic
        tax_summary = self.calculate_tax_summary(invoice.line_items.all())
        
        # Get customer KRA PIN with robust lookup
        customer_kra_pin = self.get_customer_kra_pin(invoice)
        
        # Build item list with improved tax handling
        item_list = []
        for idx, line_item in enumerate(invoice.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_percent)
            
            # Calculate taxable amount correctly
            taxable_amount = line_item.amount
            
            # Calculate tax amount with fallback
            if line_item.tax_amount and line_item.tax_amount > 0:
                tax_amount = line_item.tax_amount
            else:
                # Fallback calculation based on tax category
                if tax_category == 'B':  # 16%
                    tax_amount = taxable_amount * Decimal('0.16')
                else:
                    tax_amount = Decimal('0.00')
            
            item_data = {
                "itemSeq": idx,
                "itemCd": f"KE2NTU{line_item.item_ref_value}" or f"ITEM{idx:05d}",
                "itemClsCd": "99000000",  
                "itemNm": line_item.item_name or line_item.description or "Service",
                "bcd": None,
                "pkgUnitCd": "NT",  # No package
                "pkg": 1,
                "qtyUnitCd": "NO",  # Number
                "qty": float(line_item.qty),
                "prc": float(line_item.unit_price),
                "splyAmt": float(line_item.amount),
                "dcRt": 0.0,
                "dcAmt": 0.0,
                "isrccCd": None,
                "isrccNm": None,
                "isrcRt": None,
                "isrcAmt": None,
                "taxTyCd": tax_category,
                "taxblAmt": float(taxable_amount),
                "taxAmt": float(tax_amount),  # Use calculated tax amount
                "totAmt": float(line_item.amount)
            }
            item_list.append(item_data)
        
        # Calculate total taxable amount from summary (including only A-D since E is always 0)
        total_taxable_amount = sum([
            tax_summary['A']['taxable_amount'],
            tax_summary['B']['taxable_amount'], 
            tax_summary['C']['taxable_amount'],
            tax_summary['D']['taxable_amount'],
        ])
        
        # Calculate total tax amount from summary (including only A-D since E is always 0)
        total_tax_amount = sum([
            tax_summary['A']['tax_amount'],
            tax_summary['B']['tax_amount'],
            tax_summary['C']['tax_amount'],
            tax_summary['D']['tax_amount'],
        ])
        
        # Build main payload WITH 'E' fields (all set to 0)
        payload = {
            "tin": self.kra_config.tin,
            "bhfId": self.kra_config.bhf_id,
            "trdInvcNo": invoice.doc_number or f"INV-{kra_invoice_number}",
            "invcNo": kra_invoice_number,
            "orgInvcNo": 0,  # 0 for original invoices
            "custTin": customer_kra_pin,
            "custNm": invoice.customer_name or "",
            "salesTyCd": "N",  # Normal sale
            "rcptTyCd": "S",  # Sale
            "pmtTyCd": "01",  # Cash payment type
            "salesSttsCd": "02",  # Approved
            "cfmDt": self.transform_date_format(timezone.now()),
            "salesDt": self.transform_date_format(invoice.txn_date, 'date_only'),
            "stockRlsDt": self.transform_date_format(timezone.now()),
            "cnclReqDt": None,
            "cnclDt": None,
            "rfdDt": None,
            "rfdRsnCd": None,
            "totItemCnt": len(item_list),
            "taxblAmtA": float(tax_summary['A']['taxable_amount']),
            "taxblAmtB": float(tax_summary['B']['taxable_amount']),
            "taxblAmtC": float(tax_summary['C']['taxable_amount']),
            "taxblAmtD": float(tax_summary['D']['taxable_amount']),
            "taxblAmtE": 0.0,  # ADDED: Always 0
            "taxRtA": float(tax_summary['A']['rate']),
            "taxRtB": float(tax_summary['B']['rate']),
            "taxRtC": float(tax_summary['C']['rate']),
            "taxRtD": float(tax_summary['D']['rate']),
            "taxRtE": 8.0,  # ADDED: Rate is 8% but amount is 0
            "taxAmtA": float(tax_summary['A']['tax_amount']),
            "taxAmtB": float(tax_summary['B']['tax_amount']),
            "taxAmtC": float(tax_summary['C']['tax_amount']),
            "taxAmtD": float(tax_summary['D']['tax_amount']),
            "taxAmtE": 0.0,  # ADDED: Always 0
            "totTaxblAmt": float(total_taxable_amount),
            "totTaxAmt": float(total_tax_amount),
            "totAmt": float(invoice.total_amt),
            "prchrAcptcYn": "Y",
            "remark": invoice.private_note or f"Invoice {invoice.doc_number}",
            "regrId": "Admin",
            "regrNm": "Admin",
            "modrId": "Admin",
            "modrNm": "Admin",
            "receipt": {
                "custTin": customer_kra_pin,
                "custMblNo": "",
                "rcptPbctDt": self.transform_date_format(timezone.now()),
                "trdeNm": self.kra_config.trade_name,
                "adrs": self.kra_config.address,
                "topMsg": self.kra_config.top_message,
                "btmMsg": self.kra_config.bottom_message,
                "prchrAcptcYn": "Y"
            },
            "itemList": item_list
        }
        
        return payload

    def submit_to_kra(self, invoice_id):
        """Main method to submit invoice to KRA with improved error handling"""
        try:
            # Get invoice with line items
            invoice = Invoice.objects.select_related('company').prefetch_related('line_items').get(
                id=invoice_id, 
                company=self.company
            )
            
            # Get next sequential invoice number
            kra_invoice_number = self.get_next_invoice_number()
            
            # Build payload with improved logic
            payload = self.build_kra_payload(invoice, kra_invoice_number)
            
            # Create submission record
            submission_data = {
                'company': self.company,
                'invoice': invoice,
                'kra_invoice_number': kra_invoice_number,
                'trd_invoice_no': payload['trdInvcNo'],
                'submitted_data': payload,
                'status': 'submitted'
            }
            
            # Add document_type field only if it exists in the model
            if hasattr(KRAInvoiceSubmission, 'document_type'):
                submission_data['document_type'] = 'invoice'
            
            submission = KRAInvoiceSubmission.objects.create(**submission_data)
            
            # Prepare headers
            headers = {
                'tin': self.kra_config.tin,
                'bhfId': self.kra_config.bhf_id,
                'cmckey': self.kra_config.cmc_key,
                'Content-Type': 'application/json'
            }
            
            # Submit to KRA - use the same endpoint as credit notes for consistency
            # 'http://204.12.227.240:8089/trnsSales/saveSales',
            response = requests.post(
                'http://204.12.245.182:8985/trnsSales/saveSales',
                json=payload,
                headers=headers,
                timeout=30
            )
            
            # Process response
            if response.status_code == 200:
                response_data = response.json()
                
                if response_data.get('resultCd') == '000':  # Success
                    data = response_data.get('data', {})
                    
                    submission.status = 'success'
                    submission.response_data = response_data
                    submission.receipt_signature = data.get('rcptSign', '')
                    submission.qr_code_data = self.generate_qr_code_data(data)
                    submission.save()

                    invoice.is_kra_validated = True
                    invoice.save(update_fields=['is_kra_validated'])

                    
                    return {
                        'success': True,
                        'submission': submission,
                        'kra_response': response_data,
                        'kra_invoice_number': kra_invoice_number,
                        'trd_invoice_no': payload['trdInvcNo']
                    }
                else:
                    # KRA returned error
                    submission.status = 'failed'
                    submission.response_data = response_data
                    submission.error_message = response_data.get('resultMsg', 'Unknown KRA error')
                    submission.save()
                    
                    return {
                        'success': False,
                        'error': response_data.get('resultMsg', 'Unknown KRA error'),
                        'submission': submission
                    }
            else:
                # HTTP error
                submission.status = 'failed'
                submission.error_message = f"HTTP {response.status_code}: {response.text}"
                submission.save()
                
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}: {response.text}",
                    'submission': submission
                }
                
        except Invoice.DoesNotExist:
            return {
                'success': False,
                'error': f"Invoice not found for company: {self.company.name}"
            }
        except Exception as e:
            # Create failed submission if we got that far
            if 'submission' in locals():
                submission.status = 'failed'
                submission.error_message = str(e)
                submission.save()
            
            return {
                'success': False,
                'error': str(e)
            }
    
    def generate_qr_code_data(self, kra_data):
        """Generate QR code data from KRA response"""
        tin = self.kra_config.tin
        bhf_id = self.kra_config.bhf_id
        receipt_sign = kra_data.get('rcptSign', '')
        
        qr_data = f"https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        # qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        return qr_data
    

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
    
    def generate_qr_code_data(self, kra_data):
        """Generate QR code data from KRA response"""
        tin = self.kra_config.tin
        bhf_id = self.kra_config.bhf_id
        receipt_sign = kra_data.get('rcptSign', '')
        
        qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        return qr_data