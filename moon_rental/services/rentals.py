from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from moon_rental.models import Auction, AuctionLot, AuditEvent, Bid, BillingMode, Moon, Rental
from moon_rental.permissions import require_access, require_admin, require_manager


def audit(actor, action, obj, detail=""):
    AuditEvent.objects.create(actor=actor, action=action, object_id=str(obj.pk), detail=detail)


def isk(value):
    try:
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or amount <= 0
            or amount >= Decimal("1e18")
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(
            "Enter a positive ISK amount with at most two decimal places."
        ) from exc
    return amount


def terms(duration, mode):
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 120:
        raise ValidationError("Rental duration must be 1–120 months.")
    if mode not in BillingMode.values:
        raise ValidationError("Choose monthly or one payment billing.")


def utc_day(at):
    return at.astimezone(dt_timezone.utc).date()


def validate_rent_start(starts_on, ends_at):
    if (
        not isinstance(starts_on, date)
        or isinstance(starts_on, datetime)
        or starts_on <= utc_day(ends_at)
    ):
        raise ValidationError("Start rent must be a date after the bidding deadline (UTC).")


def release_expired(moon, at):
    if moon.current_rental_id and moon.current_rental.ends_on <= utc_day(at):
        rental = moon.current_rental
        rental.ended_at = at
        rental.save(update_fields=["ended_at"])
        moon.current_rental = None
        moon.save(update_fields=["current_rental"])


def require_default_wallet():
    from moon_rental.services.wallets import default_wallet

    wallet = default_wallet()
    if not wallet or not wallet.enabled:
        raise ValidationError("An admin must connect and select a default payment wallet first.")
    return wallet


@transaction.atomic
def open_auction(
    *,
    actor,
    title,
    moon_ids,
    ends_at,
    starts_on,
    duration_months,
    billing_mode=BillingMode.MONTHLY,
    minimum_bid="1.00",
):
    require_manager(actor)
    terms(duration_months, billing_mode)
    require_default_wallet()
    now = timezone.now()
    if not title.strip() or len(title) > 160:
        raise ValidationError("Enter an auction title of up to 160 characters.")
    if timezone.is_naive(ends_at) or ends_at <= now:
        raise ValidationError("The bidding deadline must be in the future, with a timezone.")
    validate_rent_start(starts_on, ends_at)
    ids = set(moon_ids)
    moons = list(Moon.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if not moons or len(moons) != len(ids):
        raise ValidationError("Select at least one synced moon.")
    for moon in moons:
        release_expired(moon, now)
        if not moon.available or moon.current_rental_id or moon.reserved_lot_id:
            raise ValidationError(f"{moon.name} is rented, reserved or no longer in moonmining.")
    auction = Auction.objects.create(
        title=title.strip(),
        ends_at=ends_at,
        starts_on=starts_on,
        duration_months=duration_months,
        billing_mode=billing_mode,
        minimum_bid=isk(minimum_bid),
        created_by=actor,
    )
    for moon in moons:
        lot = AuctionLot.objects.create(auction=auction, moon=moon)
        moon.reserved_lot = lot
        moon.save(update_fields=["reserved_lot"])
    audit(actor, "auction.open", auction)
    return auction


@transaction.atomic
def place_bid(*, actor, lot_id, amount):
    require_access(actor)
    auction_id = AuctionLot.objects.values_list("auction_id", flat=True).get(pk=lot_id)
    auction = Auction.objects.select_for_update().get(pk=auction_id)
    lot = AuctionLot.objects.select_for_update().get(pk=lot_id)
    if auction.settled_at or auction.ends_at <= timezone.now():
        raise ValidationError("Bidding has closed.")
    amount = isk(amount)
    top = lot.highest_bid
    if amount < auction.minimum_bid or (top and amount <= top.amount):
        raise ValidationError("Your bid must meet the minimum and exceed the highest bid.")
    bid = Bid.objects.create(lot=lot, bidder=actor, amount=amount)
    audit(actor, "bid.place", bid)
    return bid


@transaction.atomic
def settle_auction(auction_id, *, at=None):
    """Automatic settlement. No caller-supplied renter or price is accepted."""
    from moon_rental.services.billing import issue_invoices

    at = at or timezone.now()
    auction = Auction.objects.select_for_update().get(pk=auction_id)
    if auction.settled_at or auction.ends_at > at:
        return 0
    count = 0
    for lot in auction.lots.order_by("moon_id"):
        moon = Moon.objects.select_for_update().get(pk=lot.moon_id)
        if moon.reserved_lot_id != lot.pk or moon.current_rental_id:
            raise ValidationError("Moon reservation is inconsistent; admin intervention required.")
        bid = lot.highest_bid
        if bid:
            starts = auction.starts_on
            rental = Rental.objects.create(
                moon=moon,
                renter=bid.bidder,
                price=bid.amount,
                billing_mode=auction.billing_mode,
                starts_on=starts,
                ends_on=starts + relativedelta(months=auction.duration_months),
                auction_lot=lot,
                created_by=auction.created_by,
            )
            moon.current_rental = rental
            lot.winning_bid = bid
            lot.save(update_fields=["winning_bid"])
            issue_invoices(rental.pk, at=at)
            audit(None, "auction.award", rental)
            count += 1
        moon.reserved_lot = None
        moon.save(update_fields=["current_rental", "reserved_lot"])
    auction.settled_at = at
    auction.save(update_fields=["settled_at"])
    return count


@transaction.atomic
def assign_moon(
    *, actor, moon_id, renter, price, duration_months, billing_mode=BillingMode.MONTHLY, at=None
):
    from moon_rental.services.billing import issue_invoices

    require_manager(actor)
    terms(duration_months, billing_mode)
    require_default_wallet()
    require_access(renter)
    at = at or timezone.now()
    moon = Moon.objects.select_for_update().get(pk=moon_id)
    release_expired(moon, at)
    if not moon.available or moon.current_rental_id or moon.reserved_lot_id:
        raise ValidationError("Only available, unassigned moons can be assigned.")
    starts = utc_day(at)
    rental = Rental.objects.create(
        moon=moon,
        renter=renter,
        price=isk(price),
        billing_mode=billing_mode,
        starts_on=starts,
        ends_on=starts + relativedelta(months=duration_months),
        created_by=actor,
    )
    moon.current_rental = rental
    moon.save(update_fields=["current_rental"])
    issue_invoices(rental.pk, at=at)
    audit(actor, "rental.assign", rental)
    return rental


@transaction.atomic
def end_rental(*, actor, rental_id, reason, at=None):
    """Corrections terminate a lease; a replacement is a separate audited assignment."""
    from moon_rental.services.billing import issue_invoices

    require_admin(actor)
    if not reason.strip():
        raise ValidationError("An explanation is required to change an assignment.")
    at = at or timezone.now()
    moon_id = Rental.objects.values_list("moon_id", flat=True).get(pk=rental_id)
    moon = Moon.objects.select_for_update().get(pk=moon_id)
    rental = Rental.objects.select_for_update().get(pk=rental_id)
    if rental.ended_at:
        return
    issue_invoices(rental.pk, at=at)
    rental.ended_at = at
    rental.save(update_fields=["ended_at"])
    if moon.current_rental_id == rental.pk:
        moon.current_rental = None
        moon.save(update_fields=["current_rental"])
    audit(actor, "rental.end", rental, reason[:255])
