from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

User = get_user_model()

class OAuthState(models.Model):
    """
    Store OAuth state for CSRF protection in database instead of session
    """
    state = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    class Meta:
        db_table = 'oauth_states'
        indexes = [
            models.Index(fields=['state', 'used']),
            models.Index(fields=['user', 'created_at']),
        ]

    def is_valid(self):
        """Check if state is still valid (within 15 minutes and not used)"""
        if self.used:
            return False

        if not self.created_at:
            return False

        expiry_time = self.created_at + timedelta(minutes=15)
        return timezone.now() < expiry_time

    def mark_used(self):
        """Mark state as used"""
        self.used = True
        self.save()

    @classmethod
    def cleanup_expired(cls):
        """Remove expired states"""
        expiry_time = timezone.now() - timedelta(minutes=15)
        cls.objects.filter(created_at__lt=expiry_time).delete()

    def __str__(self):
        return f"OAuth State {self.state[:8]}... for {self.user.email}"