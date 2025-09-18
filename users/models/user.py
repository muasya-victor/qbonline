from django.db import models
import uuid
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.core.exceptions import ValidationError
from .managers import UserManager
from common.models import TimeStampModel

class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = [
        ("Mentor", "Mentor"),
        ("Mentee", "Mentee"),
        ("SystemAdmin", "System Admin"),
    ]

    id = models.UUIDField( 
         primary_key = True, 
         default = uuid.uuid4, 
         editable = False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    user_role = models.CharField(max_length=20,
                                  choices=ROLE_CHOICES,
                                  default="SystemAdmin")
    
    objects = UserManager()

    USERNAME_FIELD = "email"

    def get_full_name(self):
        if hasattr(self, 'profile') and self.profile.first_name and self.profile.last_name:
            return f"{self.profile.first_name} {self.profile.last_name}"
        return self.email

    def __str__(self):
        return self.email
    
    class Meta:
        indexes = [
            models.Index(fields=['user_role']),
        ]
    