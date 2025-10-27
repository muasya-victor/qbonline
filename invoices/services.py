import requests
import logging
from typing import List, Dict, Optional, Any, Tuple
from django.utils import timezone
from datetime import datetime
from decimal import Decimal
from .models import Invoice, InvoiceLine
from companies.models import Company
import json
from creditnote.models import CreditNote, CreditNoteLine
import os
from project.settings_qbo import BASE_URL

# Set up logging
logger = logging.getLogger(__name__)
QBO_ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox").lower()


class QuickBooksInvoiceService:
    """Service to fetch and sync invoices from QuickBooks API with enhanced tax support"""
    
    def __init__(self, company: Company):
        self.company = company
        if not company.is_connected:
            raise ValueError("Company is not connected to QuickBooks")
    
    def get_headers(self) -> Dict[str, str]:
        """Get authorization headers for QB API"""
        return {
            'Authorization': f'Bearer {self.company.access_token}',
            'Accept': 'application/json'
        }
    
    def _log_api_call(self, response: requests.Response, operation: str, additional_info: Dict = None):
        """Log API call details including intuit_tid for troubleshooting"""
        intuit_tid = response.headers.get('intuit-tid', 'Not provided')
        request_id = response.headers.get('request-id', 'Not provided')
        
        log_data = {
            'operation': operation,
            'intuit_tid': intuit_tid,
            'request_id': request_id,
            'status_code': response.status_code,
            'url': response.url,
            'company_id': str(self.company.id),
            'realm_id': self.company.realm_id,
            'timestamp': timezone.now().isoformat()
        }
        
        if additional_info:
            log_data.update(additional_info)
        
        if response.status_code >= 400:
            logger.error(f"QuickBooks API Error: {log_data}")
            logger.error(f"Response content: {response.text}")
        else:
            logger.info(f"QuickBooks API Call: {log_data}")
    
    def fetch_invoices_from_qb(self) -> List[Dict[str, Any]]:
        """Fetch all invoices from QuickBooks API (handles pagination)"""
        url = f"{BASE_URL}/v3/company/{self.company.realm_id}/query"
        all_invoices = []
        start_position = 1
        batch_size = 1000

        while True:
            query = f"SELECT * FROM Invoice STARTPOSITION {start_position} MAXRESULTS {batch_size}"
            logger.info(f"Fetching invoices {start_position}–{start_position + batch_size - 1} for company {self.company.realm_id}")

            try:
                response = requests.get(
                    url,
                    headers=self.get_headers(),
                    params={'query': query},
                    timeout=30
                )
                response.raise_for_status()
                
                # Log the API call with intuit_tid
                self._log_api_call(
                    response, 
                    'fetch_invoices',
                    {
                        'batch_start': start_position,
                        'batch_size': batch_size,
                        'query': query
                    }
                )

                data = response.json()
                invoices = data.get("QueryResponse", {}).get("Invoice", [])
                
                if not invoices:
                    logger.info(f"No more invoices found at position {start_position}")
                    break

                all_invoices.extend(invoices)
                logger.info(f"Retrieved {len(invoices)} invoices in batch (total so far: {len(all_invoices)})")

                # Stop if fewer than batch_size were returned — means we're at the end
                if len(invoices) < batch_size:
                    logger.info(f"Reached end of invoices at position {start_position}")
                    break

                start_position += batch_size

            except requests.RequestException as e:
                logger.error(f"Request failed for invoices batch {start_position}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error fetching invoices batch {start_position}: {str(e)}")
                raise

        logger.info(f"Finished fetching {len(all_invoices)} total invoices for company {self.company.realm_id}")
        return all_invoices
    
    def extract_tax_information(self, invoice_data: Dict) -> Tuple[Decimal, Decimal, str, Decimal]:
        """Extract comprehensive tax information from invoice data"""
        subtotal = Decimal('0.00')
        tax_total = Decimal('0.00')
        tax_rate_ref = ""
        tax_percent = Decimal('0.00')
        
        # Calculate subtotal from line items
        for line_data in invoice_data.get('Line', []):
            if line_data.get('DetailType') == 'SalesItemLineDetail':
                subtotal += Decimal(str(line_data.get('Amount', 0)))
        
        # Extract tax information from TxnTaxDetail
        if 'TxnTaxDetail' in invoice_data:
            tax_detail = invoice_data['TxnTaxDetail']
            tax_total = Decimal(str(tax_detail.get('TotalTax', 0)))
            
            if 'TaxLine' in tax_detail and tax_detail['TaxLine']:
                tax_line = tax_detail['TaxLine'][0]  # First tax line
                tax_line_detail = tax_line.get('TaxLineDetail', {})
                
                tax_rate_ref = tax_line_detail.get('TaxRateRef', {}).get('value', '')
                tax_percent = Decimal(str(tax_line_detail.get('TaxPercent', 0)))
                
                logger.debug(f"Extracted tax info - RateRef: {tax_rate_ref}, Percent: {tax_percent}%, TotalTax: {tax_total}")
        
        return subtotal, tax_total, tax_rate_ref, tax_percent
    
    def extract_line_item_tax(self, line_data: Dict, invoice_tax_percent: Decimal) -> Tuple[str, Decimal, Decimal]:
        """Extract tax information for a line item"""
        detail = line_data.get('SalesItemLineDetail', {})
        tax_code_ref = detail.get('TaxCodeRef', {}).get('value', '')
        
        # Calculate tax amount for this line using the invoice-level tax percentage
        line_amount = Decimal(str(line_data.get('Amount', 0)))
        tax_amount = line_amount * (invoice_tax_percent / Decimal('100'))
        
        return tax_code_ref, tax_amount, invoice_tax_percent
    
    def sync_invoice_to_db(self, invoice_data: Dict) -> Invoice:
        """Sync single invoice to database with enhanced tax information"""
        try:
            # Extract comprehensive tax information
            subtotal, tax_total, tax_rate_ref, tax_percent = self.extract_tax_information(invoice_data)
            
            invoice, created = Invoice.objects.update_or_create(
                company=self.company,
                qb_invoice_id=invoice_data['Id'],
                defaults={
                    'doc_number': invoice_data.get('DocNumber'),
                    'txn_date': datetime.strptime(invoice_data['TxnDate'], '%Y-%m-%d').date(),
                    'due_date': datetime.strptime(invoice_data['DueDate'], '%Y-%m-%d').date() if invoice_data.get('DueDate') else None,
                    'customer_ref_value': invoice_data.get('CustomerRef', {}).get('value'),
                    'customer_name': invoice_data.get('CustomerRef', {}).get('name'),
                    'total_amt': invoice_data.get('TotalAmt', 0),
                    'balance': invoice_data.get('Balance', 0),
                    'subtotal': subtotal,
                    'tax_total': tax_total,
                    'tax_rate_ref': tax_rate_ref,
                    'tax_percent': tax_percent,
                    'private_note': invoice_data.get('PrivateNote'),
                    'customer_memo': invoice_data.get('CustomerMemo', {}).get('value'),
                    'sync_token': invoice_data.get('SyncToken'),
                    'raw_data': invoice_data
                }
            )

            # Extract template info if available
            template_ref = invoice_data.get("CustomTemplateRef", {})
            if template_ref:
                invoice.template_id = template_ref.get("value")
                invoice.template_name = template_ref.get("name")
                invoice.save(update_fields=["template_id", "template_name"])
                logger.info(f"Invoice {invoice.doc_number} uses template: {invoice.template_name} ({invoice.template_id})")
            else:
                logger.debug(f"Invoice {invoice.doc_number} has no specific template")

            # Clear existing line items if updating
            if not created:
                invoice.line_items.all().delete()

            # Create line items with enhanced tax information
            line_items_created = 0
            for line_data in invoice_data.get('Line', []):
                if line_data.get('DetailType') == 'SalesItemLineDetail':
                    detail = line_data.get('SalesItemLineDetail', {})
                    
                    # Extract tax information for this line item using invoice-level tax data
                    tax_code_ref, tax_amount, tax_percent = self.extract_line_item_tax(line_data, invoice.tax_percent)
                    
                    InvoiceLine.objects.create(
                        invoice=invoice,
                        line_num=line_data.get('LineNum', 0),
                        item_ref_value=detail.get('ItemRef', {}).get('value'),
                        item_name=detail.get('ItemRef', {}).get('name'),
                        description=line_data.get('Description', ''),
                        qty=detail.get('Qty', 0),
                        unit_price=detail.get('UnitPrice', 0),
                        amount=line_data.get('Amount', 0),
                        tax_code_ref=tax_code_ref,
                        tax_rate_ref=invoice.tax_rate_ref,
                        tax_percent=tax_percent,
                        tax_amount=tax_amount,
                        raw_data=line_data
                    )
                    line_items_created += 1
                    
                    logger.debug(f"Line {line_data.get('LineNum', 0)}: {detail.get('ItemRef', {}).get('name')} - "
                                f"Amount: {line_data.get('Amount', 0)}, Tax: {tax_amount}, "
                                f"Tax Code: {tax_code_ref}, Tax Percent: {tax_percent}%")

            logger.info(f"Synced invoice {invoice.doc_number} - "
                       f"Subtotal: {subtotal}, Tax: {tax_total} ({tax_percent}%), Total: {invoice.total_amt}, "
                       f"Lines: {line_items_created} ({'created' if created else 'updated'})")
            return invoice
            
        except Exception as e:
            logger.error(f"Failed to sync invoice {invoice_data.get('DocNumber', 'Unknown')} (ID: {invoice_data.get('Id', 'Unknown')}): {str(e)}")
            logger.error(f"Problematic invoice data: {json.dumps(invoice_data, indent=2)}")
            raise

    def sync_all_invoices(self) -> int:
        """Sync all invoices from QuickBooks to database"""
        try:
            invoices_data = self.fetch_invoices_from_qb()
            synced_count = 0
            failed_count = 0
            
            logger.info(f"Starting sync of {len(invoices_data)} invoices for company {self.company.realm_id}")
            
            for invoice_data in invoices_data:
                try:
                    self.sync_invoice_to_db(invoice_data)
                    synced_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to sync invoice {invoice_data.get('DocNumber', 'Unknown')}: {str(e)}")
                    continue
            
            logger.info(f"Invoice sync completed: {synced_count} successful, {failed_count} failed")
            return synced_count
            
        except Exception as e:
            logger.error(f"Failed to sync invoices for company {self.company.realm_id}: {str(e)}")
            raise


