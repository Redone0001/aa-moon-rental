import json
from datetime import date, datetime, timedelta
from datetime import timezone as tz
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from moon_rental.models import Auction, AuctionLot, Invoice, Moon, Rental
from moon_rental.services.billing import issue_invoices
from moon_rental.services.rentals import assign_moon, open_auction, place_bid, settle_auction
from moon_rental.services.reporting import monthly_summary
from moon_rental.services.wallets import record_payment
from tests.test_wallets import entry

pytestmark = pytest.mark.django_db


def test_scheduled_bidding_boundaries_and_public_prices(client, manager, pilot, moon, wallet):
    start = timezone.now() + timedelta(days=1)
    auction = open_auction(
        actor=manager,
        title="Scheduled",
        moon_ids=[moon.pk],
        bidding_starts_at=start,
        ends_at=start + timedelta(days=2),
        starts_on=(start + timedelta(days=3)).date(),
        duration_months=3,
        minimum_bid="100",
        starting_prices={moon.pk: "250.01"},
    )
    lot = auction.lots.get()
    client.force_login(pilot)
    html = client.get(reverse("moon_rental:auction", args=[auction.pk])).content.decode()
    assert "Scheduled" in html and "250,01 ISK" in html and "Place bid" not in html
    with pytest.raises(ValidationError, match="not opened"):
        place_bid(actor=pilot, lot_id=lot.pk, amount="250.01")
    with patch("moon_rental.services.rentals.timezone.now", return_value=start):
        with pytest.raises(ValidationError, match="minimum"):
            place_bid(actor=pilot, lot_id=lot.pk, amount="250")
        place_bid(actor=pilot, lot_id=lot.pk, amount="250.01")
    with patch("moon_rental.services.rentals.timezone.now", return_value=auction.ends_at):
        with pytest.raises(ValidationError, match="closed"):
            place_bid(actor=pilot, lot_id=lot.pk, amount="350.01")
    assert settle_auction(auction.pk, at=auction.ends_at) == 1
    assert Rental.objects.get().price == lot.starting_price


@pytest.mark.parametrize(
    "changes",
    [
        {"starting_prices": {999: "100"}},
        {"starting_prices": {40000123: "NaN"}},
        {"reminder_hours": [2]},
        {"reminder_hours": [True]},
    ],
)
def test_invalid_auction_terms_roll_back(manager, moon, wallet, changes):
    with pytest.raises(ValidationError):
        open_auction(
            actor=manager,
            title="Invalid",
            moon_ids=[moon.pk],
            ends_at=timezone.now() + timedelta(days=1),
            starts_on=(timezone.now() + timedelta(days=2)).date(),
            duration_months=1,
            **changes,
        )
    assert not Auction.objects.exists()
    moon.refresh_from_db()
    assert not moon.reserved_lot_id


def test_invalid_schedule(manager, moon, wallet):
    deadline = timezone.now() + timedelta(days=1)
    with pytest.raises(ValidationError, match="before the deadline"):
        open_auction(
            actor=manager,
            title="Invalid",
            moon_ids=[moon.pk],
            ends_at=deadline,
            bidding_starts_at=deadline,
            starts_on=(deadline + timedelta(days=1)).date(),
            duration_months=1,
        )


def test_prices_and_schedule_survive_filter_and_save(client, manager, moon, wallet):
    client.force_login(manager)
    url = reverse("moon_rental:create_auction")
    initial_prices = client.get(url).context["form"]["starting_prices"].value()
    data = {
        "action": "filter",
        "moons": str(moon.pk),
        "title": "Public prices",
        "starts_on": "03/02/2030",
        "ends_at": "02/02/2030 18:00",
        "bidding_starts_at": "01/02/2030 18:00",
        "minimum_bid": "100",
        "duration_months": 3,
        "billing_mode": "monthly",
        "reminder_hours": [12, 1],
        f"starting_price_{moon.pk}": "250000000.01",
        "starting_prices": initial_prices,
    }
    response = client.post(url, data)
    assert not response.context["form"].errors
    assert response.context["form"]["bidding_starts_at"].value() == data["bidding_starts_at"]
    prices = response.context["form"]["starting_prices"].value()
    assert json.loads(prices)[str(moon.pk)] == "250000000.01"
    data.pop(f"starting_price_{moon.pk}")
    data.update(action="save", starting_prices=prices)
    assert client.post(url, data).status_code == 302
    auction = Auction.objects.get()
    assert auction.reminder_hours == [12, 1]
    assert str(auction.lots.get().starting_price) == "250000000.01"


