from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.contrib.auth import get_user_model

from companies.models import Company, CompanyMembership

User = get_user_model()


@receiver(post_save, sender=User)
def create_default_company_for_new_user(sender, instance, created, **kwargs):
    """
    When a new user is created, create a default company and attach membership.
    If the user already has companies (e.g., through fixtures), skip creating a new default.
    """
    if not created:
        return

    existing = CompanyMembership.objects.filter(user=instance).exists()
    if existing:
        return

    default_company = Company.objects.create(
        name="default",
        created_by=instance,
    )

    CompanyMembership.objects.create(
        user=instance,
        company=default_company,
        is_default=True,
        role="admin",
    )
