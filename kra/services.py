
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
        """Calculate tax summary for categories A-E with proper tax calculation"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_percent)
            
            # IMPORTANT: Use KES-equivalent amount from lazy calculation
            # The `amount_kes` property will calculate on-the-fly
            if hasattr(item, 'amount_kes'):
                taxable_amount = item.amount_kes  # Use lazy calculated KES amount
            else:
                taxable_amount = item.amount  # Fallback to original amount
            
            # Accumulate taxable amounts
            tax_summary[tax_category]['taxable_amount'] += taxable_amount
            
            # Calculate tax amount - FIXED: Only Category B gets 16% tax
            if tax_category == 'B':  # Standard VAT 16%
                # Use tax_amount_kes if available from lazy calculation
                if hasattr(item, 'tax_amount_kes') and item.tax_amount_kes:
                    tax_amount = item.tax_amount_kes
                elif item.tax_amount:
                    # Try to convert original tax amount to KES if needed
                    if hasattr(item.invoice, 'is_foreign_currency') and item.invoice.is_foreign_currency:
                        tax_amount = item.tax_amount * item.invoice.effective_exchange_rate
                    else:
                        tax_amount = item.tax_amount
                else:
                    # Fallback calculation using KES amount
                    tax_amount = taxable_amount * Decimal('0.16')
            else:
                # Categories A, C, D, E get 0 tax
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
        
        # LOG currency information for debugging
        print(f"🌍 Currency Info for Invoice {invoice.doc_number}:")
        print(f"  Currency: {invoice.effective_currency if hasattr(invoice, 'effective_currency') else 'KES'}")
        print(f"  Exchange Rate: {invoice.effective_exchange_rate if hasattr(invoice, 'effective_exchange_rate') else 1.0}")
        print(f"  Total Amt (Original): {invoice.total_amt}")
        print(f"  Total Amt (KES): {invoice.total_amt_kes if hasattr(invoice, 'total_amt_kes') else invoice.total_amt}")
        
        # Calculate tax summary with improved logic
        tax_summary = self.calculate_tax_summary(invoice.line_items.all())
        
        # Get customer KRA PIN with robust lookup
        customer_kra_pin = self.get_customer_kra_pin(invoice)
        
        # PARSE QUICKBOOKS TAX DETAILS
        # Extract tax amounts from QuickBooks tax lines
        qb_tax_details = {}
        if hasattr(invoice, 'txn_tax_detail') and invoice.txn_tax_detail:
            try:
                tax_detail = json.loads(invoice.txn_tax_detail) if isinstance(invoice.txn_tax_detail, str) else invoice.txn_tax_detail
                tax_lines = tax_detail.get('TaxLine', [])
                
                for tax_line in tax_lines:
                    amount = Decimal(str(tax_line.get('Amount', 0)))
                    detail = tax_line.get('TaxLineDetail', {})
                    taxable_amount = Decimal(str(detail.get('NetAmountTaxable', 0)))
                    tax_percent = Decimal(str(detail.get('TaxPercent', 0)))
                    
                    # Store by taxable amount and tax percent to match with line items
                    key = (taxable_amount, tax_percent)
                    qb_tax_details[key] = amount
                    
                    print(f"🔍 QuickBooks Tax Detail: Amount={amount}, Taxable={taxable_amount}, Percent={tax_percent}")
            except Exception as e:
                print(f"⚠️ Error parsing QuickBooks tax details: {e}")
                qb_tax_details = {}
        
        # Build item list with improved tax handling
        item_list = []
        for idx, line_item in enumerate(invoice.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_percent)
            
            # Calculate taxable amount correctly - USE KES AMOUNT
            if hasattr(line_item, 'amount_kes'):
                taxable_amount = line_item.amount_kes
            else:
                taxable_amount = line_item.amount
            
            # Calculate tax amount - FIXED LOGIC
            tax_amount = Decimal('0.00')
            
            # Method 1: Try to get from QuickBooks tax details and convert to KES if needed
            if qb_tax_details:
                # Look for tax line matching this item's amount and tax percent
                line_tax_percent = line_item.tax_percent or Decimal('0.00')
                # Use original amount for matching with QB tax details
                original_amount = line_item.amount
                key = (original_amount, line_tax_percent)
                
                if key in qb_tax_details:
                    original_tax_amount = qb_tax_details[key]
                    # Convert to KES if needed
                    if hasattr(invoice, 'is_foreign_currency') and invoice.is_foreign_currency:
                        tax_amount = original_tax_amount * invoice.effective_exchange_rate
                    else:
                        tax_amount = original_tax_amount
                    print(f"✅ Found QuickBooks tax amount for item {idx}: {original_tax_amount} -> {tax_amount} KES")
                else:
                    # Try alternative matching strategies
                    for (taxable_amt, tax_pct), tax_amt in qb_tax_details.items():
                        # Check if this tax line might correspond to our item
                        if abs(float(taxable_amt) - float(original_amount)) < 0.01:  # Amounts match
                            original_tax_amount = tax_amt
                            # Convert to KES if needed
                            if hasattr(invoice, 'is_foreign_currency') and invoice.is_foreign_currency:
                                tax_amount = original_tax_amount * invoice.effective_exchange_rate
                            else:
                                tax_amount = original_tax_amount
                            print(f"✅ Matched by amount for item {idx}: {original_tax_amount} -> {tax_amount} KES")
                            break
            
            # Method 2: Use tax_amount_kes from line_item if available (lazy calculation)
            if tax_amount == Decimal('0.00') and hasattr(line_item, 'tax_amount_kes') and line_item.tax_amount_kes and line_item.tax_amount_kes > 0:
                tax_amount = line_item.tax_amount_kes
                print(f"✅ Using line item KES tax amount for item {idx}: {tax_amount}")
            
            # Method 3: Use tax_amount from line_item and convert to KES if needed
            elif tax_amount == Decimal('0.00') and line_item.tax_amount and line_item.tax_amount > 0:
                original_tax_amount = line_item.tax_amount
                # Convert to KES if needed
                if hasattr(invoice, 'is_foreign_currency') and invoice.is_foreign_currency:
                    tax_amount = original_tax_amount * invoice.effective_exchange_rate
                else:
                    tax_amount = original_tax_amount
                print(f"✅ Using and converting line item tax amount for item {idx}: {original_tax_amount} -> {tax_amount} KES")
            
            # Method 4: Fallback calculation based on tax category using KES amounts
            if tax_amount == Decimal('0.00'):
                if tax_category == 'B':  # 16%
                    tax_amount = taxable_amount * Decimal('0.16')
                    print(f"⚠️ Calculating tax for item {idx} (Category B): {tax_amount} KES")
                else:
                    tax_amount = Decimal('0.00')
                    print(f"✅ No tax for item {idx} (Category {tax_category})")
            
            # For unit price, use KES equivalent if available
            unit_price_for_kra = line_item.unit_price
            if hasattr(line_item, 'unit_price_kes'):
                unit_price_for_kra = line_item.unit_price_kes
            
            item_data = {
                "itemSeq": idx,
                "itemCd": f"KE2NTU{line_item.item_ref_value}" or f"ITEM{idx:05d}",
                "itemClsCd": "99000000",  
                "itemNm": line_item.item_name or line_item.description or "Service",
                "bcd": None,
                "pkgUnitCd": "NT",  # No package
                "pkg": 1,
                "qtyUnitCd": "NO",  # Number
                "qty": round(float(line_item.qty), 2),
                "prc": round(float(unit_price_for_kra), 2),  # Unit price in KES
                "splyAmt": round(float(taxable_amount), 2),  # Amount in KES
                "dcRt": 0.0,
                "dcAmt": 0.0,
                "isrccCd": None,
                "isrccNm": None,
                "isrcRt": None,
                "isrcAmt": None,
                "taxTyCd": tax_category,
                "taxblAmt": round(float(taxable_amount), 2), # Taxable amount in KES
                "taxAmt": round(float(tax_amount), 2),  # Tax amount in KES
                "totAmt": round(float(taxable_amount), 2)  # Total amount in KES
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
        
        # Get total amount in KES for KRA
        if hasattr(invoice, 'total_amt_kes'):
            total_amt_for_kra = invoice.total_amt_kes
        else:
            total_amt_for_kra = invoice.total_amt
        
        # Debug: Show what's being calculated
        print(f"📊 KRA Tax Summary for Invoice {invoice.doc_number}:")
        print(f"  Currency: {invoice.effective_currency if hasattr(invoice, 'effective_currency') else 'KES'}")
        print(f"  Exchange Rate: {invoice.effective_exchange_rate if hasattr(invoice, 'effective_exchange_rate') else 1.0}")
        print(f"  Category A: Taxable={tax_summary['A']['taxable_amount']} KES, Tax={tax_summary['A']['tax_amount']} KES")
        print(f"  Category B: Taxable={tax_summary['B']['taxable_amount']} KES, Tax={tax_summary['B']['tax_amount']} KES")
        print(f"  Category C: Taxable={tax_summary['C']['taxable_amount']} KES, Tax={tax_summary['C']['tax_amount']} KES")
        print(f"  Category D: Taxable={tax_summary['D']['taxable_amount']} KES, Tax={tax_summary['D']['tax_amount']} KES")
        print(f"  Total Taxable: {total_taxable_amount} KES, Total Tax: {total_tax_amount} KES")
        print(f"  Invoice Total (Original): {invoice.total_amt}")
        print(f"  Invoice Total (KES): {total_amt_for_kra} KES")
        
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
            "taxblAmtA": round(float(tax_summary['A']['taxable_amount']), 2),
            "taxblAmtB": round(float(tax_summary['B']['taxable_amount']), 2),
            "taxblAmtC": round(float(tax_summary['C']['taxable_amount']), 2),
            "taxblAmtD": round(float(tax_summary['D']['taxable_amount']), 2),
            "taxblAmtE": 0.00,  # Changed from 0.0 to 0.00
            "taxRtA": round(float(tax_summary['A']['rate']), 2),
            "taxRtB": round(float(tax_summary['B']['rate']), 2),
            "taxRtC": round(float(tax_summary['C']['rate']), 2),
            "taxRtD": round(float(tax_summary['D']['rate']), 2),
            "taxRtE": 8.00,  # Changed from 8.0 to 8.00
            "taxAmtA": round(float(tax_summary['A']['tax_amount']), 2),
            "taxAmtB": round(float(tax_summary['B']['tax_amount']), 2),
            "taxAmtC": round(float(tax_summary['C']['tax_amount']), 2),
            "taxAmtD": round(float(tax_summary['D']['tax_amount']), 2),
            "taxAmtE": 0.00,  # Changed from 0.0 to 0.00
            "totTaxblAmt": round(float(total_taxable_amount), 2),
            "totTaxAmt": round(float(total_tax_amount), 2),
            "totAmt": round(float(total_amt_for_kra), 2),   # Use KES amount for total
            "prchrAcptcYn": "Y",
            "remark": invoice.private_note or f"Invoice {invoice.doc_number} (Currency: {invoice.effective_currency if hasattr(invoice, 'effective_currency') else 'KES'})",
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

            # Log currency information
            print(f"🌍 Submitting invoice to KRA - Currency Details:")
            print(f"  Invoice: {invoice.doc_number}")
            
            # Check if invoice has the new lazy calculation properties
            if hasattr(invoice, 'effective_currency'):
                currency = invoice.effective_currency
                exchange_rate = invoice.effective_exchange_rate
                total_amt_kes = invoice.total_amt_kes
            else:
                currency = 'KES'
                exchange_rate = Decimal('1.0')
                total_amt_kes = invoice.total_amt
            
            print(f"  Currency: {currency}")
            print(f"  Exchange Rate: {exchange_rate}")
            print(f"  Total Amount (Original): {invoice.total_amt}")
            print(f"  Total Amount (KES): {total_amt_kes}")
        
            
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
                # 'http://204.12.245.182:8985/trnsSales/saveSales',

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


from customers.models import Customer
from kra.models import KRACompanyConfig, KRAInvoiceCounter, KRAInvoiceSubmission

class KRACreditNoteService:
    """Service for handling KRA credit note submissions"""

    def __init__(self, company_id):
        self.company = Company.objects.get(id=company_id)
        self.kra_config = getattr(self.company, 'kra_config', None)
        if not self.kra_config:
            raise ValueError(f"KRA configuration not found for company: {self.company.name}")

    def get_next_kra_number(self):
        """Get next sequential number from the shared KRA counter"""
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
        """Calculate tax summary for categories A-E with proper KES conversion"""
        tax_summary = {
            'A': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'B': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('16.00')},
            'C': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'D': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('0.00')},
            'E': {'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00'), 'rate': Decimal('8.00')},
        }
        
        for item in line_items:
            tax_category = self.map_tax_category(item.tax_code_ref, item.tax_percent)
            
            # CRITICAL CHANGE: Use the lazy calculation property amount_kes
            taxable_amount = item.amount_kes  # This uses the lazy property
            
            # Accumulate taxable amounts
            tax_summary[tax_category]['taxable_amount'] += taxable_amount
            
            # Calculate tax amount - FIXED: Only Category B gets 16% tax
            if tax_category == 'B':  # Standard VAT 16%
                # Use the lazy calculation property tax_amount_kes
                if item.tax_amount_kes:
                    tax_amount = item.tax_amount_kes
                elif item.tax_amount:
                    # Fallback: Use original tax amount and convert if needed
                    if hasattr(item.credit_note, 'is_foreign_currency') and item.credit_note.is_foreign_currency:
                        tax_amount = item.tax_amount * item.credit_note.effective_exchange_rate
                    else:
                        tax_amount = item.tax_amount
                else:
                    # Fallback calculation using KES amount
                    tax_amount = taxable_amount * Decimal('0.16')
            else:
                # Categories A, C, D, E get 0 tax
                tax_amount = Decimal('0.00')
            
            tax_summary[tax_category]['tax_amount'] += tax_amount
            
            # Debug logging (optional)
            print(f"  Item {item.item_name}: Category {tax_category}, Amount KES: {taxable_amount}, Tax KES: {tax_amount}")
        
        return tax_summary
    
    def transform_date_format(self, date_obj, format_type='full'):
        """Transform date to KRA required format"""
        if format_type == 'full':  # yyyyMMddhhmmss
            return date_obj.strftime('%Y%m%d%H%M%S')
        elif format_type == 'date_only':  # yyyyMMdd
            return date_obj.strftime('%Y%m%d')
        else:
            return date_obj.strftime('%Y%m%d%H%M%S')
    
    def get_customer_kra_pin(self, credit_note):
        """Get customer KRA PIN from Customer model with robust lookup"""
        customer_kra_pin = ""
        
        # Try to find the customer by customer_ref_value
        if credit_note.customer_ref_value:
            try:
                customer = Customer.objects.filter(
                    company=self.company,
                    qb_customer_id=credit_note.customer_ref_value
                ).first()
                
                if customer and customer.kra_pin:
                    customer_kra_pin = customer.kra_pin
            except Customer.DoesNotExist:
                pass
        
        # Final fallback - check if credit_note has direct kra_pin field
        if not customer_kra_pin and hasattr(credit_note, 'kra_pin') and credit_note.kra_pin:
            customer_kra_pin = credit_note.kra_pin
        
        return customer_kra_pin
    
    def get_original_invoice_kra_number(self, credit_note):
        """Get the original invoice's KRA number for orgInvcNo"""
        original_kra_number = 0
        
        # Check if credit note has a related invoice
        if not credit_note.related_invoice:
            print(f"⚠️ Credit note {credit_note.doc_number} has no related invoice")
            return original_kra_number
        
        try:
            # Find the successful KRA submission for the original invoice
            original_submission = KRAInvoiceSubmission.objects.filter(
                company=self.company,
                invoice_id=credit_note.related_invoice.id,
                status='success'
            ).first()
            
            if original_submission:
                original_kra_number = original_submission.kra_invoice_number
                print(f"✅ Found original KRA invoice number: {original_kra_number} for invoice {credit_note.related_invoice.doc_number}")
            else:
                # If no successful submission found, check for any submission
                any_submission = KRAInvoiceSubmission.objects.filter(
                    company=self.company,
                    invoice_id=credit_note.related_invoice.id
                ).first()
                
                if any_submission:
                    print(f"⚠️ Found KRA submission but status is '{any_submission.status}' for invoice {credit_note.related_invoice.doc_number}")
                else:
                    print(f"❌ No KRA submission found for related invoice {credit_note.related_invoice.doc_number}")
                
                # For credit notes, we need the original KRA number, so we can't use fallback
                # KRA requires the actual KRA invoice number that was issued for the original invoice
                original_kra_number = 0
                
        except Exception as e:
            print(f"❌ Error getting original invoice KRA number: {str(e)}")
            original_kra_number = 0
        
        return original_kra_number
    
    def build_kra_payload(self, credit_note, kra_number):
        """Build KRA API payload from QuickBooks credit note with all tax categories (A-E)"""

        # Calculate tax summary with KES amounts
        tax_summary = self.calculate_tax_summary(credit_note.line_items.all())
        
        # Get customer KRA PIN with robust lookup
        customer_kra_pin = self.get_customer_kra_pin(credit_note)
        
        # Get original invoice KRA number for orgInvcNo
        original_invoice_kra_number = self.get_original_invoice_kra_number(credit_note)
        
        # Build item list with KES amounts
        item_list = []
        for idx, line_item in enumerate(credit_note.line_items.all(), 1):
            tax_category = self.map_tax_category(line_item.tax_code_ref, line_item.tax_percent)
            
            # USE LAZY CALCULATION PROPERTIES
            taxable_amount = line_item.amount_kes  # This is already in KES
            
            # Calculate tax amount - use the lazy property
            if tax_category == 'B':  # Standard VAT 16%
                if line_item.tax_amount_kes:
                    tax_amount = line_item.tax_amount_kes
                else:
                    # Fallback calculation
                    tax_amount = taxable_amount * Decimal('0.16')
            else:
                tax_amount = Decimal('0.00')
            
            # USE LAZY PROPERTY FOR UNIT PRICE TOO
            unit_price_for_kra = line_item.unit_price_kes
            
            item_data = {
                "itemSeq": idx,
                "itemCd": f"KE2NTU{line_item.item_ref_value}" or f"ITEM{idx:05d}",
                "itemClsCd": "99000000",  
                "itemNm": line_item.item_name or line_item.description or "Service",
                "bcd": None,
                "pkgUnitCd": "NT",  # No package
                "pkg": 1,
                "qtyUnitCd": "NO",  # Number
                "qty": round(float(line_item.qty), 2),
                "prc": round(float(unit_price_for_kra), 2),  # KES unit price
                "splyAmt": round(float(taxable_amount), 2),  # KES amount
                "dcRt": 0.0,
                "dcAmt": 0.0,
                "isrccCd": None,
                "isrccNm": None,
                "isrcRt": None,
                "isrcAmt": None,
                "taxTyCd": tax_category,
                "taxblAmt": round(float(taxable_amount), 2),  # KES taxable amount
                "taxAmt": round(float(tax_amount), 2),  # KES tax amount
                "totAmt": round(float(taxable_amount), 2)  # KES total amount
            }
            item_list.append(item_data)
        
        # Calculate total taxable amount from summary
        total_taxable_amount = sum([
            tax_summary['A']['taxable_amount'],
            tax_summary['B']['taxable_amount'], 
            tax_summary['C']['taxable_amount'],
            tax_summary['D']['taxable_amount'],
        ])
        
        # Calculate total tax amount from summary
        total_tax_amount = sum([
            tax_summary['A']['tax_amount'],
            tax_summary['B']['tax_amount'],
            tax_summary['C']['tax_amount'],
            tax_summary['D']['tax_amount'],
        ])
        
        # USE LAZY PROPERTY FOR TOTAL AMOUNT
        total_amt_for_kra = credit_note.total_amt_kes
        
        # ADD DEBUG LOGGING (similar to invoice service)
        print(f"📊 KRA Tax Summary for Credit Note {credit_note.doc_number}:")
        print(f"  Currency: {credit_note.effective_currency}")
        print(f"  Exchange Rate: {credit_note.effective_exchange_rate}")
        print(f"  Category A: Taxable={tax_summary['A']['taxable_amount']} KES, Tax={tax_summary['A']['tax_amount']} KES")
        print(f"  Category B: Taxable={tax_summary['B']['taxable_amount']} KES, Tax={tax_summary['B']['tax_amount']} KES")
        print(f"  Category C: Taxable={tax_summary['C']['taxable_amount']} KES, Tax={tax_summary['C']['tax_amount']} KES")
        print(f"  Category D: Taxable={tax_summary['D']['taxable_amount']} KES, Tax={tax_summary['D']['tax_amount']} KES")
        print(f"  Total Taxable: {total_taxable_amount} KES, Total Tax: {total_tax_amount} KES")
        print(f"  Credit Note Total (Original): {credit_note.total_amt}")
        print(f"  Credit Note Total (KES): {total_amt_for_kra} KES")
        
        # Build main payload WITH KES AMOUNTS
        payload = {
            "tin": self.kra_config.tin,
            "bhfId": self.kra_config.bhf_id,
            "trdInvcNo": credit_note.doc_number or f"CN-{kra_number}",
            "invcNo": kra_number,
            "orgInvcNo": original_invoice_kra_number,
            "custTin": customer_kra_pin,
            "custNm": credit_note.customer_name or "",
            "salesTyCd": "N",
            "rcptTyCd": "R",  # Credit note specific
            "pmtTyCd": "01",
            "salesSttsCd": "02",
            "cfmDt": self.transform_date_format(timezone.now()),
            "salesDt": self.transform_date_format(credit_note.txn_date, 'date_only'),
            "stockRlsDt": self.transform_date_format(timezone.now()),
            "cnclReqDt": None,
            "cnclDt": None,
            "rfdDt": None,
            "rfdRsnCd": None,
            "totItemCnt": len(item_list),
            "taxblAmtA": round(float(tax_summary['A']['taxable_amount']), 2),
            "taxblAmtB": round(float(tax_summary['B']['taxable_amount']), 2),
            "taxblAmtC": round(float(tax_summary['C']['taxable_amount']), 2),
            "taxblAmtD": round(float(tax_summary['D']['taxable_amount']), 2),
            "taxblAmtE": 0.00,
            "taxRtA": round(float(tax_summary['A']['rate']), 2),
            "taxRtB": round(float(tax_summary['B']['rate']), 2),
            "taxRtC": round(float(tax_summary['C']['rate']), 2),
            "taxRtD": round(float(tax_summary['D']['rate']), 2),
            "taxRtE": 8.00,
            "taxAmtA": round(float(tax_summary['A']['tax_amount']), 2),
            "taxAmtB": round(float(tax_summary['B']['tax_amount']), 2),
            "taxAmtC": round(float(tax_summary['C']['tax_amount']), 2),
            "taxAmtD": round(float(tax_summary['D']['tax_amount']), 2),
            "taxAmtE": 0.00,
            "totTaxblAmt": round(float(total_taxable_amount), 2),
            "totTaxAmt": round(float(total_tax_amount), 2),
            "totAmt": round(float(total_amt_for_kra), 2),  # KES total amount
            "prchrAcptcYn": "Y",
            "remark": credit_note.private_note or f"Credit Note for invoice {original_invoice_kra_number}",
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

        print(f"📦 Credit Note Payload - KRA: {kra_number}, Original Invoice KRA: {original_invoice_kra_number}")
        
        return payload

    def submit_to_kra(self, credit_note_id):
        """Main method to submit credit note to KRA with improved error handling"""
        try:
            # Get credit note with line items and related invoice
            credit_note = CreditNote.objects.select_related(
                'company', 
                'related_invoice'
            ).prefetch_related('line_items').get(
                id=credit_note_id, 
                company=self.company
            )

            # Validate that we have a related invoice with successful KRA submission
            if not credit_note.related_invoice:
                return {
                    'success': False,
                    'error': "Credit note must have a related invoice to submit to KRA"
                }
            
            # Check if original invoice has been submitted to KRA
            original_submission = KRAInvoiceSubmission.objects.filter(
                company=self.company,
                invoice_id=credit_note.related_invoice.id,
                status='success'
            ).first()

            if not original_submission:
                return {
                    'success': False,
                    'error': f"Original invoice {credit_note.related_invoice.doc_number} must be successfully submitted to KRA before submitting credit note"
                }

            # Get next sequential number from SHARED counter
            kra_number = self.get_next_kra_number()
            
            # Build payload
            payload = self.build_kra_payload(credit_note, kra_number)
            
            # Create submission record
            submission_data = {
                'company': self.company,
                'credit_note': credit_note,
                'kra_invoice_number': kra_number,
                'trd_invoice_no': payload['trdInvcNo'],
                'submitted_data': payload,
                'status': 'submitted'
            }
            
            # Add document_type field only if it exists in the model
            if hasattr(KRAInvoiceSubmission, 'document_type'):
                submission_data['document_type'] = 'credit_note'
            
            submission = KRAInvoiceSubmission.objects.create(**submission_data)

            # Prepare headers
            headers = {
                'tin': self.kra_config.tin,
                'bhfId': self.kra_config.bhf_id,
                'cmckey': self.kra_config.cmc_key,
                'Content-Type': 'application/json'
            }

            # Submit to KRA - use the same endpoint as invoices
            # 'http://204.12.245.182:8985/trnsSales/saveSales',
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

                    credit_note.is_kra_validated = True
                    credit_note.save(update_fields=['is_kra_validated'])
                    
                    return {
                        'success': True,
                        'submission': submission,
                        'kra_response': response_data,
                        'kra_credit_note_number': kra_number,
                        'trd_credit_note_no': payload['trdInvcNo']
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
        
        # qr_data = f"https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
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
        
        # qr_data = f"https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        qr_data = f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={tin}{bhf_id}{receipt_sign}"
        return qr_data