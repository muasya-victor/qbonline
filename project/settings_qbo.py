import os

QBO_ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")

print(QBO_ENVIRONMENT, 'out env')
# Base URLs
DEV_QBO_BASE_URL = os.getenv("DEV_QBO_BASE_URL", "https://sandbox-quickbooks.api.intuit.com")
STAGING_QBO_BASE_URL = os.getenv("PROD_QBO_BASE_URL", "https://quickbooks.api.intuit.com")
PROD_QBO_BASE_URL = os.getenv("PROD_QBO_BASE_URL", "https://quickbooks.api.intuit.com")

if QBO_ENVIRONMENT == "production":
    BASE_URL = PROD_QBO_BASE_URL
elif QBO_ENVIRONMENT == "staging":
    BASE_URL = PROD_QBO_BASE_URL
elif QBO_ENVIRONMENT == "sandbox":
    BASE_URL = PROD_QBO_BASE_URL
else:
    BASE_URL = DEV_QBO_BASE_URL



# Auth endpoints
AUTH_BASE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Sandbox & production userinfo endpoints
SANDBOX_USERINFO_URL = "https://sandbox-accounts.platform.intuit.com/v1/openid_connect/userinfo"
STAGING_USERINFO_URL = "https://accounts.platform.intuit.com/v1/openid_connect/userinfo"
PRODUCTION_USERINFO_URL = "https://accounts.platform.intuit.com/v1/openid_connect/userinfo"

# Environment-specific credentials
if QBO_ENVIRONMENT == "production":
    USERINFO_URL = PRODUCTION_USERINFO_URL
    QBO_CLIENT_ID = os.getenv("PROD_QBO_CLIENT_ID")
    QBO_CLIENT_SECRET = os.getenv("PROD_QBO_CLIENT_SECRET")
    QBO_REDIRECT_URI = os.getenv("PROD_QBO_REDIRECT_URI", "https://prod.v2.smartinvoice.co.ke/qbo/callback")
    QBO_REDIRECT_URI_FRONTEND = os.getenv("PROD_QBO_REDIRECT_URI_FRONTEND", "https://prod.v2.smartinvoice.co.ke/qbo/callback")
if QBO_ENVIRONMENT == "staging":
    USERINFO_URL = PRODUCTION_USERINFO_URL
    QBO_CLIENT_ID = os.getenv("STAGING_QBO_CLIENT_ID")
    QBO_CLIENT_SECRET = os.getenv("STAGING_QBO_CLIENT_SECRET")
    QBO_REDIRECT_URI = os.getenv("STAGING_QBO_REDIRECT_URI", "https://sbx.v2.smartinvoice.co.ke/qbo/callback")
    QBO_REDIRECT_URI_FRONTEND = os.getenv("STAGING_QBO_REDIRECT_URI_FRONTEND", "https://sbx.v2.smartinvoice.co.ke/qbo/callback")
if QBO_ENVIRONMENT == "sandbox":
    USERINFO_URL = PRODUCTION_USERINFO_URL
    QBO_CLIENT_ID = os.getenv("STAGING_QBO_CLIENT_ID")
    QBO_CLIENT_SECRET = os.getenv("STAGING_QBO_CLIENT_SECRET")
    QBO_REDIRECT_URI = os.getenv("STAGING_QBO_REDIRECT_URI", "https://sbx.v2.smartinvoice.co.ke/qbo/callback")
    QBO_REDIRECT_URI_FRONTEND = os.getenv("STAGING_QBO_REDIRECT_URI_FRONTEND", "https://sbx.v2.smartinvoice.co.ke/qbo/callback")
else:
    USERINFO_URL = SANDBOX_USERINFO_URL
    QBO_CLIENT_ID = os.getenv("DEV_QBO_CLIENT_ID")
    QBO_CLIENT_SECRET = os.getenv("DEV_QBO_CLIENT_SECRET")
    QBO_REDIRECT_URI = os.getenv("DEV_QBO_REDIRECT_URI", "http://localhost:3000/qbo/callback")
    QBO_REDIRECT_URI_FRONTEND = os.getenv("DEV_QBO_REDIRECT_URI_FRONTEND", "http://localhost:3000/qbo/callback")

# Derived endpoints using BASE_URL
COMPANY_INFO_URL = f"{BASE_URL}/v3/company/{{realm_id}}/companyinfo/{{realm_id}}"
PREFERENCES_URL = f"{BASE_URL}/v3/company/{{realm_id}}/preferences"

# General app config
ACTIVE_APP = os.getenv("ACTIVE_APP", "SMARTAPP")
SECRET_KEY = os.getenv("SECRET_KEY", "your-django-secret-key-here")
