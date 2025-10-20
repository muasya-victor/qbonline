from django.urls import path
from . import views

urlpatterns = [
    path('invoices/<uuid:invoice_id>/validate-kra/', views.validate_invoice_to_kra, name='validate_invoice_kra'),
    path('submissions/<uuid:submission_id>/status/', views.get_submission_status, name='submission_status'),
    path('companies/<uuid:company_id>/kra-submissions/', views.get_company_kra_submissions, name='company_kra_submissions'),
]