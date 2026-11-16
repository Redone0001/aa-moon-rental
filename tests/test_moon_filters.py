import json
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from django.apps import apps
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from moon_rental.filters import MoonFilters
from moon_rental.models import Auction, AuctionLot, Moon, Rental, SurveyOre
from moon_rental.services.rentals import assign_moon, open_auction, place_bid

pytestmark = pytest.mark.django_db


@pytest.fixture
def surveys(moon):
    other = Moon.objects.create(
        id=moon.pk + 1,
        name="M-OEE8 V - 9",
        solar_system="M-OEE8",
        region="Tribute",
        constellation="4-7M1U",
        rarity=16,
        composition=[{"type_id": 45492, "name": "Bitumens", "percent": 100}],
        synced_at=timezone.now(),
    )
    SurveyOre.objects.bulk_create(
        [
            SurveyOre(moon=moon, type_id=45506, name="Loparite"),
            SurveyOre(moon=moon, type_id=45492, name="Bitumens"),
            SurveyOre(moon=other, type_id=45492, name="Bitumens"),
        ]
    )
    return moon, other


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"region": "Vale of the Silent"}, [0]),
        ({"constellation": "4-7M1U"}, [1]),
        ({"rarity": "64"}, [0]),
        ({"ore": "45506"}, [0]),
        ({"ore": "45492"}, [0, 1]),
        ({"q": "m-oee8"}, [1]),
        ({"region": "Tribute", "rarity": "64"}, []),
        ({"region": "Tribute", "constellation": "4-7M1U", "rarity": "16", "ore": "45492"}, [1]),
    ],
)
def test_combined_survey_filters(surveys, filters, expected):
    moons = Moon.objects.all()
    form = MoonFilters({f"filter-{name}": value for name, value in filters.items()}, moons=moons)
    assert form.is_valid(), form.errors
    assert set(form.apply(moons)) == {surveys[i] for i in expected}


def test_invalid_filters_do_not_silently_show_every_moon(surveys):
    moons = Moon.objects.all()
    form = MoonFilters({"filter-ore": "999999"}, moons=moons)
    assert not form.is_valid()
    assert not form.apply(moons).exists()


@pytest.mark.parametrize("view,field", [("create_auction", "moons"), ("assign", "moon")])
def test_manager_filtering_preserves_choices_and_terms(client, manager, surveys, view, field):
    client.force_login(manager)
    moon, other = surveys
    data = {
        "action": "filter",
        "filter-region": "Tribute",
        field: str(moon.pk),
        "duration_months": "6",
        "billing_mode": "once",
        "price": "1234567890.12",
        "minimum_bid": "987654321.09",
        "title": "Keep this title",
        "ends_at": "01/01/2030 12:30",
        "starts_on": "03/01/2030",
    }
    response = client.post(reverse(f"moon_rental:{view}"), data)
    assert response.status_code == 200
    assert list(response.context["moon_choices"]) == [other]
    assert list(response.context["selected_moons"]) == [moon]
    assert response.context["form"]["duration_months"].value() == "6"
    assert response.context["form"]["billing_mode"].value() == "once"
    assert not response.context["form"].errors
    assert not Auction.objects.exists() and not Rental.objects.exists()
    if field == "moons":
        assert response.context["form"]["minimum_bid"].value() == "987654321.09"
        assert response.context["form"]["ends_at"].value() == "01/01/2030 12:30"
        assert response.context["form"]["starts_on"].value() == "03/01/2030"
    else:
        assert response.context["form"]["price"].value() == "1234567890.12"


def test_auction_selection_survives_pages_and_can_be_removed(client, manager, moon):
    Moon.objects.bulk_create(
        [Moon(id=i, name=f"Extra {i:02}", synced_at=timezone.now()) for i in range(100, 135)]
    )
    client.force_login(manager)
    url = reverse("moon_rental:create_auction")
    first = client.get(url)
    assert len(first.context["moon_choices"]) == 30
    response = client.post(url, {"moon_page": "2", "moons": "100", "title": "Saved"})
    assert response.context["moon_choices"].number == 2
    assert response.context["form"]["moons"].value() == ["100"]
    response = client.post(
        url, {"action": "filter", "moons_kept": '["100"]', "moons": str(moon.pk)}
    )
    assert set(response.context["form"]["moons"].value()) == {"100", str(moon.pk)}
    response = client.post(
        url, {"remove_moon": "100", "moons_kept": json.dumps(["100", str(moon.pk)])}
    )
    assert response.context["form"]["moons"].value() == [str(moon.pk)]
    response = client.post(url, {"action": "clear_selection", "moons_kept": '["100"]'})
    assert response.context["form"]["moons"].value() == []


def test_select_page_and_clear_filters_preserve_selection(client, manager, surveys):
    client.force_login(manager)
    moon, other = surveys
    url = reverse("moon_rental:create_auction")
    response = client.post(
        url,
        {
            "action": "select_page",
            "filter-region": "Tribute",
            "moons_page": json.dumps([str(other.pk)]),
            "moons_kept": json.dumps([str(moon.pk)]),
        },
    )
    assert response.context["selection_count"] == 2
    response = client.post(
        url,
        {"action": "clear_filters", "filter-region": "Tribute", "moons": str(other.pk)},
    )
    assert response.context["moon_choices"].paginator.count == 2
    assert response.context["selection_count"] == 1


