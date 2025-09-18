import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
from common.models import TimeStampModel  # base model with created/updated fields

User = settings.AUTH_USER_MODEL


class Company(TimeStampModel):
    """
    Represents a tenant (company) in the system. A company can be associated with many users.
    QuickBooks connection data is stored here (one connection per company).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="default")
    
    # QuickBooks fields
    realm_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    is_connected = models.BooleanField(default=False)  # keep for admin / filtering

    # Token information
    access_token = models.TextField(null=True, blank=True)
    refresh_token = models.TextField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    token_data = models.JSONField(null=True, blank=True)
    is_connected_db = models.BooleanField(default=False)

    # Optional: who initially created this company
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_companies")

    class Meta:
        indexes = [
            models.Index(fields=["realm_id"]),
            models.Index(fields=["name"]),
        ]

    @property
    def is_connected(self):
        """
        Returns True if access_token exists and has more than 50 minutes before expiry.
        """
        if self.access_token and self.access_token_expires_at:
            remaining = self.access_token_expires_at - timezone.now()
            return remaining > timedelta(minutes=50)
        return False

    def mark_connected(self, token_response: dict):
        """
        Helper: update tokens from QuickBooks token response dict.
        """
        self.access_token = token_response.get("access_token")
        self.refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in")
        if expires_in:
            self.access_token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        self.token_data = token_response

        self.is_connected_db = self.is_connected_db
        self.save(update_fields=[
            "access_token",
            "refresh_token",
            "access_token_expires_at",
            "token_data",
            "is_connected_db",
        ])

    def disconnect(self):
        self.access_token = None
        self.refresh_token = None
        self.access_token_expires_at = None
        self.refresh_token_expires_at = None
        self.token_data = None
        self.is_connected = False
        self.save()


class CompanyMembership(models.Model):
    """
    Through model relating users to companies.
    This allows per-user flags (e.g. is_default) for a company.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="company_memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    is_default = models.BooleanField(default=False)
    # role: admin/member/etc (optional)
    role = models.CharField(max_length=50, default="member")

    class Meta:
        unique_together = ("user", "company")
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["company"]),
            models.Index(fields=["is_default"]),
        ]

    def save(self, *args, **kwargs):
        # If this membership becomes default, unset other defaults for this user
        if self.is_default:
            CompanyMembership.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)



class ActiveCompany(models.Model):
    """
    Stores the current active company for a given user.
    Unlike 'is_default' in CompanyMembership, this is meant
    to reflect what the user is actively working on right now.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="active_company"
    )
    company = models.ForeignKey(
        "Company", 
        on_delete=models.CASCADE,
        related_name="active_users"
    )
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Active Company"
        verbose_name_plural = "Active Companies"

    def __str__(self):
        return f"{self.user} active in {self.company}"