def test_reuse_is_paginated_and_skips_unavailable_moons(client, auction, manager, moon):
    other = Moon.objects.create(id=1, name="Available", synced_at=timezone.now())
    AuctionLot.objects.create(auction=auction, moon=other, starting_price="300")
    # A completed source auction can contain moons still rented or reserved elsewhere.
    client.force_login(manager)
    for i in range(12):
        Auction.objects.create(
            title=f"History {i}",
            ends_at=auction.ends_at,
            starts_on=auction.starts_on,
            duration_months=1,
            minimum_bid=1,
            created_by=manager,
        )
    url = reverse("moon_rental:create_auction")
    response = client.get(url)
    assert len(response.context["previous_auctions"]) == 5
    titles = list(
        Auction.objects.order_by("-created_at", "-pk").values_list("title", flat=True)[:5]
    )
    assert [a.title for a in response.context["previous_auctions"]] == titles
    response = client.post(url, {"reuse_auction": auction.pk, "title": "Keep terms"})
    assert response.context["form"]["moons"].value() == [str(other.pk)]
    assert (
        json.loads(response.context["form"]["starting_prices"].value())[str(other.pk)] == "300.00"
    )
    assert response.context["form"]["title"].value() == "Keep terms"
    response = client.post(url, {"action": "search_history", "previous_q": "no match"})
    assert (
        b"No auctions match" in response.content and b"Search previous auctions" in response.content
    )


@pytest.mark.parametrize("name", ["admin_overview", "invoice_ledger"])
def test_financial_admin_pages_are_restricted(client, manager, pilot, admin_user, name):
    url = reverse(f"moon_rental:{name}")
    for user in [manager, pilot]:
        client.force_login(user)
        assert client.get(url).status_code == 403
    client.force_login(admin_user)
    assert client.get(url).status_code == 200


def test_monthly_summary_combines_concurrent_terms_and_partial_payments(
    manager, admin_user, pilot, moon, wallet
):
    at = datetime(2030, 2, 1, tzinfo=tz.utc)
    other = Moon.objects.create(id=1, name="Other period", synced_at=timezone.now())
    earlier = datetime(2030, 1, 1, tzinfo=tz.utc)
    a = assign_moon(
        actor=manager, moon_id=moon.pk, renter=pilot, price="100", duration_months=4, at=earlier
    )
    b = assign_moon(
        actor=manager, moon_id=other.pk, renter=pilot, price="200", duration_months=2, at=at
    )
    # The first lease's February invoice has not yet been issued by the worker.
    invoice = b.invoices.get()
    assert record_payment(wallet.pk, entry(invoice, wallet, amount="50"))
    report = monthly_summary(date(2030, 2, 1), actor=admin_user, at=at)
    assert report["predicted"] == 300 and report["paid"] == 50
    assert report["monthly_income"] == 300 and report["rented_count"] == 2
    assert report["paid_percent"] == 16.7 or str(report["paid_percent"]) == "16.7"
    assert report["open_count"] == 2
    a.ended_at = datetime(2030, 1, 31, tzinfo=tz.utc)
    a.save()
    assert monthly_summary(date(2030, 2, 1), actor=admin_user, at=at)["predicted"] == 200
    with pytest.raises(PermissionDenied):
        monthly_summary(date(2030, 2, 1), actor=manager)


def test_voids_one_time_charges_and_overpayments(admin_user, manager, pilot, moon, wallet):
    at = datetime(2030, 2, 12, tzinfo=tz.utc)
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        billing_mode="once",
        at=at,
    )
    invoice = rental.invoices.get()
    assert record_payment(wallet.pk, entry(invoice, wallet, amount="150"))
    summary = monthly_summary(date(2030, 2, 1), actor=admin_user, at=at)
    assert summary["paid"] == 100 and summary["paid_percent"] == 100
    assert summary["monthly_income"] == 0
    assert monthly_summary(date(2030, 3, 1), actor=admin_user, at=at)["predicted"] == 0
    invoice.status = Invoice.Status.VOID
    invoice.save()
    assert monthly_summary(date(2030, 2, 1), actor=admin_user, at=at)["predicted"] == 0


