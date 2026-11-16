from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse

from moon_rental.models import AuctionLot, AuditEvent, Bid, Moon, Notification, Rental
from moon_rental.services.maintenance import run_lifecycle
from moon_rental.services.rentals import place_bid, settle_auction

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "remaining_seconds,extended",
    [(121, False), (120.000001, False), (120, True), (119, True), (0.000001, True)],
)
def test_accepted_bid_extends_only_within_final_120_seconds(
    auction, pilot, remaining_seconds, extended
):
    original_end = auction.ends_at
    now = original_end - timedelta(seconds=remaining_seconds)
    with patch("moon_rental.services.rentals.timezone.now", return_value=now):
        bid = place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")

    auction.refresh_from_db()
    assert auction.ends_at == original_end + timedelta(seconds=120 if extended else 0)
    events = AuditEvent.objects.filter(action="auction.extend", object_id=str(auction.pk))
    if extended:
        event = events.get()
        assert event.actor_id == pilot.pk
        assert str(bid.pk) in event.detail
        assert original_end.isoformat() in event.detail
        assert auction.ends_at.isoformat() in event.detail
    else:
        assert not events.exists()


def test_bids_on_different_moons_can_repeat_extension(auction, pilot, other_pilot):
    original_end = auction.ends_at
    rent_start = auction.starts_on
    first_lot = auction.lots.get()
    moon = Moon.objects.create(pk=40000124, name="Another moon", synced_at=auction.created_at)
    second_lot = AuctionLot.objects.create(auction=auction, moon=moon, starting_price="100")
    moon.reserved_lot = second_lot
    moon.save(update_fields=["reserved_lot"])

    with patch(
        "moon_rental.services.rentals.timezone.now",
        return_value=original_end - timedelta(seconds=60),
    ):
        place_bid(actor=pilot, lot_id=first_lot.pk, amount="100")
    # The original deadline has passed, but every moon remains open until the new deadline.
    with patch(
        "moon_rental.services.rentals.timezone.now",
        return_value=original_end + timedelta(seconds=60),
    ):
        place_bid(actor=other_pilot, lot_id=second_lot.pk, amount="100")

    auction.refresh_from_db()
    assert auction.ends_at == original_end + timedelta(seconds=240)
    assert auction.starts_on == rent_start
    assert AuditEvent.objects.filter(action="auction.extend").count() == 2
    assert settle_auction(auction.pk, at=original_end) == 0
    run_lifecycle(at=auction.ends_at - timedelta(microseconds=1))
    assert not Rental.objects.exists()
    assert settle_auction(auction.pk, at=auction.ends_at) == 2
    assert settle_auction(auction.pk, at=auction.ends_at) == 0
    assert set(Rental.objects.values_list("starts_on", flat=True)) == {rent_start}


@pytest.mark.parametrize("amount", ["99", "100", "bad", "100.001"])
def test_rejected_bid_does_not_extend_deadline(auction, pilot, other_pilot, amount):
    lot = auction.lots.get()
    place_bid(actor=pilot, lot_id=lot.pk, amount="100")
    original_end = auction.ends_at
    with (
        patch(
            "moon_rental.services.rentals.timezone.now",
            return_value=original_end - timedelta(seconds=1),
        ),
        pytest.raises(ValidationError),
    ):
        place_bid(actor=other_pilot, lot_id=lot.pk, amount=amount)
    auction.refresh_from_db()
    assert auction.ends_at == original_end
    assert Bid.objects.count() == 1
    assert not AuditEvent.objects.filter(action="auction.extend").exists()


@pytest.mark.parametrize("seconds_after_end", [0, 1])
def test_bid_at_or_after_deadline_cannot_reopen_auction(auction, pilot, seconds_after_end):
    original_end = auction.ends_at
    with (
        patch(
            "moon_rental.services.rentals.timezone.now",
            return_value=original_end + timedelta(seconds=seconds_after_end),
        ),
        pytest.raises(ValidationError, match="Bidding has closed"),
    ):
        place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")
    auction.refresh_from_db()
    assert auction.ends_at == original_end
    assert not Bid.objects.exists()


def test_extension_rolls_back_with_bid(auction, pilot):
    original_end = auction.ends_at
    with (
        patch(
            "moon_rental.services.rentals.timezone.now",
            return_value=original_end - timedelta(seconds=1),
        ),
        pytest.raises(RuntimeError, match="rollback"),
        transaction.atomic(),
    ):
        place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")
        raise RuntimeError("rollback")
    auction.refresh_from_db()
    assert auction.ends_at == original_end
    assert not Bid.objects.exists()
    assert not AuditEvent.objects.filter(action="auction.extend").exists()


def test_outbid_notice_uses_extended_deadline(auction, pilot, other_pilot):
    from moon_rental.models import Configuration

    config = Configuration.objects.get(pk=1)
    config.discord_guild_id = 123
    config.save()
    lot = auction.lots.get()
    place_bid(actor=pilot, lot_id=lot.pk, amount="100")
    with (
        patch("moon_rental.services.notifications.apps.is_installed", return_value=True),
        patch(
            "moon_rental.services.rentals.timezone.now",
            return_value=auction.ends_at - timedelta(seconds=30),
        ),
    ):
        bid = place_bid(actor=other_pilot, lot_id=lot.pk, amount="101")
    auction.refresh_from_db()
    notice = Notification.objects.get(key=f"outbid:{bid.pk}")
    assert f"Bidding closes {auction.ends_at:%d/%m/%Y %H:%M} UTC" in notice.message
    closing = next(field for field in notice.embed["fields"] if field["name"] == "Bidding closes")
    assert f"<t:{int(auction.ends_at.timestamp())}:R>" in closing["value"]


def test_bid_redirect_shows_rule_and_extended_deadline(client, auction, pilot):
    original_end = auction.ends_at
    client.force_login(pilot)
    with patch(
        "moon_rental.services.rentals.timezone.now",
        return_value=original_end - timedelta(seconds=30),
    ):
        response = client.post(
            reverse("moon_rental:bid", args=[auction.lots.get().pk]),
            {"amount": "100"},
            follow=True,
        )
    auction.refresh_from_db()
    assert response.status_code == 200
    assert auction.ends_at == original_end + timedelta(seconds=120)
    assert response.context["bidding_open"]
    assert response.context["auction"].ends_at == auction.ends_at
    html = response.content.decode()
    assert "Anti-sniping:" in html
    assert "last 120 seconds adds 120 seconds" in html
    assert f"{auction.ends_at:%d/%m/%Y %H:%M} UTC" in html
