# Create a simple model
from django.db import models

class OAuthState(models.Model):
    state = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'oauth_states'