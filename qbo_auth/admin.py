from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from .models import OAuthState

@admin.register(OAuthState)
class OAuthStateAdmin(admin.ModelAdmin):
    list_display = ("id", "state_preview", "user_email", "status_display", "validity_display", "created_at", "time_since_created",)
    list_filter = ("used", "created_at")
    search_fields = ("state", "user__email", "user__first_name", "user__last_name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "validity_display", "time_since_created", "expires_at")
    raw_id_fields = ("user",)

    def state_preview(self, obj):
        return f"{obj.state[:8]}...{obj.state[-4:]}"
    state_preview.short_description = "State Preview"

    def user_email(self, obj):
        url = reverse('admin:auth_user_change', args=[obj.user.id])
        return format_html('<a href="{}">{}</a>', url, obj.user.email)
    user_email.short_description = "User"

    def status_display(self, obj):
        if obj.used:
            return format_html('<span style="color: orange;">● Used</span>')
        elif obj.is_valid():
            return format_html('<span style="color: green;">● Valid</span>')
        else:
            return format_html('<span style="color: red;">● Expired</span>')
    status_display.short_description = "Status"

    def validity_display(self, obj):
        if obj.used:
            return "Used (cannot be reused)"
        elif not obj.created_at:
            return "Not saved yet"
        elif obj.is_valid():
            expires_at = obj.created_at + timezone.timedelta(minutes=15)
            time_left = expires_at - timezone.now()
            minutes_left = int(time_left.total_seconds() / 60)
            return f"Valid ({minutes_left} minutes remaining)"
        else:
            return "Expired"
    validity_display.short_description = "Validity"

    def time_since_created(self, obj):
        if not obj.created_at:
            return "Not created yet"

        now = timezone.now()
        diff = now - obj.created_at
        if diff.days > 0:
            return f"{diff.days} days ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hours ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minutes ago"
        else:
            return "Just now"
    time_since_created.short_description = "Age"

    def expires_at(self, obj):
        if not obj.created_at:
            return "Not created yet"
        return obj.created_at + timezone.timedelta(minutes=15)
    expires_at.short_description = "Expires At"

    fieldsets = (
        (None, {
            "fields": ("state", "user", "used")
        }),
        ("Validity Info", {
            "fields": ("validity_display", "created_at", "expires_at", "time_since_created"),
        }),
    )

    actions = ["mark_as_used", "cleanup_expired"]

    def mark_as_used(self, request, queryset):
        updated = queryset.update(used=True)
        self.message_user(request, f"{updated} OAuth states marked as used.")
    mark_as_used.short_description = "Mark selected states as used"

    def cleanup_expired(self, request, queryset):
        from datetime import timedelta
        expiry_time = timezone.now() - timedelta(minutes=15)
        deleted_count = OAuthState.objects.filter(created_at__lt=expiry_time).count()
        OAuthState.objects.filter(created_at__lt=expiry_time).delete()
        self.message_user(request, f"Cleaned up {deleted_count} expired OAuth states.")
    cleanup_expired.short_description = "Clean up all expired states"
