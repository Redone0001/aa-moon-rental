from django import forms
from django.contrib import admin

from moon_rental import models
from moon_rental.permissions import is_admin
from moon_rental.services.notifications import configure_notifications, validate_webhook


class ReadOnlyAdmin(admin.ModelAdmin):
    """All changes go through permission-checked services and audit events."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [
    models.Moon,
    models.Auction,
    models.AuctionLot,
    models.Bid,
    models.Rental,
    models.Invoice,
    models.PaymentReceipt,
    models.WalletAccount,
    models.AuditEvent,
    models.Notification,
]:
    admin.site.register(model, ReadOnlyAdmin)


class ConfigurationForm(forms.ModelForm):
    class Meta:
        model = models.Configuration
        fields = ["discord_webhook_url", "discord_dm_enabled", "discord_guild_id"]
        widgets = {"discord_webhook_url": forms.PasswordInput(render_value=True)}
        help_texts = {
            "discord_webhook_url": "Public auction reminders. Leave blank to disable. Treat this URL as a secret.",
            "discord_dm_enabled": "Outbid, invoice issued and partial/full payment DMs require Discord Multiverse and a server ID.",
        }

    def clean_discord_webhook_url(self):
        value = self.cleaned_data["discord_webhook_url"]
        validate_webhook(value)
        return value


@admin.register(models.Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    form = ConfigurationForm
    readonly_fields = ["default_wallet"]
    fields = ["default_wallet", "discord_webhook_url", "discord_dm_enabled", "discord_guild_id"]

    def has_module_permission(self, request):
        return is_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_admin(request.user)

    def has_add_permission(self, request):
        return is_admin(request.user) and not models.Configuration.objects.exists()

    def has_change_permission(self, request, obj=None):
        return is_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        config = configure_notifications(
            actor=request.user,
            webhook_url=obj.discord_webhook_url,
            dm_enabled=obj.discord_dm_enabled,
            guild_id=obj.discord_guild_id,
        )
        obj.pk = config.pk
