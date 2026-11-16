from datetime import date, datetime, timedelta
from datetime import timezone as tz
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse

from moon_rental.forms import AuctionForm
from moon_rental.models import Auction, Rental
from moon_rental.services.maintenance import run_lifecycle
from moon_rental.services.rentals import open_auction, place_bid, settle_auction

pytestmark = pytest.mark.django_db


def test_form_requires_rent_start_above_bid_end(client, manager):
    client.force_login(manager)
    response = client.get(reverse("moon_rental:create_auction"))
    html = response.content.decode()
    assert html.index('name="starts_on"') < html.index('name="ends_at"')
    assert 'placeholder="dd/mm/yyyy"' in html
    assert 'placeholder="dd/mm/yyyy hh:mm"' in html
    form = AuctionForm(RequestFactory().post("/", {"ends_at": "01/01/2030 23:30"}).POST)
    assert not form.is_valid() and "starts_on" in form.errors


@pytest.mark.parametrize("start", ["31/12/2029", "01/01/2030"])
def test_form_rejects_start_before_bid_end(start):
    form = AuctionForm(
        RequestFactory().post("/", {"starts_on": start, "ends_at": "01/01/2030 23:30"}).POST
    )
    assert not form.is_valid()
    assert "Start rent must be a date after" in form.errors["starts_on"][0]


def test_day_first_dates_are_saved_and_visible_to_bidders(client, manager, pilot, moon, wallet):
    client.force_login(manager)
    response = client.post(
        reverse("moon_rental:create_auction"),
        {
            "action": "save",
            "moons": moon.pk,
            "title": "Setup window",
            "starts_on": "03/02/2030",
            "ends_at": "01/02/2030 18:30",
            "duration_months": 3,
            "billing_mode": "monthly",
            "minimum_bid": "100",
        },
    )
    assert response.status_code == 302
    auction = Auction.objects.get()
    assert auction.starts_on == date(2030, 2, 3)
    assert auction.ends_at == datetime(2030, 2, 1, 18, 30, tzinfo=tz.utc)
    client.force_login(pilot)
    html = client.get(response.url).content.decode()
    assert "03/02/2030" in html and "01/02/2030 18:30" in html


@pytest.mark.parametrize("start", [None, date(2030, 1, 1), datetime(2030, 1, 3, tzinfo=tz.utc)])
def test_service_rejects_invalid_start(manager, moon, wallet, start):
    with pytest.raises(ValidationError, match="Start rent"):
        open_auction(
            actor=manager,
            title="Invalid start",
            moon_ids=[moon.pk],
            ends_at=datetime(2030, 1, 1, 18, tzinfo=tz.utc),
            starts_on=start,
            duration_months=1,
        )
    assert not Auction.objects.exists()
    moon.refresh_from_db()
    assert moon.reserved_lot_id is None


@pytest.mark.parametrize("mode", ["monthly", "once"])
def test_award_reserves_moon_without_billing_before_rent_start(
    manager, pilot, moon, wallet, client, mode
):
    auction = open_auction(
        actor=manager,
        title="Setup window",
        moon_ids=[moon.pk],
        ends_at=datetime(2030, 1, 28, 18, tzinfo=tz.utc),
        starts_on=date(2030, 2, 1),
        duration_months=2,
        billing_mode=mode,
    )
    place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")
    assert settle_auction(auction.pk, at=auction.ends_at) == 1
    rental = Rental.objects.get()
    assert rental.starts_on == date(2030, 2, 1)
    assert rental.ends_on == date(2030, 4, 1)
    assert not rental.invoices.exists()
    moon.refresh_from_db()
    assert moon.current_rental_id == rental.pk
    client.force_login(pilot)
    assert b"Scheduled" in client.get(reverse("moon_rental:my_moons")).content
    run_lifecycle(at=datetime(2030, 2, 1, tzinfo=tz.utc))
    assert rental.invoices.get().period == date(2030, 2, 1)
    run_lifecycle(at=datetime(2030, 2, 1, tzinfo=tz.utc))
    assert rental.invoices.count() == 1


def test_delayed_award_uses_rent_start_and_catches_up_invoices(manager, pilot, moon, wallet):
    auction = open_auction(
        actor=manager,
        title="Delayed worker",
        moon_ids=[moon.pk],
        ends_at=datetime(2030, 1, 1, tzinfo=tz.utc),
        starts_on=date(2030, 1, 31),
        duration_months=1,
    )
    place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")
    assert settle_auction(auction.pk, at=datetime(2030, 3, 15, tzinfo=tz.utc)) == 1
    rental = Rental.objects.get()
    assert rental.starts_on == date(2030, 1, 31)
    assert rental.ends_on == date(2030, 2, 28)
    assert rental.invoices.get().period == date(2030, 2, 1)


def test_start_validation_uses_deadline_utc_day(manager, moon, wallet):
    auction = open_auction(
        actor=manager,
        title="UTC date",
        moon_ids=[moon.pk],
        ends_at=datetime(2030, 1, 2, 0, 30, tzinfo=tz(timedelta(hours=2))),
        starts_on=date(2030, 1, 2),
        duration_months=1,
    )
    assert auction.starts_on == date(2030, 1, 2)


def test_migration_preserves_legacy_deadline_start(auction):
    preserve = import_module(
        "moon_rental.migrations.0005_auction_start_and_pull_estimate"
    ).preserve_auction_starts
    preserve(apps, SimpleNamespace(connection=connection))
    auction.refresh_from_db()
    assert auction.starts_on == auction.ends_at.astimezone(tz.utc).date()
