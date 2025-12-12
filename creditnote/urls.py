from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CreditNoteViewSet, generate_credit_note_pdf, credit_note_detail_html

router = DefaultRouter()
router.register(r'credit-notes', CreditNoteViewSet, basename='creditnote')

# Add URL patterns for custom actions that aren't auto-registered by the router
urlpatterns = [
    path('', include(router.urls)),
    path('credit-notes/pdf/<uuid:credit_note_id>/download/', generate_credit_note_pdf, name='credit-note-pdf'),
    path('credit-notes/pdf/<uuid:credit_note_id>/', credit_note_detail_html, name='credit-note-html'),
    
    # Existing custom actions
    path('credit-notes/smart-sync/', CreditNoteViewSet.as_view({'post': 'smart_sync_credit_notes'}), name='creditnote-smart-sync'),
    path('credit-notes/enhance-stub-customers/', CreditNoteViewSet.as_view({'post': 'enhance_stub_customers'}), name='creditnote-enhance-stub-customers'),
    path('credit-notes/analyze-customer-links/', CreditNoteViewSet.as_view({'get': 'analyze_customer_links'}), name='creditnote-analyze-customer-links'),
    
    # Existing invoice linking functionality
    path('credit-notes/available-invoices/', CreditNoteViewSet.as_view({'get': 'available_invoices'}), name='creditnote-available-invoices'),
    path('credit-notes/<uuid:pk>/update-related-invoice/', CreditNoteViewSet.as_view({'patch': 'update_related_invoice', 'put': 'update_related_invoice'}), name='creditnote-update-related-invoice'),
    path('credit-notes/<uuid:pk>/remove-related-invoice/', CreditNoteViewSet.as_view({'delete': 'remove_related_invoice'}), name='creditnote-remove-related-invoice'),
    
    path('credit-notes/validate-credit/', CreditNoteViewSet.as_view({'post': 'validate_credit_linkage'}), name='creditnote-validate-credit'),
    path('credit-notes/<uuid:pk>/validate-current/', CreditNoteViewSet.as_view({'get': 'validate_current_credit_note'}), name='creditnote-validate-current'),
    path('credit-notes/invoice-credit-summary/<uuid:invoice_id>/', CreditNoteViewSet.as_view({'get': 'invoice_credit_summary'}), name='creditnote-invoice-credit-summary'),
    path('credit-notes/fully-credited-invoices/', CreditNoteViewSet.as_view({'get': 'fully_credited_invoices'}), name='creditnote-fully-credited-invoices'),
]