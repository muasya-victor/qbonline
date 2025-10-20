from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'companies', views.CompanyViewSet, basename='company')
router.register(r'memberships', views.CompanyMembershipViewSet, basename='membership')
router.register(r'active-company', views.ActiveCompanyViewSet, basename='active-company')

urlpatterns = [
    path('', include(router.urls)),
]