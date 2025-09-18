from django.urls import path, include
from rest_framework.routers import DefaultRouter
router = DefaultRouter()

from .views import QuickBooksAuthURLView, QuickBooksCallbackView

urlpatterns = [
    path('auth-url/', QuickBooksAuthURLView.as_view(), name='get-quickbooks-auth-url'),
    path('callback/', QuickBooksCallbackView.as_view(), name='quickbooks-callback'),
    path('', include(router.urls)),
]
