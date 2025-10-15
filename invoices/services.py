# invoices/services.py
import requests
from typing import List, Dict, Optional, Any
from django.utils import timezone
from datetime import datetime
from .models import Invoice, InvoiceLine
from companies.models import Company
import json
from creditnote.models import CreditNote, CreditNoteLine


"""
2. Does your app capture the value of the intuit_tid field from response headers?
Tip: We recommend you capture this field. It will help our support team quickly identify issues when troubleshooting.

3. Does your app have a mechanism for storing all error information in logs that can be shared for troubleshooting purposes, if required?
Tip: We recommend you maintain logs. It will help our support team quickly identify issues when troubleshooting.
"""
class QuickBooksInvoiceService:
    """Service to fetch and sync invoices from QuickBooks API"""
    
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
    # BASE_URL = "https://quickbooks.api.intuit.com"
    
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
    
    def fetch_invoices_from_qb(self) -> List[Dict[str, Any]]:
        """Fetch all invoices from QuickBooks API (handles pagination)"""
        url = f"{self.BASE_URL}/v3/company/{self.company.realm_id}/query"
        all_invoices = []
        start_position = 1
        batch_size = 1000

        while True:
            query = f"SELECT * FROM Invoice STARTPOSITION {start_position} MAXRESULTS {batch_size}"
            print(f"🔹 Fetching invoices {start_position}–{start_position + batch_size - 1}")

            response = requests.get(
                url,
                headers=self.get_headers(),
                params={'query': query},
                timeout=30
            )
            response.raise_for_status()

            try:
                data = response.json()
                print(json.dumps(data, indent=2))  # Pretty-print JSON
            except ValueError:
                print("❌ Response is not valid JSON:")
                print(response.text)
                raise

            invoices = data.get("QueryResponse", {}).get("Invoice", [])
            if not invoices:
                break

            all_invoices.extend(invoices)
            print(f"✅ Retrieved {len(invoices)} invoices in this batch (total so far: {len(all_invoices)})")

            # Stop if fewer than 1000 were returned — means we’re at the end
            if len(invoices) < batch_size:
                break

            start_position += batch_size

        print(f"🎯 Finished fetching {len(all_invoices)} total invoices.")
        return all_invoices
    
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

        # 🧩 Extract template info if available
        template_ref = invoice_data.get("CustomTemplateRef", {})
        if template_ref:
            invoice.template_id = template_ref.get("value")
            invoice.template_name = template_ref.get("name")
            invoice.save(update_fields=["template_id", "template_name"])
            print(f"🧾 Invoice {invoice.doc_number} uses template: {invoice.template_name} ({invoice.template_id})")
        else:
            print(f"🧾 Invoice {invoice.doc_number} has no specific template (may use company default).")

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

        print(f"✅ Synced invoice {invoice.doc_number} ({'created' if created else 'updated'})")
        return invoice

    def sync_all_invoices(self) -> int:
        """Sync all invoices from QuickBooks to database"""
        invoices_data = self.fetch_invoices_from_qb()
        synced_count = 0
        
        for invoice_data in invoices_data:
            self.sync_invoice_to_db(invoice_data)
            synced_count += 1
        
        return synced_count

class QuickBooksCreditNoteService:
    """Service to fetch and sync credit notes (Credit Memos) from QuickBooks"""

    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
    # BASE_URL = "https://quickbooks.api.intuit.com"

    def __init__(self, company: Company):
        self.company = company
        if not company.is_connected:
            raise ValueError("Company is not connected to QuickBooks")

    def get_headers(self) -> Dict[str, str]:
        return {
            'Authorization': f'Bearer {self.company.access_token}',
            'Accept': 'application/json'
        }

    def fetch_credit_notes_from_qb(self) -> List[Dict[str, Any]]:
        """Fetch all credit notes from QuickBooks"""
        url = f"{self.BASE_URL}/v3/company/{self.company.realm_id}/query"
        all_credits = []
        start_position = 1
        batch_size = 1000

        while True:
            query = f"SELECT * FROM CreditMemo STARTPOSITION {start_position} MAXRESULTS {batch_size}"
            print(f"🔹 Fetching credit notes {start_position}–{start_position + batch_size - 1}")

            response = requests.get(
                url,
                headers=self.get_headers(),
                params={'query': query},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            credits = data.get("QueryResponse", {}).get("CreditMemo", [])
            if not credits:
                break

            all_credits.extend(credits)
            print(f"✅ Retrieved {len(credits)} credit notes (total so far: {len(all_credits)})")

            if len(credits) < batch_size:
                break

            start_position += batch_size

        print(f"🎯 Finished fetching {len(all_credits)} total credit notes.")
        return all_credits

    def sync_credit_note_to_db(self, credit_data: Dict) -> CreditNote:
        """Sync a single credit note"""
        related_invoice = None
        linked_txn = credit_data.get("LinkedTxn", [])
        if linked_txn:
            invoice_ref = next((t for t in linked_txn if t.get("TxnType") == "Invoice"), None)
            if invoice_ref:
                related_invoice = Invoice.objects.filter(
                    company=self.company,
                    qb_invoice_id=invoice_ref.get("TxnId")
                ).first()

        credit_note, created = CreditNote.objects.update_or_create(
            company=self.company,
            qb_credit_id=credit_data['Id'],
            defaults={
                'doc_number': credit_data.get('DocNumber'),
                'txn_date': datetime.strptime(credit_data['TxnDate'], '%Y-%m-%d').date(),
                'total_amt': credit_data.get('TotalAmt', 0),
                'balance': credit_data.get('Balance', 0),
                'customer_ref_value': credit_data.get('CustomerRef', {}).get('value'),
                'customer_name': credit_data.get('CustomerRef', {}).get('name'),
                'private_note': credit_data.get('PrivateNote'),
                'customer_memo': credit_data.get('CustomerMemo', {}).get('value'),
                'sync_token': credit_data.get('SyncToken'),
                'related_invoice': related_invoice,
                'raw_data': credit_data
            }
        )

        template_ref = credit_data.get("CustomTemplateRef", {})
        if template_ref:
            credit_note.template_id = template_ref.get("value")
            credit_note.template_name = template_ref.get("name")
            credit_note.save(update_fields=["template_id", "template_name"])

        if not created:
            credit_note.line_items.all().delete()

        for line_data in credit_data.get('Line', []):
            if line_data.get('DetailType') == 'SalesItemLineDetail':
                detail = line_data.get('SalesItemLineDetail', {})
                CreditNoteLine.objects.create(
                    credit_note=credit_note,
                    line_num=line_data.get('LineNum', 0),
                    item_ref_value=detail.get('ItemRef', {}).get('value'),
                    item_name=detail.get('ItemRef', {}).get('name'),
                    description=line_data.get('Description', ''),
                    qty=detail.get('Qty', 0),
                    unit_price=detail.get('UnitPrice', 0),
                    amount=line_data.get('Amount', 0),
                    raw_data=line_data
                )

        print(f"✅ Synced credit note {credit_note.doc_number or credit_note.qb_credit_id}")
        return credit_note

    def sync_all_credit_notes(self) -> int:
        """Fetch and sync all credit notes"""
        credits_data = self.fetch_credit_notes_from_qb()
        synced = 0
        for data in credits_data:
            self.sync_credit_note_to_db(data)
            synced += 1
        return synced
