# invoices/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet,invoice_detail,generate_invoice_pdf

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoices')

urlpatterns = [
    path('', include(router.urls)),
    path('invoices/pdf/<uuid:invoice_id>/', invoice_detail, name='invoice_detail'),
    path('invoices/pdf/<uuid:invoice_id>/download/', generate_invoice_pdf, name='generate_invoice_pdf'),
]
