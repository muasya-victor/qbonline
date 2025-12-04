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
import requests
from urllib.parse import quote

from project.settings_qbo import (
    BASE_URL,
    QBO_ENVIRONMENT,
    QBO_CLIENT_ID,
    QBO_REDIRECT_URI_FRONTEND,
    AUTH_BASE_URL,
    TOKEN_URL,
    USERINFO_URL,
    COMPANY_INFO_URL,
    QBO_CLIENT_ID,
    QBO_CLIENT_SECRET,
    QBO_REDIRECT_URI,
    QBO_REDIRECT_URI_FRONTEND,
    PREFERENCES_URL
)


logger = logging.getLogger(__name__)
User = get_user_model()


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
        client_id = QBO_CLIENT_ID
        redirect_uri = QBO_REDIRECT_URI_FRONTEND

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
            "redirect_uri": "https://prod.v2.smartinvoice.co.ke/qbo/callback",
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

        client_id = QBO_CLIENT_ID
        client_secret = QBO_CLIENT_SECRET
        redirect_uri = QBO_REDIRECT_URI_FRONTEND

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
                
                # Basic company info
                "qb_company_name": company.qb_company_name,
                "qb_legal_name": company.qb_legal_name,
                "country": company.qb_country,
                "email": company.qb_email,
                "phone": company.qb_phone,
                "website": company.qb_website,
                "address": company.qb_address,
                
                # Currency and financial settings
                "currency_code": company.currency_code,
                "tax_enabled": company.tax_enabled,
                "tax_calculation": company.tax_calculation,
                
                # Invoice and branding
                "invoice_template_id": company.invoice_template_id,
                "invoice_template_name": company.invoice_template_name,
                "invoice_logo_enabled": company.invoice_logo_enabled,
                "brand_color": company.brand_color,
                "logo_url": company.logo_url,
                "kra_pin": company.kra_pin,
                
                # Features
                "multi_currency_enabled": company.multi_currency_enabled,
                "time_tracking_enabled": company.time_tracking_enabled,
                
                # Additional metadata
                "fiscal_year_start": company.qb_fiscal_year_start,
                "supported_languages": company.qb_supported_languages,
                "company_type": company.company_type,
            },
            "membership": {"is_default": membership.is_default, "role": membership.role},
            "active_company": str(company.id),
            "user": user_info,
            "api_access_ok": api_access_ok  # Let frontend know about API access status
        })

    def _fetch_and_store_company_info(self, company, access_token):
        """
        Fetch basic company information from QuickBooks CompanyInfo API
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
                company_info = data.get("CompanyInfo")
                
                if company_info:
                    # Update company with basic QB info (no logo here)
                    self._update_company_basic_info(company, company_info)
                    logger.info(f"Company basic info updated for {company.realm_id}: {company.qb_company_name}")
                else:
                    logger.warning(f"No company info found in QB response for realm {company.realm_id}")
            elif response.status_code == 403:
                logger.error(f"Access forbidden for company info - check app permissions and environment")
            elif response.status_code == 401:
                logger.error(f"Unauthorized for company info - token may be invalid or expired")
            else:
                logger.error(f"Failed to fetch company info: {response.status_code} - {response.text}")

        except requests.RequestException as e:
            logger.error(f"Error fetching company info from QuickBooks: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error processing company info: {str(e)}")

    def _update_company_basic_info(self, company, company_info: dict):
        """
        Update company with basic information from QuickBooks CompanyInfo API response,
        including logo if available.
        """
        if not company_info:
            return

        # Extract data from QB response
        company.qb_company_info = company_info
        company.qb_company_name = company_info.get("CompanyName")
        company.qb_legal_name = company_info.get("LegalName")
        company.qb_country = company_info.get("Country")
        company.qb_fiscal_year_start = company_info.get("FiscalYearStartMonth")
        company.qb_supported_languages = company_info.get("SupportedLanguages")
        company.qb_name_value = company_info.get("Name")
        company.company_type = company_info.get("CompanyType")

        # Handle start date
        company_start_date = company_info.get("CompanyStartDate")
        if company_start_date:
            from datetime import datetime
            try:
                if isinstance(company_start_date, str):
                    company.company_start_date = datetime.strptime(company_start_date, '%Y-%m-%d').date()
                else:
                    company.company_start_date = company_start_date
            except (ValueError, TypeError):
                logger.warning(f"Could not parse company start date: {company_start_date}")

        company.ein = company_info.get("EIN")

        # Address info
        company.qb_address = company_info.get("CompanyAddr")
        company.customer_communication_addr = company_info.get("CustomerCommunicationAddr")
        company.legal_addr = company_info.get("LegalAddr")

        # Contact info
        email = company_info.get("Email")
        if email and email.get("Address"):
            company.qb_email = email["Address"]

        phone = company_info.get("PrimaryPhone")
        if phone and phone.get("FreeFormNumber"):
            company.qb_phone = phone["FreeFormNumber"]

        website = company_info.get("WebAddr")
        if website and website.get("URI"):
            company.qb_website = website["URI"]

        # ✅ New: Fetch logo directly from CompanyLogoRef (supported and reliable)
        logo_ref = company_info.get("CompanyLogoRef")
        if logo_ref and logo_ref.get("Value"):
            company.logo_url = logo_ref["Value"]
            logger.info(f"✅ Company logo found in CompanyInfo: {company.logo_url}")

        # Use QB company name if name is default
        if company.qb_company_name and company.name == "default":
            company.name = company.qb_company_name

        company.save(update_fields=[
            "name", "qb_company_name", "qb_legal_name", "qb_country", "qb_address",
            "qb_phone", "qb_email", "qb_website", "qb_fiscal_year_start", 
            "qb_supported_languages", "qb_name_value", "qb_company_info", 
            "company_type", "company_start_date", "ein", "customer_communication_addr", 
            "legal_addr", "logo_url"
        ])

    def _fetch_and_store_company_preferences(self, company, access_token):
        """
        Fetch comprehensive QuickBooks preferences including logo, currency, and invoice settings
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
                preferences = data.get("Preferences", {})
                
                # Store complete preferences for reference
                company.preferences_data = preferences
                
                # Extract and store logo URL from SalesFormsPrefs
                self._extract_and_store_logo_url(company, preferences)
                
                # Currency preferences
                currency_prefs = preferences.get("CurrencyPrefs", {})
                if currency_prefs:
                    home_currency = currency_prefs.get("HomeCurrency", {})
                    company.currency_code = home_currency.get("value", "USD")
                    company.multi_currency_enabled = currency_prefs.get("MultiCurrencyEnabled", False)
                    logger.info(f"Set company currency: {company.currency_code}, Multi-currency: {company.multi_currency_enabled}")
                
                # Sales form preferences (invoices, estimates, etc.)
                sales_prefs = preferences.get("SalesFormsPrefs", {})
                if sales_prefs:
                    # Invoice template
                    template_ref = sales_prefs.get("DefaultInvoiceTemplateRef", {})
                    if template_ref:
                        company.invoice_template_id = template_ref.get("value")
                        company.invoice_template_name = template_ref.get("name")
                    
                    # Invoice customization
                    company.invoice_logo_enabled = sales_prefs.get("AllowInvoiceLogo", True)
                    company.brand_color = sales_prefs.get("BrandingColor", "#0077C5")
                    
                    # Invoice numbering and terms
                    company.auto_invoice_number = sales_prefs.get("AutoInvoiceNumber", False)
                    
                    default_terms = sales_prefs.get("DefaultTerms")
                    if default_terms:
                        company.default_payment_terms = default_terms.get("value")
                    
                    # Shipping and delivery
                    company.default_delivery_method = sales_prefs.get("DefaultDeliveryMethod")
                    company.default_ship_method = sales_prefs.get("DefaultShipMethod")

                # Tax preferences
                tax_prefs = preferences.get("TaxPrefs", {})
                if tax_prefs:
                    company.tax_enabled = tax_prefs.get("UsingSalesTax", False)
                    company.tax_calculation = tax_prefs.get("TaxGroupCodePref", "TaxExcluded")

                # Other important preferences
                other_prefs = preferences.get("OtherPrefs", {})
                if other_prefs:
                    # Feature flags
                    company.time_tracking_enabled = other_prefs.get("TimeTrackingEnabled", False)
                    company.inventory_enabled = other_prefs.get("InventoryEnabled", False)
                    company.class_tracking_enabled = other_prefs.get("ClassTrackingPerTxn", False)
                    company.department_tracking_enabled = other_prefs.get("DepartmentTracking", False)
                    
                    # Customer/Vendor tracking
                    company.customer_tracking_enabled = other_prefs.get("CustomerTracking", True)
                    company.vendor_tracking_enabled = other_prefs.get("VendorTracking", True)

                # Email preferences
                email_prefs = preferences.get("EmailMessagesPrefs", {})
                if email_prefs:
                    company.email_when_sent = email_prefs.get("InvoiceEmailWhenSent", False)
                    company.email_when_opened = email_prefs.get("InvoiceEmailWhenOpened", False)
                    company.email_when_paid = email_prefs.get("InvoiceEmailWhenPaid", False)

                # Save all preference-related fields
                update_fields = [
                    "preferences_data", "currency_code", "multi_currency_enabled",
                    "invoice_template_id", "invoice_template_name", "invoice_logo_enabled", 
                    "brand_color", "auto_invoice_number", "default_payment_terms", 
                    "tax_enabled", "tax_calculation", "time_tracking_enabled", 
                    "inventory_enabled", "class_tracking_enabled", "department_tracking_enabled",
                    "customer_tracking_enabled", "vendor_tracking_enabled", 
                    "default_delivery_method", "default_ship_method", "email_when_sent", 
                    "email_when_opened", "email_when_paid"
                ]

                # Add logo_url to update fields if it was set
                if hasattr(company, '_logo_url_updated') and company._logo_url_updated:
                    update_fields.append("logo_url")
                    delattr(company, '_logo_url_updated')

                company.save(update_fields=update_fields)
                
                logger.info(f"Stored comprehensive preferences for company {company.realm_id}")
                
            elif response.status_code == 403:
                logger.error(f"Access forbidden for company preferences - check app permissions")
            else:
                logger.error(f"Failed to fetch preferences: {response.status_code}")

        except requests.RequestException as e:
            logger.error(f"Error fetching preferences from QuickBooks: {str(e)}")
    


    def _extract_and_store_logo_url(self, company, preferences: dict):
        """
        Attempts to fetch the company logo from three QuickBooks API sources in order:
        1. Preferences (Direct link, least common)
        2. CustomFormStyle (Preferred link, fails with 'Unsupported Operation' on some plans)
        3. Attachments API (Fallback, requires file download and local storage/S3)
        """
        if not preferences or not company.realm_id or not company.access_token:
            logger.warning("⚠️ Missing data for logo extraction (access_token, realm_id, or preferences).")
            return

        access_token = company.access_token
        
        # ------------------------------------------------------------------------
        # 1. ATTEMPT: Preferences (Checking for direct logo reference inside the Preferences JSON)
        # ------------------------------------------------------------------------
        try:
            sales_prefs = preferences.get("SalesFormsPrefs", {})
            custom_styles = sales_prefs.get("CustomFormStyles", [])
            for style in custom_styles:
                logo_ref = style.get("LogoRef", {})
                if logo_ref and logo_ref.get("Value"):
                    company.logo_url = logo_ref["Value"]
                    company._logo_url_updated = True
                    logger.info(f"✅ Found logo in preferences: {company.logo_url}")
                    return
        except Exception as e:
            logger.debug(f"Error reading logo from preferences: {str(e)}")

        # ------------------------------------------------------------------------
        # 2. ATTEMPT: CustomFormStyle API (The one that often returns 400/Unsupported)
        # ------------------------------------------------------------------------
        try:
            logger.info("🔍 Fetching CustomFormStyle API...")
            
            # Use a QBO query URL format for better compatibility
            styles_query = "SELECT * FROM CustomFormStyle"
            styles_url = f"https://quickbooks.api.intuit.com/v3/company/{company.realm_id}/query?query={quote(styles_query)}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }

            response = requests.get(styles_url, headers=headers, timeout=30)
            logger.info(f"CustomFormStyle response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                styles = data.get("QueryResponse", {}).get("CustomFormStyle", [])
                
                for style in styles:
                    # The structure for logo here is often complex, checking for ImageName or URI
                    logo_ref = style.get("CustomStyle", {}).get("Image", {}).get("ImageName")
                    if logo_ref:
                        company.logo_url = logo_ref
                        company._logo_url_updated = True
                        # NOTE: This value might be a reference ID, not a URL.
                        logger.info(f"✅ Found logo in CustomFormStyle (Reference): {company.logo_url}")
                        return

            elif response.status_code == 400:
                # Graceful handling for "Unsupported Operation"
                error_message = response.json().get("Fault", {}).get("Error", [{}])[0].get("Message", "")
                if "Unsupported Operation" in error_message or "customformstyle is not supported" in response.text:
                    logger.warning("⏩ Intuit returned 'Unsupported Operation' (400) for CustomFormStyle. Skipping...")
                    pass # Continue to the next attempt
                else:
                    logger.error(f"Failed to fetch CustomFormStyle: {response.status_code} - {response.text}")

            else:
                logger.error(f"Failed to fetch CustomFormStyle: {response.status_code} - {response.text}")

        except requests.RequestException as e:
            logger.error(f"❌ Error fetching CustomFormStyle: {str(e)}")

        # ------------------------------------------------------------------------
        # 3. ATTEMPT: Attachments API (Fallback, requires file download)
        # ------------------------------------------------------------------------
        try:
            logger.info("🔍 Trying Attachments API for company logo...")
            
            # Query for an image (jpeg/png) attachment linked to the main Company entity
            attachments_query = (
                "SELECT Id, FileName FROM Attachable WHERE "
                "ContentType IN ('image/jpeg', 'image/png') AND "
                "AttachableRef.EntityType = 'Company' "
                "MAXRESULTS 1"
            )
            query_url = f"https://quickbooks.api.intuit.com/v3/company/{company.realm_id}/query?query={quote(attachments_query)}"
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

            response = requests.get(query_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json().get("QueryResponse", {})
            attachments = data.get("Attachable", [])

            if attachments:
                attachment = attachments[0]
                attachment_id = attachment["Id"]
                file_name = attachment.get("FileName", f"qb_logo_{attachment_id}.png")
                logger.info(f"Found potential logo attachment with ID: {attachment_id}")

                # Download the attachment file
                download_url = f"https://quickbooks.api.intuit.com/v3/company/{company.realm_id}/attachable/{attachment_id}/upload"
                file_response = requests.get(download_url, headers=headers, timeout=30)
                file_response.raise_for_status()

                # *** PLACEHOLDER FOR YOUR FILE STORAGE LOGIC ***
                # Replace this block with your actual code to save the file and get a URL
                # public_logo_url = save_file_to_storage(file_name, file_response.content)
                public_logo_url = f"/media/logos/{file_name}" # Example placeholder
                
                company.logo_url = public_logo_url
                company._logo_url_updated = True
                
                logger.info(f"✅ Logo successfully fetched and stored from Attachments: {company.logo_url}")
                return # Success!
            else:
                logger.info("No company-related image attachments found.")

        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ HTTP Error during attachment fetch: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.error(f"❌ Unexpected error in attachment logo fetch: {str(e)}")

        logger.info("ℹ️ Logo fetch attempts completed without finding a logo.")

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
                "brand_color": company.brand_color,
                "logo": company.logo_url,
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