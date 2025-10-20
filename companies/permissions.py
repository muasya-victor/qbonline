from rest_framework import permissions

class IsCompanyMember(permissions.BasePermission):
    """Check if user is a member of the company"""
    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'memberships'):
            return obj.memberships.filter(user=request.user).exists()
        return False

class IsCompanyAdmin(permissions.BasePermission):
    """Check if user is an admin of the company"""
    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'memberships'):
            return obj.memberships.filter(
                user=request.user, 
                role='admin'
            ).exists()
        return False