def test_monthly_forecast_uses_start_date_and_short_month_anniversaries(
    admin_user, manager, pilot, moon, wallet
):
    auction = open_auction(
        actor=manager,
        title="Month-end forecast",
        moon_ids=[moon.pk],
        ends_at=datetime(2030, 1, 1, tzinfo=tz.utc),
        starts_on=date(2030, 1, 31),
        duration_months=3,
    )
    place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="100")
    settle_auction(auction.pk, at=auction.ends_at)
    rental = Rental.objects.get()
    assert not rental.invoices.exists()
    for month in [1, 2, 3]:
        summary = monthly_summary(date(2030, month, 1), actor=admin_user)
        assert summary["predicted"] == 100 and summary["paid"] == 0
    assert monthly_summary(date(2030, 4, 1), actor=admin_user)["predicted"] == 0
    assert issue_invoices(rental.pk, at=datetime(2030, 1, 31, tzinfo=tz.utc)) == 1
    assert monthly_summary(date(2030, 1, 1), actor=admin_user)["predicted"] == 100
    rental.invoices.update(status=Invoice.Status.VOID)
    assert monthly_summary(date(2030, 1, 1), actor=admin_user)["predicted"] == 0
    rental.invoices.all().delete()
    assert monthly_summary(date(2030, 1, 1), actor=admin_user)["predicted"] == 0
    # A legacy first-of-month cursor covers February, even without a retained invoice.
    Rental.objects.filter(pk=rental.pk).update(billed_through=date(2030, 2, 1))
    assert monthly_summary(date(2030, 2, 1), actor=admin_user)["predicted"] == 0
    assert monthly_summary(date(2030, 3, 1), actor=admin_user)["predicted"] == 100


@pytest.mark.parametrize("end_day,expected", [(27, 0), (28, 100)])
def test_monthly_forecast_respects_early_end_before_anniversary(
    admin_user, manager, pilot, moon, wallet, end_day, expected
):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=datetime(2030, 1, 31, tzinfo=tz.utc),
    )
    # Delayed billing must use the same cutoff as the forecast for ended leases.
    Rental.objects.filter(pk=rental.pk).update(ended_at=datetime(2030, 2, end_day, tzinfo=tz.utc))
    assert monthly_summary(date(2030, 2, 1), actor=admin_user)["predicted"] == expected
    assert issue_invoices(rental.pk, at=datetime(2030, 5, 1, tzinfo=tz.utc)) == expected // 100
    assert monthly_summary(date(2030, 2, 1), actor=admin_user)["predicted"] == expected


def test_history_outcomes_and_ledger_filters(client, admin_user, manager, auction, pilot, wallet):
    other = Moon.objects.create(id=2, name="Unrented", synced_at=timezone.now())
    lot = AuctionLot.objects.create(auction=auction, moon=other, starting_price=100)
    other.reserved_lot = lot
    other.save()
    place_bid(actor=pilot, lot_id=auction.lots.get(moon__name__startswith="J-A").pk, amount=100)
    settle_auction(auction.pk, at=auction.ends_at)
    client.force_login(admin_user)
    response = client.get(reverse("moon_rental:admin_overview"))
    past = response.context["past_auctions"][0]
    assert past.rented_count == 1 and past.unrented_count == 1
    response = client.get(reverse("moon_rental:auction", args=[auction.pk]) + "?outcome=unrented")
    assert [row["lot"].moon for row in response.context["rows"]] == [other]
    assign_moon(
        actor=manager,
        moon_id=other.pk,
        renter=pilot,
        price=100,
        duration_months=1,
        billing_mode="once",
    )
    invoice = Invoice.objects.get()
    response = client.get(
        reverse("moon_rental:invoice_ledger") + f"?month={invoice.period:%Y-%m}&q={invoice.code}"
    )
    assert response.context["invoices"].paginator.count == 1
    assert invoice.code.encode() in response.content and b"Astra Vale" in response.content


def test_migration_preserves_existing_minimums(auction):
    auction.lots.update(starting_price=1)
    migrate = import_module(
        "moon_rental.migrations.0006_auction_automation"
    ).preserve_starting_prices
    migrate(apps, SimpleNamespace(connection=connection))
    assert auction.lots.get().starting_price == auction.minimum_bid
