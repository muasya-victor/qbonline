import logging
from typing import Dict, Tuple
from companies.models import Company
from customers.services import QuickBooksCustomerService
from invoices.services import QuickBooksInvoiceService
from customers.models import Customer

logger = logging.getLogger(__name__)

class QuickBooksSyncOrchestrator:
    """Orchestrates the sync process to ensure proper order and handle all scenarios"""
    
    def __init__(self, company: Company):
        self.company = company
        self.customer_service = QuickBooksCustomerService(company)
        self.invoice_service = QuickBooksInvoiceService(company)
    
    def sync_all_data(self) -> Dict[str, any]:
        """Sync all data in the correct order - the recommended approach"""
        results = {}
        
        logger.info("=== Starting complete QuickBooks data sync ===")
        
        # Step 1: Sync customers first
        logger.info("Step 1: Syncing customers...")
        customer_success, customer_failed = self.customer_service.sync_all_customers()
        results['customers'] = {
            'success': customer_success,
            'failed': customer_failed
        }
        
        # Step 2: Sync invoices (they can now link to existing customers)
        logger.info("Step 2: Syncing invoices...")
        invoice_success, invoice_failed = self.invoice_service.sync_all_invoices()
        results['invoices'] = {
            'success': invoice_success,
            'failed': invoice_failed
        }
        
        # Step 3: Link any invoices that might have been missed
        logger.info("Step 3: Linking invoices to customers...")
        linked_count, link_failed = self.invoice_service.link_existing_invoices_to_customers()
        results['linking'] = {
            'linked': linked_count,
            'failed': link_failed
        }
        
        total_success = customer_success + invoice_success
        total_failed = customer_failed + invoice_failed
        
        logger.info(f"=== Sync completed ===")
        logger.info(f"Customers: {customer_success} successful, {customer_failed} failed")
        logger.info(f"Invoices: {invoice_success} successful, {invoice_failed} failed") 
        logger.info(f"Linking: {linked_count} linked, {link_failed} failed")
        logger.info(f"Total: {total_success} successful, {total_failed} failed")
        
        results['summary'] = {
            'total_success': total_success,
            'total_failed': total_failed
        }
        
        return results
    
    def sync_invoices_only(self) -> Dict[str, any]:
        """Sync invoices only, handling missing customers gracefully"""
        results = {}
        
        logger.info("=== Starting invoice-only sync ===")
        
        # Sync invoices (with robust customer handling)
        logger.info("Syncing invoices...")
        invoice_success, invoice_failed = self.invoice_service.sync_all_invoices()
        results['invoices'] = {
            'success': invoice_success,
            'failed': invoice_failed
        }
        
        # Try to link any that have customers
        logger.info("Linking invoices to existing customers...")
        linked_count, link_failed = self.invoice_service.link_existing_invoices_to_customers()
        results['linking'] = {
            'linked': linked_count,
            'failed': link_failed
        }
        
        logger.info(f"=== Invoice-only sync completed ===")
        logger.info(f"Invoices: {invoice_success} successful, {invoice_failed} failed")
        logger.info(f"Linking: {linked_count} linked, {link_failed} failed")
        
        return results
    
    def sync_customers_only(self) -> Dict[str, any]:
        """Sync customers only"""
        results = {}
        
        logger.info("=== Starting customer-only sync ===")
        
        customer_success, customer_failed = self.customer_service.sync_all_customers()
        results['customers'] = {
            'success': customer_success,
            'failed': customer_failed
        }
        
        logger.info(f"=== Customer-only sync completed ===")
        logger.info(f"Customers: {customer_success} successful, {customer_failed} failed")
        
        return results
    
    def sync_customer_with_invoices(self, customer_qb_id: str) -> Dict[str, any]:
        """Sync a specific customer and all their invoices"""
        results = {}
        
        logger.info(f"=== Syncing customer {customer_qb_id} and their invoices ===")
        
        try:
            # First, ensure the customer exists locally
            customer = None
            try:
                customer = Customer.objects.get(company=self.company, qb_customer_id=customer_qb_id)
                logger.info(f"Found existing customer: {customer.display_name}")
            except Customer.DoesNotExist:
                logger.warning(f"Customer {customer_qb_id} not found locally. You may need to sync all customers first.")
                return {'error': f'Customer {customer_qb_id} not found locally'}
            
            # Sync this customer's invoices
            invoice_success, invoice_failed = self.customer_service.sync_customer_invoices(customer)
            results['invoices'] = {
                'success': invoice_success,
                'failed': invoice_failed
            }
            
            logger.info(f"=== Customer invoice sync completed ===")
            logger.info(f"Invoices for {customer.display_name}: {invoice_success} successful, {invoice_failed} failed")
            
        except Exception as e:
            logger.error(f"Failed to sync customer {customer_qb_id} and invoices: {str(e)}")
            results['error'] = str(e)
        
        return results