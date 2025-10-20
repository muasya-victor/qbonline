import os, json, requests, secrets, logging
from urllib.parse import urlencode
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, get_user_model
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from companies.models import Company, CompanyMembership, ActiveCompany
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
from typing import Dict
from django.db import IntegrityError

logger = logging.getLogger(__name__)
User = get_user_model()

# Endpoints - SANDBOX ENVIRONMENT (use all sandbox URLs)
AUTH_BASE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
# USERINFO_URL = "https://sandbox-accounts.platform.intuit.com/v1/openid_connect/userinfo"
# COMPANY_INFO_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/companyinfo/{realm_id}"
# PREFERENCES_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/preferences"

# PRODUCTION URLs (comment out when using sandbox)
USERINFO_URL = "https://accounts.platform.intuit.com/v1/openid_connect/userinfo"
COMPANY_INFO_URL = "https://quickbooks.api.intuit.com/v3/company/{realm_id}/companyinfo/{realm_id}"
PREFERENCES_URL = "https://quickbooks.api.intuit.com/v3/company/{realm_id}/preferences"


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
    Returns tokens only during initial login
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

        # Generate JWT tokens ONLY during initial login
        refresh = RefreshToken.for_user(user)
        jwt_tokens = {
            "refresh": str(refresh), 
            "access": str(refresh.access_token)
        }

        # Get user's default company (if any)
        company_details = get_default_company_by_email(email)

        # Generate OAuth URL for new connection
        auth_url = self._generate_oauth_url(request, scopes, user)
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
            "tokens": jwt_tokens,  # Tokens only returned during initial login
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
        print("🔄 Generating OAuth state:", state)

        # Store state in database for reliable persistence
        try:
            oauth_state = OAuthState.objects.create(
                state=state,
                user=user
            )
            print(f"✅ OAuth state created for user: {user.email} (ID: {user.id})")
            print(f"✅ State stored in DB with ID: {oauth_state.id}")
            
        except Exception as e:
            logger.error(f"Failed to create OAuth state: {e}")
            print(f"❌ ERROR creating OAuth state: {e}")
            return None

        # Build OAuth URL
        params = {
            "client_id": client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        
        auth_url = f"{AUTH_BASE_URL}?{urlencode(params)}"
        print(f"🔗 Generated OAuth URL with state: {state}")
        return auth_url


class QuickBooksCallbackView(APIView):
    """
    Class-based view for QuickBooks OAuth callback with authentication
    NO token generation here to avoid session conflicts
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        print("🚀 === QUICKBOOKS CALLBACK STARTED ===")
        
        data = request.data
        auth_code = data.get("code")
        realm_id = data.get("realmId")
        returned_state = data.get("state")

        print(f"👤 Callback received for user: {request.user.id} - {request.user.email}")
        print(f"📨 Callback data - code: {auth_code[:10] if auth_code else 'None'}..., realm_id: {realm_id}, state: {returned_state}")

        if not auth_code:
            logger.error("Missing authorization code in callback")
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

        # FIRST: Check if this state was already used (duplicate request)
        try:
            used_state = OAuthState.objects.get(
                state=returned_state,
                user=request.user,
                used=True
            )
            print(f"🔄 Duplicate request detected - state already used at: {used_state.created_at}")
            # If it was already used successfully, return success
            return Response({
                "success": True,
                "message": "OAuth flow already completed successfully.",
                "duplicate": True
            })
        except OAuthState.DoesNotExist:
            # Continue with normal flow
            pass

        # Validate state using database
        try:
            oauth_state = OAuthState.objects.get(
                state=returned_state,
                user=request.user,
                used=False
            )
            print(f"✅ State validation SUCCESS - found state ID: {oauth_state.id}")
            print(f"✅ State created at: {oauth_state.created_at}")
            print(f"✅ State is valid: {oauth_state.is_valid()}")

            if not oauth_state.is_valid():
                logger.warning(f"Expired OAuth state for user {request.user.id}: {returned_state[:8]}...")
                return Response(
                    {"success": False, "error": "OAuth state expired. Please try logging in again."},
                    status=status.HTTP_403_FORBIDDEN
                )

            # DON'T mark as used yet - wait until entire flow completes
            logger.info(f"Successfully validated OAuth state for user {request.user.id}: {returned_state[:8]}...")

        except OAuthState.DoesNotExist:
            print(f"❌ State validation FAILED - state not found in database")
            print(f"❌ User {request.user.id} looked for state: {returned_state}")
            
            # Check what states are available for this user
            user_states = OAuthState.objects.filter(user=request.user, used=False)
            available_states = list(user_states.values_list('state', flat=True))
            print(f"❌ Available states for this user: {available_states}")
            
            logger.error(f"❌ State validation FAILED - state not found in database")
            logger.error(f"User {request.user.id} looked for state: {returned_state}")
            logger.error(f"Available states for this user: {available_states}")
            return Response(
                {"success": False, "error": "Invalid OAuth state. Please try logging in again."},
                status=status.HTTP_403_FORBIDDEN
            )

        client_id = os.environ.get("QBO_CLIENT_ID")
        client_secret = os.environ.get("QBO_CLIENT_SECRET")
        redirect_uri = os.environ.get("QBO_REDIRECT_URI_FRONTEND")

        # DEBUG: Log OAuth configuration (mask secrets)
        print(f"🔧 OAuth config - Client ID: {client_id[:10]}..., Redirect URI: {redirect_uri}")

        try:
            print("🔄 Starting token exchange with Intuit...")
            logger.info("Starting token exchange with Intuit...")
            
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
            
            print(f"📨 Token exchange response status: {token_response.status_code}")
            logger.info(f"Token exchange response status: {token_response.status_code}")
            
            token_response.raise_for_status()
            
            tokens = token_response.json()
            print(f"✅ Token exchange successful for realm {realm_id}")
            logger.info(f"✅ Token exchange successful for realm {realm_id}")
            
        except requests.RequestException as e:
            logger.error(f"❌ Token exchange failed: {str(e)}")
            
            # ADD DETAILED DEBUGGING:
            if hasattr(e, 'response') and e.response is not None:
                print(f"❌ Intuit response status: {e.response.status_code}")
                print(f"❌ Intuit response body: {e.response.text}")
                logger.error(f"Intuit response status: {e.response.status_code}")
                logger.error(f"Intuit response body: {e.response.text}")
                
                # Try to parse JSON error response
                try:
                    error_data = e.response.json()
                    print(f"❌ Intuit error details: {error_data}")
                    logger.error(f"Intuit error details: {error_data}")
                except:
                    print("❌ Intuit error response is not JSON")
                    logger.error("Intuit error response is not JSON")
            else:
                print("❌ No response received from Intuit")
                logger.error("No response received from Intuit")
            
            # Don't mark state as used - let user retry
            return Response(
                {"success": False, "error": f"Token exchange failed: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Handle company creation with transaction to prevent race conditions
        from django.db import transaction
        
        try:
            with transaction.atomic():
                # First, try to get existing company for this user with this realm_id
                company = Company.objects.filter(
                    realm_id=realm_id,
                    memberships__user=request.user
                ).first()
                
                created = False
                
                if not company:
                    # If no company exists for this user, check if company exists for other users
                    existing_company = Company.objects.filter(realm_id=realm_id).first()
                    
                    if existing_company:
                        # Company exists but user doesn't have access - add them as member
                        company = existing_company
                        created = False
                        logger.info(f"User added to existing company: {company.id}")
                    else:
                        # Create new company
                        company = Company.objects.create(
                            realm_id=realm_id,
                            name=f"Company-{realm_id}",
                            created_by=request.user
                        )
                        created = True
                        logger.info(f"Created new company: {company.id}")
                        
        except Company.MultipleObjectsReturned:
            # Handle the case where there are multiple companies with same realm_id
            logger.warning(f"Multiple companies found for realm_id {realm_id}, using the first one")
            company = Company.objects.filter(realm_id=realm_id).first()
            created = False
        
        # Save QuickBooks tokens
        company.mark_connected(tokens)
        logger.info(f"Tokens saved for company {company.id}: access_token={'***' if company.access_token else 'None'}")

        # Test API access before making other calls
        access_token = tokens.get("access_token")
        api_access_ok = False

        # Try to fetch company information (but don't fail the entire flow if this fails)
        try:
            self._fetch_and_store_company_info(company, access_token)
            self._fetch_and_store_company_preferences(company, access_token)
        except Exception as e:
            logger.warning(f"Company info fetch failed, but continuing: {str(e)}")

        # Create or update membership
        membership, membership_created = CompanyMembership.objects.get_or_create(
            user=request.user,
            company=company,
            defaults={"is_default": True, "role": "admin"},
        )
        logger.info(f"Membership {'created' if membership_created else 'updated'} for user {request.user.id}")

        # Set Active Company
        active_company, active_created = ActiveCompany.objects.update_or_create(
            user=request.user,
            defaults={"company": company}
        )
        logger.info(f"Active company {'created' if active_created else 'updated'} for user {request.user.id}")

        user_info = None
        if access_token:
            try:
                user_response = requests.get(
                    USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30,
                )
                
                logger.info(f"User info fetch response: {user_response.status_code}")

                if user_response.status_code == 200:
                    user_info = user_response.json()
                    logger.info("User info fetched successfully")
                elif user_response.status_code == 403:
                    logger.warning("User info access forbidden - check OpenID Connect permissions")
                else:
                    logger.warning(f"User info fetch returned {user_response.status_code}")
            except requests.RequestException as e:
                logger.error(f"Failed to fetch user info: {str(e)}")
                user_info = {"error": "Failed to fetch user info"}

        # ✅ ONLY NOW mark the state as used - after successful completion
        oauth_state.mark_used()
        print(f"✅ OAuth state marked as used and OAuth flow completed successfully")
        logger.info(f"✅ OAuth state marked as used and OAuth flow completed successfully")

        # Return success even if some API calls failed
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
            "api_access_ok": api_access_ok  # Let frontend know about API access status
        })

    def _fetch_and_store_company_preferences(self, company, access_token):
        """
        Fetch QuickBooks preferences to get default invoice template name and ID
        """
        if not access_token or not company.realm_id:
            logger.warning("Missing access_token or realm_id for company preferences fetch")
            return

        try:
            url = PREFERENCES_URL.format(realm_id=company.realm_id)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
            response = requests.get(url, headers=headers, timeout=30)
            
            logger.info(f"Preferences fetch response: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()

                prefs = data.get("Preferences", {})
                sales_prefs = prefs.get("SalesFormsPrefs", {})
                template_ref = sales_prefs.get("DefaultInvoiceTemplateRef", {})

                if template_ref:
                    company.invoice_template_id = template_ref.get("value")
                    company.invoice_template_name = template_ref.get("name")
                    company.save(update_fields=["invoice_template_id", "invoice_template_name"])
                    logger.info(
                        f"Stored invoice template for {company.realm_id}: "
                        f"{company.invoice_template_name} ({company.invoice_template_id})"
                    )
                else:
                    logger.info(f"No DefaultInvoiceTemplateRef found for company {company.realm_id}")
            elif response.status_code == 403:
                logger.error(f"Access forbidden for company preferences - check app permissions")
            else:
                logger.error(f"Failed to fetch preferences: {response.status_code}")

        except requests.RequestException as e:
            logger.error(f"Error fetching preferences from QuickBooks: {str(e)}")

    def _fetch_and_store_company_info(self, company, access_token):
        """
        Fetch company information from QuickBooks API and store it
        """
        if not access_token or not company.realm_id:
            logger.warning("Missing access_token or realm_id for company info fetch")
            return

        try:
            company_info_url = COMPANY_INFO_URL.format(realm_id=company.realm_id)
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }

            response = requests.get(company_info_url, headers=headers, timeout=30)

            logger.info(f"Company info fetch response: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                company_info = data.get("CompanyInfo")  # Direct access for companyinfo endpoint
                
                if company_info:
                    # Update company with QB info
                    company.update_company_info(company_info)
                    logger.info(f"Company info updated for {company.realm_id}: {company.qb_company_name}")
                else:
                    logger.warning(f"No company info found in QB response for realm {company.realm_id}")
            elif response.status_code == 403:
                logger.error(f"Access forbidden for company info - check app permissions and environment")
                logger.error(f"Make sure your app is properly configured in Intuit Developer Portal")
            elif response.status_code == 401:
                logger.error(f"Unauthorized for company info - token may be invalid or expired")
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