def test_hidden_selection_is_validated_against_available_moons(client, manager, surveys, wallet):
    moon, other = surveys
    client.force_login(manager)
    url = reverse("moon_rental:create_auction")
    data = {
        "action": "save",
        "moons_kept": json.dumps([str(moon.pk)]),
        "moons": str(other.pk),
        "title": "Filtered auction",
        "ends_at": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        "starts_on": (timezone.now() + timedelta(days=3)).strftime("%d/%m/%Y"),
        "duration_months": "3",
        "billing_mode": "monthly",
        "minimum_bid": "1234567890.12",
        "filter-region": "Tribute",
    }
    moon.available = False
    moon.save()
    response = client.post(url, data)
    assert "moons" in response.context["form"].errors
    assert not Auction.objects.exists()
    moon.available = True
    moon.save()
    response = client.post(url, data)
    assert response.status_code == 302
    assert set(Auction.objects.get().lots.values_list("moon_id", flat=True)) == {moon.pk, other.pk}


def test_assignment_can_submit_selection_hidden_by_filters(client, manager, pilot, surveys, wallet):
    moon, _ = surveys
    client.force_login(manager)
    response = client.post(
        reverse("moon_rental:assign"),
        {
            "action": "save",
            "filter-region": "Tribute",
            "moon": str(moon.pk),
            "renter": pilot.pk,
            "price": "100",
            "duration_months": "3",
            "billing_mode": "monthly",
        },
    )
    assert response.status_code == 302
    assert Rental.objects.get().moon == moon


def test_unavailable_reserved_and_rented_moons_are_not_offered(
    client, manager, pilot, surveys, wallet
):
    moon, other = surveys
    assign_moon(actor=manager, renter=pilot, moon_id=moon.pk, price="100", duration_months=1)
    open_auction(
        actor=manager,
        title="Reserved",
        moon_ids=[other.pk],
        ends_at=timezone.now() + timedelta(days=1),
        starts_on=(timezone.now() + timedelta(days=2)).date(),
        duration_months=1,
        minimum_bid="1",
    )
    Moon.objects.create(id=3, name="Removed", available=False, synced_at=timezone.now())
    client.force_login(manager)
    for name in ("create_auction", "assign"):
        response = client.get(reverse(f"moon_rental:{name}"))
        assert response.context["moon_choices"].paginator.count == 0


def test_members_cannot_use_manager_selectors(client, pilot, surveys):
    client.force_login(pilot)
    for name in ("create_auction", "assign"):
        assert client.get(reverse(f"moon_rental:{name}")).status_code == 403
        assert client.post(reverse(f"moon_rental:{name}"), {"action": "filter"}).status_code == 403


def test_auction_filters_are_scoped_and_bid_return_keeps_filters(
    client, manager, pilot, surveys, wallet
):
    moon, other = surveys
    auction = open_auction(
        actor=manager,
        title="Two regions",
        moon_ids=[moon.pk, other.pk],
        ends_at=timezone.now() + timedelta(days=1),
        starts_on=(timezone.now() + timedelta(days=2)).date(),
        duration_months=1,
        minimum_bid="1",
    )
    lot = auction.lots.get(moon=moon)
    place_bid(actor=pilot, lot_id=lot.pk, amount="1200000000")
    place_bid(actor=pilot, lot_id=lot.pk, amount="1200000001")
    client.force_login(pilot)
    query = "?filter-region=Vale+of+the+Silent&filter-ore=45506&page=1"
    url = reverse("moon_rental:auction", args=[auction.pk])
    response = client.get(url + query)
    rows = response.context["rows"]
    assert len(rows) == 1 and rows[0]["lot"].moon == moon
    assert rows[0]["count"] == 2 and rows[0]["top"].amount == 1200000001
    response = client.post(
        reverse("moon_rental:bid", args=[lot.pk]) + query, {"amount": "1300000000"}
    )
    assert response.url == url + query


def test_large_auction_is_paginated(client, auction, pilot):
    moons = [Moon(id=i, name=f"Moon {i:03}", synced_at=timezone.now()) for i in range(100, 150)]
    Moon.objects.bulk_create(moons)
    AuctionLot.objects.bulk_create([AuctionLot(auction=auction, moon=m) for m in moons])
    client.force_login(pilot)
    url = reverse("moon_rental:auction", args=[auction.pk])
    response = client.get(url)
    assert len(response.context["rows"]) == 24
    assert response.context["lots"].paginator.count == 51
    assert len(client.get(url + "?page=3").context["rows"]) == 3


def test_bid_redirect_stays_on_auction_with_hostile_query_values(client, auction, pilot):
    client.force_login(pilot)
    query = "?filter-q=https://example.invalid/&next=//example.invalid/&page=1"
    response = client.post(
        reverse("moon_rental:bid", args=[auction.lots.get().pk]) + query, {"amount": "1000"}
    )
    target = urlsplit(response.url)
    assert not target.scheme and not target.netloc
    assert target.path == reverse("moon_rental:auction", args=[auction.pk])


def test_migration_indexes_existing_survey_snapshot(moon):
    moon.composition.append({"type_id": 123, "name": "Zero", "percent": 0})
    moon.save()
    populate = import_module("moon_rental.migrations.0004_moon_filters").populate_filters
    populate(apps, SimpleNamespace(connection=connection))
    assert set(moon.survey_ores.values_list("type_id", flat=True)) == {45506, 45511, 45492}


@pytest.mark.parametrize("kept", ["not json", "{}", '["²"]', '["invalid selection"]'])
def test_malformed_selection_shows_error_and_can_be_cleared(client, manager, kept):
    client.force_login(manager)
    url = reverse("moon_rental:create_auction")
    response = client.post(url, {"action": "save", "moons_kept": kept})
    assert response.status_code == 200 and "moons" in response.context["form"].errors
    response = client.post(url, {"action": "clear_selection", "moons_kept": kept})
    assert response.context["form"]["moons"].value() == []
