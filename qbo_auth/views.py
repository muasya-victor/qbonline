import os, json, requests, secrets, logging
from urllib.parse import urlencode
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, get_user_model
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from users.models import Company, CompanyMembership, ActiveCompany
from .models import OAuthState
from .utils import get_default_company_by_email
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json
from rest_framework.views import APIView
from django.db import IntegrityError

logger = logging.getLogger(__name__)
User = get_user_model()

# Endpoints
AUTH_BASE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
USERINFO_URL = "https://sandbox-accounts.platform.intuit.com/v1/openid_connect/userinfo"
COMPANY_INFO_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/companyinfo/{company_id}"


class UserRegistrationView(APIView):
    """
    User registration endpoint
    """

    def post(self, request):
        """
        Register a new user with email and password
        """
        data = request.data
        email = data.get("email")
        password = data.get("password")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")

        # Validate required fields
        if not email or not password:
            return Response(
                {"success": False, "error": "Email and password are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate password length
        if len(password) < 8:
            return Response(
                {"success": False, "error": "Password must be at least 8 characters long."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Create user
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name
            )

            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            jwt_tokens = {
                "refresh": str(refresh),
                "access": str(refresh.access_token)
            }

            logger.info(f"New user registered: {email}")

            return Response({
                "success": True,
                "message": "User registered successfully. Please connect your QuickBooks company.",
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name
                },
                "tokens": jwt_tokens
            }, status=status.HTTP_201_CREATED)

        except IntegrityError:
            return Response(
                {"success": False, "error": "A user with this email already exists."},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            logger.error(f"User registration failed: {str(e)}")
            return Response(
                {"success": False, "error": "Registration failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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

        # Get user's default company (if any)
        company_details = get_default_company_by_email(email)

        # If user has a connected company, return it
        if company_details and company_details.get("connection_status") == "connected":
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
                company_details = None

            if company_details:
                return Response({
                    "success": True,
                    "company": company_details,
                    "is_connected": True,
                    "authUrl": None,
                    "tokens": jwt_tokens
                })

        # Generate OAuth URL for new connection (no company or disconnected company)
        auth_url = self._generate_oauth_url(request, scopes, user)
        if not auth_url:
            return Response(
                {"success": False, "error": "Server configuration error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            "success": True,
            "company": company_details,  # Will be None for new users
            "is_connected": False,
            "authUrl": auth_url,
            "tokens": jwt_tokens,
            "message": "Please connect your QuickBooks company to continue."
        })

    def _generate_oauth_url(self, request, scopes, user):
        """
        Generate QuickBooks OAuth URL with state parameter
        """
        client_id = os.environ.get("QBO_CLIENT_ID")
        redirect_uri = os.environ.get("QBO_REDIRECT_URI_FRONTEND")

        if not client_id or not redirect_uri:
            logger.error("QuickBooks config missing: QBO_CLIENT_ID or QBO_REDIRECT_URI_FRONTEND not set.")
            return None

        # Generate and store state in database
        state = secrets.token_urlsafe(32)

        # Clean up any expired states first
        OAuthState.cleanup_expired()

        # Store state in database for reliable persistence
        try:
            oauth_state = OAuthState.objects.create(
                state=state,
                user=user
            )
            logger.info(f"Generated OAuth state: {state[:8]}... for user: {user.id}, stored in DB with ID: {oauth_state.id}")
        except Exception as e:
            logger.error(f"Failed to create OAuth state: {e}")
            return None

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

        if not auth_code:
            return Response(
                {"success": False, "error": "Missing authorization code."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not returned_state:
            logger.warning(f"Missing state parameter in OAuth callback for user {request.user.id}")
            return Response(
                {"success": False, "error": "Missing state parameter. Please try again."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Clean up expired states first
        OAuthState.cleanup_expired()

        # Validate state using database
        try:
            oauth_state = OAuthState.objects.get(
                state=returned_state,
                user=request.user,
                used=False
            )

            if not oauth_state.is_valid():
                logger.warning(f"Expired OAuth state for user {request.user.id}: {returned_state[:8]}...")
                return Response(
                    {"success": False, "error": "OAuth state expired. Please try logging in again."},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Mark state as used to prevent replay attacks
            oauth_state.mark_used()
            logger.info(f"Successfully validated OAuth state for user {request.user.id}: {returned_state[:8]}...")

        except OAuthState.DoesNotExist:
            logger.warning(f"Invalid OAuth state for user {request.user.id}: {returned_state[:8]}... - not found in database")
            return Response(
                {"success": False, "error": "Invalid OAuth state. Please try logging in again."},
                status=status.HTTP_403_FORBIDDEN
            )

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

        # Fetch and store company information from QuickBooks
        self._fetch_and_store_company_info(company, tokens.get("access_token"))

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

    def _fetch_and_store_company_info(self, company, access_token):
        """
        Fetch company information from QuickBooks API and store it
        """
        if not access_token or not company.realm_id:
            logger.warning("Missing access_token or realm_id for company info fetch")
            return

        try:
            # First, get the company info ID (usually "1" for the main company)
            company_info_list_url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{company.realm_id}/companyinfo/1"

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }

            response = requests.get(company_info_list_url, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                company_info = data.get("QueryResponse", {}).get("CompanyInfo", [])

                if company_info and len(company_info) > 0:
                    # Update company with QB info
                    company.update_company_info(company_info[0])
                    logger.info(f"Company info updated for {company.realm_id}: {company.qb_company_name}")
                else:
                    logger.warning(f"No company info found in QB response for realm {company.realm_id}")
            else:
                logger.error(f"Failed to fetch company info: {response.status_code} - {response.text}")

        except requests.RequestException as e:
            logger.error(f"Error fetching company info from QuickBooks: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error processing company info: {str(e)}")


class UserCompaniesView(APIView):
    """
    List all companies for the authenticated user
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Get all companies the user has access to
        """
        user_memberships = CompanyMembership.objects.filter(
            user=request.user
        ).select_related('company')

        companies = []
        active_company_id = None

        # Get user's active company
        try:
            active_company = ActiveCompany.objects.get(user=request.user)
            active_company_id = str(active_company.company.id)
        except ActiveCompany.DoesNotExist:
            pass

        for membership in user_memberships:
            company = membership.company
            companies.append({
                "id": str(company.id),
                "name": company.name,
                "realm_id": company.realm_id,
                "is_connected": company.is_connected,
                "is_default": membership.is_default,
                "role": membership.role,
                "is_active": str(company.id) == active_company_id,
                "created_at": company.created_at.isoformat() if hasattr(company, 'created_at') else None
            })

        return Response({
            "success": True,
            "companies": companies,
            "active_company_id": active_company_id
        })


class SetActiveCompanyView(APIView):
    """
    Set the active company for the authenticated user
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        Set the active company for the user
        """
        data = request.data
        company_id = data.get("company_id")

        if not company_id:
            return Response(
                {"success": False, "error": "Company ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify user has access to this company
        try:
            membership = CompanyMembership.objects.get(
                user=request.user,
                company_id=company_id
            )
            company = membership.company
        except CompanyMembership.DoesNotExist:
            return Response(
                {"success": False, "error": "You don't have access to this company."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Set as active company
        active_company, created = ActiveCompany.objects.update_or_create(
            user=request.user,
            defaults={"company": company}
        )

        logger.info(f"User {request.user.email} switched to company {company.name}")

        return Response({
            "success": True,
            "message": f"Active company set to {company.name}",
            "active_company": {
                "id": str(company.id),
                "name": company.name,
                "realm_id": company.realm_id,
                "is_connected": company.is_connected,
                "role": membership.role
            }
        })