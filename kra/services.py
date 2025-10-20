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
    
    def map_tax_category(self, tax_code_ref, tax_rate):
        """
        Map QuickBooks tax codes to KRA tax categories
        This needs to be customized based on your tax configuration
        """
        tax_code_ref = (tax_code_ref or "").upper()
        
        # Default mapping - adjust based on your requirements
        if tax_code_ref in ['EXEMPT', 'EXEMPTED']:
            return 'A'  # Exempted (0%)
        elif tax_rate == Decimal('0.16'):  # 16% VAT
            return 'B'
        elif tax_rate == Decimal('0.08'):  # 8% Other rate
            return 'E'
        elif tax_rate == Decimal('0.00'):
            return 'C'  # Zero-rated
        else:
            return 'D'  # Non-VAT
    
    def calculate_tax_summary(self, line_items):
        """Calculate tax summary for categories A-E"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_rate)
            taxable_amount = item.amount - item.tax_amount
            
            tax_summary[tax_category]['taxable_amount'] += taxable_amount
            
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
    
    def build_kra_payload(self, invoice, kra_invoice_number):
        """Build KRA API payload from QuickBooks invoice"""
        
        # Calculate tax summary
        tax_summary = self.calculate_tax_summary(invoice.line_items.all())
        
        # Build item list
        item_list = []
        for idx, line_item in enumerate(invoice.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_rate)
            
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
                "taxblAmt": float(line_item.amount - line_item.tax_amount),
                "taxAmt": float(line_item.tax_amount),
                "totAmt": float(line_item.amount)
            }
            item_list.append(item_data)
        
        # Build main payload
        payload = {
            "tin": self.kra_config.tin,
            "bhfId": self.kra_config.bhf_id,
            "trdInvcNo": invoice.doc_number or f"INV-{kra_invoice_number}",
            "invcNo": kra_invoice_number,
            "orgInvcNo": 0,  # 0 for normal invoices
            "custTin": invoice.customer_ref_value or "",
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
            "totTaxblAmt": float(invoice.subtotal),
            "totTaxAmt": float(invoice.tax_total),
            "totAmt": float(invoice.total_amt),
            "prchrAcptcYn": "Y",
            "remark": invoice.private_note,
            "regrId": "Admin",
            "regrNm": "Admin",
            "modrId": "Admin",
            "modrNm": "Admin",
            "receipt": {
                "custTin": invoice.customer_ref_value or "",
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