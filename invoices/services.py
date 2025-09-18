# invoices/services.py
import requests
from typing import List, Dict, Optional
from django.utils import timezone
from datetime import datetime
from .models import Invoice, InvoiceLine
from users.models import Company


class QuickBooksInvoiceService:
    """Service to fetch and sync invoices from QuickBooks API"""
    
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
    
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
    
    def fetch_invoices_from_qb(self) -> List[Dict]:
        """Fetch all invoices from QuickBooks API"""
        url = f"{self.BASE_URL}/v3/company/{self.company.realm_id}/query"
        query = "SELECT * FROM Invoice MAXRESULTS 1000"
        
        response = requests.get(
            url,
            headers=self.get_headers(),
            params={'query': query},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        return data.get('QueryResponse', {}).get('Invoice', [])
    
    def sync_invoice_to_db(self, invoice_data: Dict) -> Invoice:
        """Sync single invoice to database"""
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
                'private_note': invoice_data.get('PrivateNote'),
                'customer_memo': invoice_data.get('CustomerMemo', {}).get('value'),
                'sync_token': invoice_data.get('SyncToken'),
                'raw_data': invoice_data
            }
        )
        
        # Clear existing line items if updating
        if not created:
            invoice.line_items.all().delete()
        
        # Create line items
        for line_data in invoice_data.get('Line', []):
            if line_data.get('DetailType') == 'SalesItemLineDetail':
                detail = line_data.get('SalesItemLineDetail', {})
                InvoiceLine.objects.create(
                    invoice=invoice,
                    line_num=line_data.get('LineNum', 0),
                    item_ref_value=detail.get('ItemRef', {}).get('value'),
                    item_name=detail.get('ItemRef', {}).get('name'),
                    description=line_data.get('Description', ''),
                    qty=detail.get('Qty', 0),
                    unit_price=detail.get('UnitPrice', 0),
                    amount=line_data.get('Amount', 0),
                    raw_data=line_data
                )
        
        return invoice
    
    def sync_all_invoices(self) -> int:
        """Sync all invoices from QuickBooks to database"""
        invoices_data = self.fetch_invoices_from_qb()
        synced_count = 0
        
        for invoice_data in invoices_data:
            self.sync_invoice_to_db(invoice_data)
            synced_count += 1
        
        return synced_count
