from datetime import datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from moon_rental.models import Auction, AuctionLot, Invoice, Rental
from moon_rental.services.rentals import (
    assign_moon,
    end_rental,
    isk,
    open_auction,
    place_bid,
    settle_auction,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("amount", [0, -1, "NaN", "Infinity", "0.001", "1e19", "bad"])
def test_invalid_money(amount):
    with pytest.raises(ValidationError):
        isk(amount)


def test_competitive_bids_and_award(auction, pilot, other_pilot, moon, manager):
    lot = auction.lots.get()
    place_bid(actor=pilot, lot_id=lot.pk, amount="100")
    for amount in ["99", "100"]:
        with pytest.raises(ValidationError):
            place_bid(actor=other_pilot, lot_id=lot.pk, amount=amount)
    winning = place_bid(actor=other_pilot, lot_id=lot.pk, amount="101.01")
    assert settle_auction(auction.pk) == 0
    assert settle_auction(auction.pk, at=auction.ends_at) == 1
    assert settle_auction(auction.pk, at=auction.ends_at) == 0
    rental = Rental.objects.get()
    assert (rental.renter_id, rental.price, rental.auction_lot_id) == (
        other_pilot.pk,
        Decimal("101.01"),
        lot.pk,
    )
    lot.refresh_from_db()
    moon.refresh_from_db()
    assert lot.winning_bid_id == winning.pk
    assert moon.current_rental_id == rental.pk and moon.reserved_lot_id is None
    with pytest.raises(PermissionDenied):
        end_rental(actor=manager, rental_id=rental.pk, reason="change")


def test_closed_auction_rejects_bid(auction, pilot):
    Auction.objects.filter(pk=auction.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="closed"):
        place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="150")


def test_no_bid_moon_released(auction, moon):
    assert settle_auction(auction.pk, at=auction.ends_at) == 0
    moon.refresh_from_db()
    assert moon.reserved_lot_id is None and moon.current_rental_id is None


def test_cannot_double_reserve_or_assign(auction, manager, pilot, moon):
    with pytest.raises(ValidationError):
        assign_moon(actor=manager, moon_id=moon.pk, renter=pilot, price="100", duration_months=3)
    with pytest.raises(ValidationError):
        open_auction(
            actor=manager,
            title="Duplicate",
            moon_ids=[moon.pk],
            ends_at=auction.ends_at,
            duration_months=1,
        )
    assert AuctionLot.objects.count() == 1


def test_admin_correction_preserves_old_invoice(
    manager, admin_user, wallet, moon, pilot, other_pilot
):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="200",
        duration_months=2,
        billing_mode="once",
    )
    end_rental(actor=admin_user, rental_id=rental.pk, reason="Renter correction")
    replacement = assign_moon(
        actor=admin_user, moon_id=moon.pk, renter=other_pilot, price="300", duration_months=3
    )
    assert Invoice.objects.get(rental=rental).amount == 200
    assert replacement.renter == other_pilot


def test_basic_user_cannot_manage(pilot, wallet, moon):
    with pytest.raises(PermissionDenied):
        open_auction(
            actor=pilot,
            title="No",
            moon_ids=[moon.pk],
            ends_at=timezone.now() + timedelta(days=1),
            duration_months=2,
        )


def test_no_permission_or_inactive_user_cannot_bid(auction, pilot):
    user = User.objects.create_user("outsider")
    for bidder in [user, pilot]:
        if bidder == pilot:
            bidder.is_active = False
        with pytest.raises(PermissionDenied):
            place_bid(actor=bidder, lot_id=auction.lots.get().pk, amount="200")


def test_expired_lease_can_be_reassigned(manager, pilot, other_pilot, wallet, moon):
    at = datetime(2026, 1, 31, 12, tzinfo=tz.utc)
    old = assign_moon(
        actor=manager, moon_id=moon.pk, renter=pilot, price="100", duration_months=1, at=at
    )
    assert old.ends_on.isoformat() == "2026-02-28"
    replacement = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=other_pilot,
        price="100",
        duration_months=1,
        at=datetime(2026, 2, 28, tzinfo=tz.utc),
    )
    old.refresh_from_db()
    assert old.ended_at and replacement.pk != old.pk


@pytest.mark.parametrize(
    "duration,mode", [(0, "monthly"), (121, "monthly"), (True, "monthly"), (3, "weekly")]
)
def test_bad_terms(manager, wallet, moon, duration, mode):
    with pytest.raises(ValidationError):
        open_auction(
            actor=manager,
            title="Bad",
            moon_ids=[moon.pk],
            ends_at=timezone.now() + timedelta(days=1),
            duration_months=duration,
            billing_mode=mode,
        )
