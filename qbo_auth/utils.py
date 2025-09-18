from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist

User = get_user_model()

def get_default_company_by_email(email: str):
    """
    Given a user's email, return the default company details.
    Returns None if no default company is found.
    """
    try:
        user = User.objects.get(email=email)
    except ObjectDoesNotExist:
        return None

    try:
        membership = user.company_memberships.select_related("company").get(is_default=True)
        company = membership.company
        return {
            "company_id": str(company.id),
            "company_name": company.name,
            "realm_id": company.realm_id,
            "connection_status": "connected" if company.is_connected else "disconnected",
            "created_by": company.created_by_id,
            "role": membership.role,
        }
    except ObjectDoesNotExist:
        return None
