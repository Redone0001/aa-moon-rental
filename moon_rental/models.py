import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def invoice_code():
    alphabet = string.ascii_uppercase + string.digits
    return "moon-rental-" + ".".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
    )


def default_reminder_hours():
    return [24, 12, 6, 1]


class BillingMode(models.TextChoices):
    MONTHLY = "monthly", "Per month"
    ONCE = "once", "One payment"


class Moon(models.Model):
    """A local survey snapshot. Never writes back to moonmining."""

    id = models.PositiveBigIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    solar_system = models.CharField(max_length=255)
    region = models.CharField(max_length=255, db_index=True)
    constellation = models.CharField(max_length=255, blank=True, default="", db_index=True)
    rarity = models.PositiveIntegerField(default=0, db_index=True)
    composition = models.JSONField(default=list)
    estimated_value = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    pull_30d = models.JSONField(default=dict, blank=True)
    available = models.BooleanField(default=True)
    synced_at = models.DateTimeField()
    current_rental = models.OneToOneField(
        "Rental", null=True, blank=True, on_delete=models.SET_NULL, related_name="active_moon"
    )
    reserved_lot = models.OneToOneField(
        "AuctionLot", null=True, blank=True, on_delete=models.SET_NULL, related_name="reserved_moon"
    )

    class Meta:
        ordering = ["name"]
        default_permissions = ()
        permissions = [
            ("basic_access", "Can access Moon Rental and bid"),
            ("manage_rentals", "Can open auctions, assign available moons and view invoices"),
            ("administer_rentals", "Can sync moons, manage wallets and change assignments"),
        ]

    def __str__(self):
        return self.name


class SurveyOre(models.Model):
    """Indexed ore membership in the local survey snapshot."""

    moon = models.ForeignKey(Moon, on_delete=models.CASCADE, related_name="survey_ores")
    type_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=255)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["moon", "type_id"], name="mr_unique_survey_ore")
        ]


class Auction(models.Model):
    title = models.CharField(max_length=160)
    bidding_starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reminder_hours = models.JSONField(default=default_reminder_hours, blank=True)
    starts_on = models.DateField(help_text="Rent starts at 00:00 UTC on this date")
    ends_at = models.DateTimeField(db_index=True)
    duration_months = models.PositiveSmallIntegerField()
    billing_mode = models.CharField(
        max_length=8, choices=BillingMode.choices, default=BillingMode.MONTHLY
    )
    minimum_bid = models.DecimalField(max_digits=20, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        default_permissions = ()

    def __str__(self):
        return self.title


class AuctionLot(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="lots")
    moon = models.ForeignKey(Moon, on_delete=models.PROTECT, related_name="lots")
    starting_price = models.DecimalField(max_digits=20, decimal_places=2)
    winning_bid = models.ForeignKey("Bid", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=["auction", "moon"], name="mr_unique_lot")]

    @property
    def highest_bid(self):
        return self.bids.select_related("bidder").order_by("-amount", "created_at", "pk").first()


class Bid(models.Model):
    lot = models.ForeignKey(AuctionLot, on_delete=models.CASCADE, related_name="bids")
    bidder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="mr_bid_positive")
        ]
        indexes = [models.Index(fields=["lot", "-amount"], name="mr_bid_rank")]


class Rental(models.Model):
    moon = models.ForeignKey(Moon, on_delete=models.PROTECT, related_name="rentals")
    renter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    price = models.DecimalField(
        max_digits=20, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    billing_mode = models.CharField(max_length=8, choices=BillingMode.choices)
    starts_on = models.DateField()
    ends_on = models.DateField(help_text="Exclusive end date (UTC)")
    ended_at = models.DateTimeField(null=True, blank=True)
    billed_through = models.DateField(null=True, blank=True, editable=False)
    auction_lot = models.OneToOneField(AuctionLot, null=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["moon__name", "-starts_on"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gt=0), name="mr_rent_positive"),
            models.CheckConstraint(
                condition=models.Q(ends_on__gt=models.F("starts_on")), name="mr_rent_dates"
            ),
        ]


class WalletAccount(models.Model):
    class Kind(models.TextChoices):
        CHARACTER = "character", "Character"
        CORPORATION = "corporation", "Corporation (master wallet)"

    kind = models.CharField(max_length=12, choices=Kind.choices)
    eve_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=255)
    character_id = models.PositiveBigIntegerField()
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    enabled = models.BooleanField(default=True)
    last_scan_at = models.DateTimeField(null=True)
    last_scan_ok = models.BooleanField(null=True)

    class Meta:
        ordering = ["name"]
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=["kind", "eve_id"], name="mr_unique_wallet")]

    def __str__(self):
        return self.name


class Configuration(models.Model):
    """One singleton pointer guarantees a single default on MySQL as well as PostgreSQL."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_wallet = models.ForeignKey(WalletAccount, null=True, on_delete=models.PROTECT)
    discord_webhook_url = models.URLField(max_length=500, blank=True)
    discord_dm_enabled = models.BooleanField(default=True)
    discord_guild_id = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Discord Multiverse server ID used for renter DMs."
    )

    class Meta:
        default_permissions = ()

    def __str__(self):
        return "Moon Rental settings"


class Invoice(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    rental = models.ForeignKey(Rental, on_delete=models.CASCADE, related_name="invoices")
    code = models.CharField(max_length=25, default=invoice_code, unique=True, editable=False)
    period = models.DateField()
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    recipient = models.ForeignKey(WalletAccount, null=True, on_delete=models.PROTECT)
    recipient_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=4, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-period", "-pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["rental", "period"], name="mr_invoice_period")
        ]

    @property
    def paid_amount(self):
        return sum((receipt.amount for receipt in self.receipts.all()), Decimal("0"))

    @property
    def outstanding(self):
        return max(Decimal("0"), self.amount - self.paid_amount)


class PaymentReceipt(models.Model):
    """Only matched payment evidence; never a copy of the wallet journal."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="receipts")
    account = models.ForeignKey(WalletAccount, on_delete=models.PROTECT)
    journal_id = models.PositiveBigIntegerField()
    amount = models.DecimalField(max_digits=24, decimal_places=2)
    received_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["account", "journal_id"], name="mr_receipt_unique")
        ]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()


class Notification(models.Model):
    """Transactional delivery queue; contains no webhook or bot credentials."""

    key = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="+"
    )
    auction = models.ForeignKey(Auction, null=True, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True)
    skipped_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)

    class Meta:
        default_permissions = ()
