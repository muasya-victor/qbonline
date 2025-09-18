import os, json, requests, secrets, logging
from urllib.parse import urlencode
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from users.models import Company, CompanyMembership, ActiveCompany
from .utils import get_default_company_by_email
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# Endpoints
AUTH_BASE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
USERINFO_URL = "https://sandbox-accounts.platform.intuit.com/v1/openid_connect/userinfo"



class QuickBooksAuthURLView(APIView):
    """
    Generate QuickBooks OAuth URL after user authentication
    """
    
    def post(self, request):
        """
        Authenticate user and generate QB OAuth URL if company not connected
        """
        # Parse request data
        data = request.data
        email = data.get("email")
        password = data.get("password")
        scopes = data.get(
            "scopes",
            "com.intuit.quickbooks.accounting openid profile email phone address"
        )

        # Validate required fields
        if not email or not password:
            return Response(
                {"success": False, "error": "Email and password are required."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Authenticate user
        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"success": False, "error": "Invalid email or password."}, 
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)
        jwt_tokens = {
            "refresh": str(refresh), 
            "access": str(refresh.access_token)
        }

        # Get user's default company
        company_details = get_default_company_by_email(email)
        if not company_details:
            return Response(
                {"success": False, "error": "No default company found for this user."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # Set/update active company
        try:
            company = Company.objects.get(id=company_details["company_id"])
            active_company, created = ActiveCompany.objects.update_or_create(
                user=user,
                defaults={"company": company}
            )
            logger.info(f"ActiveCompany {'created' if created else 'updated'}: {active_company}")
        except Company.DoesNotExist:
            logger.warning(f"Company {company_details['company_id']} not found in DB")
            return Response(
                {"success": False, "error": "Company not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if already connected
        if company_details.get("connection_status") == "connected":
            return Response({
                "success": True,
                "company": company_details,
                "is_connected": True,
                "authUrl": None,
                "tokens": jwt_tokens
            })

        # Generate OAuth URL for connection
        auth_url = self._generate_oauth_url(request, scopes)
        if not auth_url:
            return Response(
                {"success": False, "error": "Server configuration error."}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            "success": True,
            "company": company_details,
            "is_connected": False,
            "authUrl": auth_url,
            "tokens": jwt_tokens
        })

    def _generate_oauth_url(self, request, scopes):
        """
        Generate QuickBooks OAuth URL with state parameter
        """
        client_id = os.environ.get("QBO_CLIENT_ID")
        redirect_uri = os.environ.get("QBO_REDIRECT_URI_FRONTEND")

        if not client_id or not redirect_uri:
            logger.error("QuickBooks config missing: QBO_CLIENT_ID or QBO_REDIRECT_URI_FRONTEND not set.")
            return None

        # Generate and store state
        state = secrets.token_urlsafe(32)
        if not request.session.session_key:
            request.session.create()
        request.session["quickbooks_oauth_state"] = state
        request.session.save()

        # Build OAuth URL
        params = {
            "client_id": client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        
        return f"{AUTH_BASE_URL}?{urlencode(params)}"

class QuickBooksCallbackView(APIView):
    """
    Class-based view for QuickBooks OAuth callback with authentication
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        data = request.data
        
        auth_code = data.get("code")
        realm_id = data.get("realmId")
        returned_state = data.get("state")
        expected_state = request.session.get("quickbooks_oauth_state")

        if not auth_code:
            return Response(
                {"success": False, "error": "Missing authorization code."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        if not returned_state or returned_state != expected_state:
            return Response(
                {"success": False, "error": "State mismatch. Possible CSRF attack."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        # Clean up session state
        if "quickbooks_oauth_state" in request.session:
            del request.session["quickbooks_oauth_state"]
            request.session.save()

        client_id = os.environ.get("QBO_CLIENT_ID")
        client_secret = os.environ.get("QBO_CLIENT_SECRET")
        redirect_uri = os.environ.get("QBO_REDIRECT_URI_FRONTEND")

        try:
            token_response = requests.post(
                TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
                timeout=30,
            )
            token_response.raise_for_status()
            tokens = token_response.json()
        except requests.RequestException as e:
            return Response(
                {"success": False, "error": f"Token exchange failed: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Authenticated user context
        company, created = Company.objects.get_or_create(
            realm_id=realm_id,
            defaults={"name": f"Company-{realm_id}", "created_by": request.user},
        )
        
        # Save QuickBooks tokens
        company.mark_connected(tokens)
        logger.info(f"Tokens saved for company {company.id}: access_token={'***' if company.access_token else 'None'}")

        membership, _ = CompanyMembership.objects.get_or_create(
            user=request.user,
            company=company,
            defaults={"is_default": True, "role": "admin"},
        )

        # Set Active Company
        ActiveCompany.objects.update_or_create(
            user=request.user,
            defaults={"company": company}
        )

        # Generate JWT tokens
        refresh = RefreshToken.for_user(request.user)
        jwt_tokens = {
            "refresh": str(refresh),
            "access": str(refresh.access_token)
        }

        user_info = None
        access_token = tokens.get("access_token")
        if access_token:
            try:
                user_response = requests.get(
                    USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30,
                )
                if user_response.status_code == 200:
                    user_info = user_response.json()
            except requests.RequestException:
                user_info = {"error": "Failed to fetch user info"}

        return Response({
            "success": True,
            "company": {
                "id": str(company.id),
                "name": company.name,
                "realm_id": company.realm_id,
                "is_connected": company.is_connected,
            },
            "membership": {"is_default": membership.is_default, "role": membership.role},
            "active_company": str(company.id),
            "user": user_info,
            "tokens": jwt_tokens
        })