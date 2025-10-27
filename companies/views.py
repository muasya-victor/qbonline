from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import Company, CompanyMembership, ActiveCompany
from .serializers import (
    CompanySerializer, 
    CompanyCreateSerializer, 
    CompanyUpdateSerializer,
    CompanyMembershipSerializer,
    ActiveCompanySerializer
)
from users.models import User
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing companies.
    Users can only access companies they are members of.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser] 
    
    def get_queryset(self):
        """Return companies that the user is a member of"""
        return Company.objects.filter(
            memberships__user=self.request.user
        ).distinct().select_related('created_by')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return CompanyCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return CompanyUpdateSerializer
        return CompanySerializer
    
    def perform_create(self, serializer):
        """Create company and automatically add user as admin member"""
        company = serializer.save()
        
        # Create membership for the creator as admin
        CompanyMembership.objects.create(
            user=self.request.user,
            company=company,
            is_default=True,
            role='admin'
        )
        
        # Set as active company
        ActiveCompany.objects.update_or_create(
            user=self.request.user,
            defaults={'company': company}
        )
    
    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Add a member to the company"""
        company = self.get_object()
        
        # Check if current user is admin of the company
        if not company.memberships.filter(user=request.user, role='admin').exists():
            return Response(
                {'error': 'Only company admins can add members'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        user_email = request.data.get('user_email')
        if not user_email:
            return Response(
                {'error': 'user_email is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            user_to_add = User.objects.get(email=user_email)
        except User.DoesNotExist:
            return Response(
                {'error': 'User with this email does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if user is already a member
        if company.memberships.filter(user=user_to_add).exists():
            return Response(
                {'error': 'User is already a member of this company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        membership = CompanyMembership.objects.create(
            user=user_to_add,
            company=company,
            role=request.data.get('role', 'member'),
            is_default=request.data.get('is_default', False)
        )
        
        serializer = CompanyMembershipSerializer(membership)
        return Response({
            'success': True,
            'membership': serializer.data
        })

    @action(detail=True, methods=['post'])
    def refresh_info(self, request, pk=None):
        """Refresh company info from QuickBooks"""
        company = self.get_object()
        
        if not company.is_connected:
            return Response(
                {'error': 'Company is not connected to QuickBooks'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Your existing logic to refresh company info from QB
        # This would use the access token to fetch latest company info
        # and call company.update_company_info()
        
        return Response({
            'success': True,
            'message': 'Company info refreshed successfully',
            'company': CompanySerializer(company).data
        })

    
    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        """Disconnect from QuickBooks"""
        company = self.get_object()
        company.disconnect()
        return Response({
            'success': True, 
            'message': 'Company disconnected successfully'
        })
    
    @action(detail=False, methods=['get'])
    def my_companies(self, request):
        """Get companies with membership info for current user"""
        memberships = CompanyMembership.objects.filter(
            user=request.user
        ).select_related('company')
        
        serializer = CompanyMembershipSerializer(memberships, many=True)
        return Response(serializer.data)


class CompanyMembershipViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing company memberships.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CompanyMembershipSerializer
    
    def get_queryset(self):
        """Users can only see their own memberships"""
        return CompanyMembership.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        """Ensure user is set to current user"""
        serializer.save(user=self.request.user)


class ActiveCompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing active company.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ActiveCompanySerializer
    
    def get_queryset(self):
        return ActiveCompany.objects.filter(user=self.request.user)
    
    def get_object(self):
        """Get or create active company for user"""
        obj, created = ActiveCompany.objects.get_or_create(
            user=self.request.user
        )
        return obj
    
    @action(detail=False, methods=['post'])
    def set_active(self, request):
        """Set active company for user"""
        company_id = request.data.get('company_id')
        
        if not company_id:
            return Response(
                {'error': 'company_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify user has access to the company
        try:
            company = Company.objects.get(
                id=company_id,
                memberships__user=request.user
            )
        except Company.DoesNotExist:
            return Response(
                {'error': 'Company not found or access denied'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        active_company, created = ActiveCompany.objects.update_or_create(
            user=request.user,
            defaults={'company': company}
        )
        
        serializer = self.get_serializer(active_company)
        return Response(serializer.data)