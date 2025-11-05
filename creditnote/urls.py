# creditnote/urls.py
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
    
    # Add these for your custom actions
    path('credit-notes/smart-sync/', CreditNoteViewSet.as_view({'post': 'smart_sync_invoices'}), name='creditnote-smart-sync'),
    path('credit-notes/enhance-stub-customers/', CreditNoteViewSet.as_view({'post': 'enhance_stub_customers'}), name='creditnote-enhance-stub-customers'),
    path('credit-notes/analyze-customer-links/', CreditNoteViewSet.as_view({'get': 'analyze_customer_links'}), name='creditnote-analyze-customer-links'),
]