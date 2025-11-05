# creditnote/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CreditNoteViewSet, generate_credit_note_pdf, credit_note_detail_html

router = DefaultRouter()
router.register(r'credit-notes', CreditNoteViewSet, basename='creditnote')

urlpatterns = [
    path('', include(router.urls)),
    path('credit-notes/<uuid:credit_note_id>/pdf/', generate_credit_note_pdf, name='credit-note-pdf'),
    path('credit-notes/<uuid:credit_note_id>/html/', credit_note_detail_html, name='credit-note-html'),
]