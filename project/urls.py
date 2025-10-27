# In your main project's urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('qbo_auth.urls')),
    path('api/', include('invoices.urls')),
    path('api/', include('creditnote.urls')),
    path('api/', include('customers.urls')),
    path('api/', include('companies.urls')),
    path('api/kra/', include('kra.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)