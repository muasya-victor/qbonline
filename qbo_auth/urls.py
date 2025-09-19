from django.urls import path, include
from rest_framework.routers import DefaultRouter
router = DefaultRouter()

from .views import (
    QuickBooksAuthURLView,
    QuickBooksCallbackView,
    UserRegistrationView,
    UserCompaniesView,
    SetActiveCompanyView
)

urlpatterns = [
    path('register/', UserRegistrationView.as_view(), name='user-registration'),
    path('auth-url/', QuickBooksAuthURLView.as_view(), name='get-quickbooks-auth-url'),
    path('callback/', QuickBooksCallbackView.as_view(), name='quickbooks-callback'),
    path('companies/', UserCompaniesView.as_view(), name='user-companies'),
    path('companies/set-active/', SetActiveCompanyView.as_view(), name='set-active-company'),
    path('', include(router.urls)),
]
