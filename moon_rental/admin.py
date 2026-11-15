from django.contrib import admin

from moon_rental import models


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
    models.Configuration,
    models.AuditEvent,
]:
    admin.site.register(model, ReadOnlyAdmin)
