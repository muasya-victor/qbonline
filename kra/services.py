import requests
import json
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from companies.models import Company
from invoices.models import Invoice
from .models import KRACompanyConfig, KRAInvoiceCounter, KRAInvoiceSubmission

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
        Map QuickBooks tax information to KRA tax categories
        Using actual tax percentage from TxnTaxDetail
        """
        # Convert tax_percent to Decimal
        if tax_percent is not None:
            try:
                tax_percent = Decimal(str(tax_percent))
            except (ValueError, TypeError):
                tax_percent = Decimal('0.00')
        else:
            tax_percent = Decimal('0.00')
        
        # Map based on actual tax percentage
        if tax_percent == Decimal('16'):
            return 'B'  # Standard VAT 16%
        elif tax_percent == Decimal('8'):
            return 'E'  # Reduced VAT 8%
        elif tax_percent == Decimal('0'):
            # Check if it's exempt or zero-rated based on tax_code_ref
            tax_code_ref = str(tax_code_ref or "").upper()
            if tax_code_ref in ['EXEMPT', 'EXEMPTED', 'EXEMPTION']:
                return 'A'  # Exempt
            else:
                return 'C'  # Zero-rated
        else:
            return 'D'  # Other/Non-VAT
    
    def calculate_tax_summary(self, line_items):
        """Calculate tax summary for categories A-E using actual tax data"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_percent)
            
            taxable_amount = item.amount
            tax_amount = item.tax_amount
            
            tax_summary[tax_category]['taxable_amount'] += taxable_amount
            tax_summary[tax_category]['tax_amount'] += tax_amount
        
        return tax_summary
    
    def transform_date_format(self, date_obj, format_type='full'):
        """Transform date to KRA required format"""
        if format_type == 'full':  # yyyyMMddhhmmss
            return date_obj.strftime('%Y%m%d%H%M%S')
        elif format_type == 'date_only':  # yyyyMMdd
            return date_obj.strftime('%Y%m%d')
        else:
            return date_obj.strftime('%Y%m%d%H%M%S')
    
    def build_kra_payload(self, invoice, kra_invoice_number):
        """Build KRA API payload from QuickBooks invoice"""
        
        # Calculate tax summary
        tax_summary = self.calculate_tax_summary(invoice.line_items.all())
        
        # Get customer KRA PIN - check both customer relationship and customer_ref_value
        customer_kra_pin = ""
        if invoice.customer and invoice.customer.kra_pin:
            customer_kra_pin = invoice.customer.kra_pin
        elif hasattr(invoice, 'kra_pin') and invoice.kra_pin:
            customer_kra_pin = invoice.kra_pin
        
        # Build item list
        item_list = []
        for idx, line_item in enumerate(invoice.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_percent)
            
            # Calculate taxable amount correctly
            taxable_amount = line_item.amount
            
            item_data = {
                "itemSeq": idx,
                "itemCd": line_item.item_ref_value or f"ITEM{idx:05d}",
                "itemClsCd": "8500000000",  # Default service classification
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
                "taxAmt": float(line_item.tax_amount),
                "totAmt": float(line_item.amount)
            }
            item_list.append(item_data)
        
        # Calculate total taxable amount from summary
        total_taxable_amount = sum([
            tax_summary['A']['taxable_amount'],
            tax_summary['B']['taxable_amount'], 
            tax_summary['C']['taxable_amount'],
            tax_summary['D']['taxable_amount'],
            tax_summary['E']['taxable_amount']
        ])
        
        # Calculate total tax amount from summary
        total_tax_amount = sum([
            tax_summary['A']['tax_amount'],
            tax_summary['B']['tax_amount'],
            tax_summary['C']['tax_amount'],
            tax_summary['D']['tax_amount'],
            tax_summary['E']['tax_amount']
        ])
        
        # Build main payload
        payload = {
            "tin": self.kra_config.tin,
            "bhfId": self.kra_config.bhf_id,
            "trdInvcNo": invoice.doc_number or f"INV-{kra_invoice_number}",
            "invcNo": kra_invoice_number,
            "orgInvcNo": 0,  # 0 for normal invoices
            "custTin": customer_kra_pin,  # Use customer's KRA PIN
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
            "taxblAmtE": float(tax_summary['E']['taxable_amount']),
            "taxRtA": float(tax_summary['A']['rate']),
            "taxRtB": float(tax_summary['B']['rate']),
            "taxRtC": float(tax_summary['C']['rate']),
            "taxRtD": float(tax_summary['D']['rate']),
            "taxRtE": float(tax_summary['E']['rate']),
            "taxAmtA": float(tax_summary['A']['tax_amount']),
            "taxAmtB": float(tax_summary['B']['tax_amount']),
            "taxAmtC": float(tax_summary['C']['tax_amount']),
            "taxAmtD": float(tax_summary['D']['tax_amount']),
            "taxAmtE": float(tax_summary['E']['tax_amount']),
            "totTaxblAmt": float(total_taxable_amount),
            "totTaxAmt": float(total_tax_amount),
            "totAmt": float(invoice.total_amt),
            "prchrAcptcYn": "Y",
            "remark": invoice.private_note,
            "regrId": "Admin",
            "regrNm": "Admin",
            "modrId": "Admin",
            "modrNm": "Admin",
            "receipt": {
                "custTin": customer_kra_pin,  # Use customer's KRA PIN here too
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
        """Main method to submit invoice to KRA"""
        try:
            # Get invoice with line items
            invoice = Invoice.objects.select_related('company').prefetch_related('line_items').get(
                id=invoice_id, 
                company=self.company
            )
            
            # Get next sequential invoice number
            kra_invoice_number = self.get_next_invoice_number()
            
            # Build payload
            payload = self.build_kra_payload(invoice, kra_invoice_number)
            
            # Create submission record
            submission = KRAInvoiceSubmission.objects.create(
                company=self.company,
                invoice=invoice,
                kra_invoice_number=kra_invoice_number,
                trd_invoice_no=payload['trdInvcNo'],
                submitted_data=payload,
                status='submitted'
            )
            
            # Prepare headers
            headers = {
                'tin': self.kra_config.tin,
                'bhfId': self.kra_config.bhf_id,
                'cmckey': self.kra_config.cmc_key,
                'Content-Type': 'application/json'
            }
            
            # Submit to KRA
            response = requests.post(
                'http://204.12.227.240:8089/trnsSales/saveSales',
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
                        'kra_response': response_data
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
        
        qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        return qr_data
    
    
# kra/services_credit_note.py
import requests
import json
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from companies.models import Company
from creditnote.models import CreditNote
from .models import KRACompanyConfig, KRACreditNoteCounter, KRACreditNoteSubmission

class KRACreditNoteService:
    """Service for handling KRA credit note submissions"""
    
    def __init__(self, company_id):
        self.company = Company.objects.get(id=company_id)
        self.kra_config = getattr(self.company, 'kra_config', None)
        if not self.kra_config:
            raise ValueError(f"KRA configuration not found for company: {self.company.name}")
    
    def get_next_credit_note_number(self):
        """Get next sequential credit note number for KRA"""
        with transaction.atomic():
            counter, created = KRACreditNoteCounter.objects.select_for_update().get_or_create(
                company=self.company,
                defaults={'last_credit_note_number': 0}
            )
            counter.last_credit_note_number += 1
            counter.save()
            return counter.last_credit_note_number
    
    def map_tax_category(self, tax_code_ref, tax_rate):
        """
        Map QuickBooks tax codes to KRA tax categories
        Same logic as invoice service
        """
        tax_code_ref = str(tax_code_ref or "").upper()
        
        # Convert tax_rate to Decimal if it's not already
        if tax_rate is not None:
            try:
                tax_rate = Decimal(str(tax_rate))
            except (ValueError, TypeError):
                tax_rate = Decimal('0.00')
        else:
            tax_rate = Decimal('0.00')
        
        # Map common QuickBooks tax codes to KRA categories
        tax_code_mapping = {
            # VAT 16%
            '13': 'B',  # Assuming "13" is 16% VAT in your QB
            'TAX': 'B',
            'VAT': 'B',
            '16': 'B',
            '16%': 'B',
            
            # VAT 8%
            '8': 'E',   # Assuming "8" is 8% VAT
            'VAT8': 'E',
            '8%': 'E',
            
            # Zero-rated
            '0': 'C',
            'ZERO': 'C',
            'NON': 'C',
            'NONE': 'C',
            'ZERO-RATED': 'C',
            
            # Exempt
            'EXEMPT': 'A',
            'EXEMPTED': 'A',
            'EXEMPTION': 'A',
        }
        
        # First try to map by tax code
        if tax_code_ref in tax_code_mapping:
            return tax_code_mapping[tax_code_ref]
        
        # Fall back to tax rate mapping
        if tax_rate == Decimal('16') or tax_rate == Decimal('16.00'):
            return 'B'
        elif tax_rate == Decimal('8') or tax_rate == Decimal('8.00'):
            return 'E'
        elif tax_rate == Decimal('0') or tax_rate == Decimal('0.00'):
            return 'C'
        else:
            return 'D'  # Non-VAT or unknown
    
    def calculate_tax_summary(self, line_items):
        """Calculate tax summary for categories A-E with corrected logic"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_rate)
            
            # Use the actual taxable amount from the line item
            taxable_amount = item.amount  # This should be the taxable amount
            
            tax_summary[tax_category]['taxable_amount'] += taxable_amount
            
            # Use the actual tax_amount from the line item if available
            if item.tax_amount and item.tax_amount > 0:
                tax_amount = item.tax_amount
            else:
                # Calculate tax amount based on category rate
                if tax_category == 'B':  # 16%
                    tax_amount = taxable_amount * Decimal('0.16')
                elif tax_category == 'E':  # 8%
                    tax_amount = taxable_amount * Decimal('0.08')
                else:
                    tax_amount = Decimal('0.00')
            
            tax_summary[tax_category]['tax_amount'] += tax_amount
        
        return tax_summary
    
    def transform_date_format(self, date_obj, format_type='full'):
        """Transform date to KRA required format"""
        if format_type == 'full':  # yyyyMMddhhmmss
            return date_obj.strftime('%Y%m%d%H%M%S')
        elif format_type == 'date_only':  # yyyyMMdd
            return date_obj.strftime('%Y%m%d')
        else:
            return date_obj.strftime('%Y%m%d%H%M%S')
    
    def build_kra_payload(self, credit_note, kra_credit_note_number):
        """Build KRA API payload from QuickBooks credit note"""
        
        # Calculate tax summary
        tax_summary = self.calculate_tax_summary(credit_note.line_items.all())
        
        # Build item list
        item_list = []
        for idx, line_item in enumerate(credit_note.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_rate)
            
            # Calculate taxable amount correctly
            taxable_amount = line_item.amount  # Use amount as taxable base
            
            item_data = {
                "itemSeq": idx,
                "itemCd": line_item.item_ref_value or f"ITEM{idx:05d}",
                "itemClsCd": "8500000000",  # Default service classification
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
                "taxblAmt": float(taxable_amount),  # Use calculated taxable amount
                "taxAmt": float(line_item.tax_amount),
                "totAmt": float(line_item.amount)
            }
            item_list.append(item_data)
        
        # Calculate total taxable amount from summary
        total_taxable_amount = sum([
            tax_summary['A']['taxable_amount'],
            tax_summary['B']['taxable_amount'], 
            tax_summary['C']['taxable_amount'],
            tax_summary['D']['taxable_amount'],
            tax_summary['E']['taxable_amount']
        ])
        
        # Calculate total tax amount from summary
        total_tax_amount = sum([
            tax_summary['A']['tax_amount'],
            tax_summary['B']['tax_amount'],
            tax_summary['C']['tax_amount'],
            tax_summary['D']['tax_amount'],
            tax_summary['E']['tax_amount']
        ])
        
        # Get original invoice reference if available
        original_invoice_ref = ""
        if credit_note.related_invoice:
            original_invoice_ref = credit_note.related_invoice.doc_number or ""
        
        # Build main payload for credit note
        payload = {
            "tin": self.kra_config.tin,
            "bhfId": self.kra_config.bhf_id,
            "trdInvcNo": credit_note.doc_number or f"CN-{kra_credit_note_number}",
            "invcNo": kra_credit_note_number,
            "orgInvcNo": original_invoice_ref or 0,  # Original invoice number for credit notes
            "custTin": credit_note.customer_ref_value or "",
            "custNm": credit_note.customer_name or "",
            "salesTyCd": "N",  # Normal sale
            "rcptTyCd": "R",  # Return/credit note (different from invoice)
            "pmtTyCd": "01",  # Cash payment type
            "salesSttsCd": "02",  # Approved
            "cfmDt": self.transform_date_format(timezone.now()),
            "salesDt": self.transform_date_format(credit_note.txn_date, 'date_only'),
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
            "taxblAmtE": float(tax_summary['E']['taxable_amount']),
            "taxRtA": float(tax_summary['A']['rate']),
            "taxRtB": float(tax_summary['B']['rate']),
            "taxRtC": float(tax_summary['C']['rate']),
            "taxRtD": float(tax_summary['D']['rate']),
            "taxRtE": float(tax_summary['E']['rate']),
            "taxAmtA": float(tax_summary['A']['tax_amount']),
            "taxAmtB": float(tax_summary['B']['tax_amount']),
            "taxAmtC": float(tax_summary['C']['tax_amount']),
            "taxAmtD": float(tax_summary['D']['tax_amount']),
            "taxAmtE": float(tax_summary['E']['tax_amount']),
            "totTaxblAmt": float(total_taxable_amount),  # Use calculated total taxable
            "totTaxAmt": float(total_tax_amount),        # Use calculated total tax
            "totAmt": float(credit_note.total_amt),
            "prchrAcptcYn": "Y",
            "remark": credit_note.private_note or f"Credit Note for {original_invoice_ref}" if original_invoice_ref else "Credit Note",
            "regrId": "Admin",
            "regrNm": "Admin",
            "modrId": "Admin",
            "modrNm": "Admin",
            "receipt": {
                "custTin": credit_note.customer_ref_value or "",
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
    
    def submit_to_kra(self, credit_note_id):
        """Main method to submit credit note to KRA"""
        try:
            # Get credit note with line items
            credit_note = CreditNote.objects.select_related('company', 'related_invoice').prefetch_related('line_items').get(
                id=credit_note_id, 
                company=self.company
            )
            
            # Get next sequential credit note number
            kra_credit_note_number = self.get_next_credit_note_number()
            
            # Build payload
            payload = self.build_kra_payload(credit_note, kra_credit_note_number)
            
            # Create submission record
            submission = KRACreditNoteSubmission.objects.create(
                company=self.company,
                credit_note=credit_note,
                kra_credit_note_number=kra_credit_note_number,
                trd_credit_note_no=payload['trdInvcNo'],
                submitted_data=payload,
                status='submitted'
            )
            
            # Prepare headers
            headers = {
                'tin': self.kra_config.tin,
                'bhfId': self.kra_config.bhf_id,
                'cmckey': self.kra_config.cmc_key,
                'Content-Type': 'application/json'
            }
            
            # Submit to KRA - using the same endpoint as invoices for credit notes
            response = requests.post(
                'http://204.12.227.240:8089/trnsSales/saveSales',  # Same endpoint as invoices
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

                    credit_note.is_kra_validated = True
                    credit_note.save(update_fields=['is_kra_validated'])

                    
                    return {
                        'success': True,
                        'submission': submission,
                        'kra_response': response_data
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
                
        except CreditNote.DoesNotExist:
            return {
                'success': False,
                'error': f"Credit note not found for company: {self.company.name}"
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
        
        qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        return qr_data