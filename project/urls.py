# In your main project's urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('qbo_auth.urls')),
    path('api/', include('invoices.urls')),
    path('api/', include('creditnote.urls')),
    path('api/', include('customers.urls')),
]