class QuickBooksCreditNoteService:
    """Service to fetch and sync credit notes (Credit Memos) from QuickBooks with enhanced tax support"""

    def __init__(self, company: Company):
        self.company = company
        if not company.is_connected:
            raise ValueError("Company is not connected to QuickBooks")

    def get_headers(self) -> Dict[str, str]:
        """Get authorization headers for QB API"""
        return {
            'Authorization': f'Bearer {self.company.access_token}',
            'Accept': 'application/json'
        }

    def _log_api_call(self, response: requests.Response, operation: str, additional_info: Dict = None):
        """Log API call details including intuit_tid for troubleshooting"""
        intuit_tid = response.headers.get('intuit-tid', 'Not provided')
        request_id = response.headers.get('request-id', 'Not provided')
        
        log_data = {
            'operation': operation,
            'intuit_tid': intuit_tid,
            'request_id': request_id,
            'status_code': response.status_code,
            'url': response.url,
            'company_id': str(self.company.id),
            'realm_id': self.company.realm_id,
            'timestamp': timezone.now().isoformat()
        }
        
        if additional_info:
            log_data.update(additional_info)
        
        if response.status_code >= 400:
            logger.error(f"QuickBooks CreditNote API Error: {log_data}")
            logger.error(f"Response content: {response.text}")
        else:
            logger.info(f"QuickBooks CreditNote API Call: {log_data}")

    def fetch_credit_notes_from_qb(self) -> List[Dict[str, Any]]:
        """Fetch all credit notes from QuickBooks API (handles pagination)"""
        url = f"{BASE_URL}/v3/company/{self.company.realm_id}/query"
        all_credits = []
        start_position = 1
        batch_size = 1000

        while True:
            query = f"SELECT * FROM CreditMemo STARTPOSITION {start_position} MAXRESULTS {batch_size}"
            logger.info(f"Fetching credit notes {start_position}–{start_position + batch_size - 1} for company {self.company.realm_id}")

            try:
                response = requests.get(
                    url,
                    headers=self.get_headers(),
                    params={'query': query},
                    timeout=30
                )
                response.raise_for_status()
                
                # Log the API call with intuit_tid
                self._log_api_call(
                    response,
                    'fetch_credit_notes',
                    {
                        'batch_start': start_position,
                        'batch_size': batch_size,
                        'query': query
                    }
                )
                
                data = response.json()
                credits = data.get("QueryResponse", {}).get("CreditMemo", [])
                
                if not credits:
                    logger.info(f"No more credit notes found at position {start_position}")
                    break

                all_credits.extend(credits)
                logger.info(f"Retrieved {len(credits)} credit notes in batch (total so far: {len(all_credits)})")

                # Stop if fewer than batch_size were returned — means we're at the end
                if len(credits) < batch_size:
                    logger.info(f"Reached end of credit notes at position {start_position}")
                    break

                start_position += batch_size

            except requests.RequestException as e:
                logger.error(f"Request failed for credit notes batch {start_position}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error fetching credit notes batch {start_position}: {str(e)}")
                raise

        logger.info(f"Finished fetching {len(all_credits)} total credit notes for company {self.company.realm_id}")
        return all_credits

    def extract_credit_note_tax_information(self, credit_data: Dict) -> Tuple[Decimal, Decimal, str, Decimal]:
        """Extract comprehensive tax information from credit note data"""
        subtotal = Decimal('0.00')
        tax_total = Decimal('0.00')
        tax_rate_ref = ""
        tax_percent = Decimal('0.00')
        
        # Calculate subtotal from line items
        for line_data in credit_data.get('Line', []):
            if line_data.get('DetailType') == 'SalesItemLineDetail':
                subtotal += Decimal(str(line_data.get('Amount', 0)))
        
        # Extract tax information from TxnTaxDetail
        if 'TxnTaxDetail' in credit_data:
            tax_detail = credit_data['TxnTaxDetail']
            tax_total = Decimal(str(tax_detail.get('TotalTax', 0)))
            
            if 'TaxLine' in tax_detail and tax_detail['TaxLine']:
                tax_line = tax_detail['TaxLine'][0]  # First tax line
                tax_line_detail = tax_line.get('TaxLineDetail', {})
                
                tax_rate_ref = tax_line_detail.get('TaxRateRef', {}).get('value', '')
                tax_percent = Decimal(str(tax_line_detail.get('TaxPercent', 0)))
                
                logger.debug(f"Extracted credit note tax info - RateRef: {tax_rate_ref}, Percent: {tax_percent}%, TotalTax: {tax_total}")
        
        return subtotal, tax_total, tax_rate_ref, tax_percent

    def extract_credit_line_item_tax(self, line_data: Dict, credit_tax_percent: Decimal) -> Tuple[str, Decimal, Decimal]:
        """Extract tax information for a credit note line item"""
        detail = line_data.get('SalesItemLineDetail', {})
        tax_code_ref = detail.get('TaxCodeRef', {}).get('value', '')
        
        # Calculate tax amount for this line using the credit note-level tax percentage
        line_amount = Decimal(str(line_data.get('Amount', 0)))
        tax_amount = line_amount * (credit_tax_percent / Decimal('100'))
        
        return tax_code_ref, tax_amount, credit_tax_percent

    def sync_credit_note_to_db(self, credit_data: Dict) -> CreditNote:
        """Sync single credit note to database with enhanced tax information"""
        try:
            # Find related invoice if any
            related_invoice = None
            linked_txn = credit_data.get("LinkedTxn", [])
            if linked_txn:
                invoice_ref = next((t for t in linked_txn if t.get("TxnType") == "Invoice"), None)
                if invoice_ref:
                    related_invoice = Invoice.objects.filter(
                        company=self.company,
                        qb_invoice_id=invoice_ref.get("TxnId")
                    ).first()
                    logger.debug(f"Found related invoice for credit note: {invoice_ref.get('TxnId')}")

            # Extract comprehensive tax information
            subtotal, tax_total, tax_rate_ref, tax_percent = self.extract_credit_note_tax_information(credit_data)
            
            credit_note, created = CreditNote.objects.update_or_create(
                company=self.company,
                qb_credit_id=credit_data['Id'],
                defaults={
                    'doc_number': credit_data.get('DocNumber'),
                    'txn_date': datetime.strptime(credit_data['TxnDate'], '%Y-%m-%d').date(),
                    'total_amt': credit_data.get('TotalAmt', 0),
                    'balance': credit_data.get('Balance', 0),
                    'subtotal': subtotal,
                    'tax_total': tax_total,
                    'tax_rate_ref': tax_rate_ref,
                    'tax_percent': tax_percent,
                    'customer_ref_value': credit_data.get('CustomerRef', {}).get('value'),
                    'customer_name': credit_data.get('CustomerRef', {}).get('name'),
                    'private_note': credit_data.get('PrivateNote'),
                    'customer_memo': credit_data.get('CustomerMemo', {}).get('value'),
                    'sync_token': credit_data.get('SyncToken'),
                    'related_invoice': related_invoice,
                    'raw_data': credit_data
                }
            )

            # Extract template info if available
            template_ref = credit_data.get("CustomTemplateRef", {})
            if template_ref:
                credit_note.template_id = template_ref.get("value")
                credit_note.template_name = template_ref.get("name")
                credit_note.save(update_fields=["template_id", "template_name"])
                logger.info(f"Credit note {credit_note.doc_number} uses template: {credit_note.template_name} ({credit_note.template_id})")
            else:
                logger.debug(f"Credit note {credit_note.doc_number} has no specific template")

            # Clear existing line items if updating
            if not created:
                credit_note.line_items.all().delete()

            # Create line items with enhanced tax information
            line_items_created = 0
            for line_data in credit_data.get('Line', []):
                if line_data.get('DetailType') == 'SalesItemLineDetail':
                    detail = line_data.get('SalesItemLineDetail', {})
                    
                    # Extract tax information for this line item using credit note-level tax data
                    tax_code_ref, tax_amount, tax_percent = self.extract_credit_line_item_tax(line_data, credit_note.tax_percent)
                    
                    CreditNoteLine.objects.create(
                        credit_note=credit_note,
                        line_num=line_data.get('LineNum', 0),
                        item_ref_value=detail.get('ItemRef', {}).get('value'),
                        item_name=detail.get('ItemRef', {}).get('name'),
                        description=line_data.get('Description', ''),
                        qty=detail.get('Qty', 0),
                        unit_price=detail.get('UnitPrice', 0),
                        amount=line_data.get('Amount', 0),
                        tax_code_ref=tax_code_ref,
                        tax_rate_ref=credit_note.tax_rate_ref,
                        tax_percent=tax_percent,
                        tax_amount=tax_amount,
                        raw_data=line_data
                    )
                    line_items_created += 1
                    
                    logger.debug(f"Credit line {line_data.get('LineNum', 0)}: {detail.get('ItemRef', {}).get('name')} - "
                                f"Amount: {line_data.get('Amount', 0)}, Tax: {tax_amount}, "
                                f"Tax Code: {tax_code_ref}, Tax Percent: {tax_percent}%")

            logger.info(f"Synced credit note {credit_note.doc_number or credit_note.qb_credit_id} - "
                       f"Subtotal: {subtotal}, Tax: {tax_total} ({tax_percent}%), Total: {credit_note.total_amt}, "
                       f"Lines: {line_items_created} ({'created' if created else 'updated'})")
            return credit_note
            
        except Exception as e:
            logger.error(f"Failed to sync credit note {credit_data.get('DocNumber', 'Unknown')} (ID: {credit_data.get('Id', 'Unknown')}): {str(e)}")
            logger.error(f"Problematic credit note data: {json.dumps(credit_data, indent=2)}")
            raise

    def sync_all_credit_notes(self) -> int:
        """Sync all credit notes from QuickBooks to database"""
        try:
            credits_data = self.fetch_credit_notes_from_qb()
            synced_count = 0
            failed_count = 0
            
            logger.info(f"Starting sync of {len(credits_data)} credit notes for company {self.company.realm_id}")
            
            for credit_data in credits_data:
                try:
                    self.sync_credit_note_to_db(credit_data)
                    synced_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to sync credit note {credit_data.get('DocNumber', 'Unknown')}: {str(e)}")
                    continue
            
            logger.info(f"Credit note sync completed: {synced_count} successful, {failed_count} failed")
            return synced_count
            
        except Exception as e:
            logger.error(f"Failed to sync credit notes for company {self.company.realm_id}: {str(e)}")
            raise