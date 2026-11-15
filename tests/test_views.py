import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from moon_rental.services.rentals import assign_moon

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "name",
    ["index", "moons", "my_moons", "invoices", "rentals", "wallets", "create_auction", "assign"],
)
def test_real_aa_pages_render(client, admin_user, wallet, moon, name):
    client.force_login(admin_user)
    response = client.get(reverse(f"moon_rental:{name}"))
    assert response.status_code == 200
    assert b"Moon Rental" in response.content
    assert "allianceauth/base-bs5.html" in [t.name for t in response.templates]


def test_auction_shows_bidder_and_composition(client, auction, pilot):
    from moon_rental.services.rentals import place_bid

    place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="123.45")
    client.force_login(pilot)
    response = client.get(reverse("moon_rental:auction", args=[auction.pk]))
    assert response.status_code == 200
    assert (
        b"Astra Vale" in response.content
        and b"123.45" in response.content
        and b"Loparite" in response.content
    )


def test_user_only_sees_own_invoices(client, manager, pilot, other_pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=1,
        billing_mode="once",
    )
    code = rental.invoices.get().code.encode()
    client.force_login(other_pilot)
    assert code not in client.get(reverse("moon_rental:invoices")).content
    client.force_login(pilot)
    response = client.get(reverse("moon_rental:invoices"))
    assert code in response.content and b"Copy amount" in response.content


@pytest.mark.parametrize("name", ["wallets", "connect_character", "connect_corporation", "sync"])
def test_manager_cannot_access_admin_endpoints(client, manager, name):
    client.force_login(manager)
    assert client.post(reverse(f"moon_rental:{name}")).status_code == 403


def test_manager_cannot_end_assignment(client, manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager, moon_id=moon.pk, renter=pilot, price="100", duration_months=1
    )
    client.force_login(manager)
    assert (
        client.post(
            reverse("moon_rental:end_assignment", args=[rental.pk]), {"reason": "bypass"}
        ).status_code
        == 403
    )


def test_csrf_and_post_required(pilot, auction):
    client = Client(enforce_csrf_checks=True)
    client.force_login(pilot)
    url = reverse("moon_rental:bid", args=[auction.lots.get().pk])
    assert client.get(url).status_code == 405
    assert client.post(url, {"amount": "1000"}).status_code == 403


def test_unauthorized_logged_in_user_rejected(client):
    client.force_login(User.objects.create_user("outsider"))
    assert client.get(reverse("moon_rental:index")).status_code == 403


def test_anonymous_redirects(client):
    assert client.get(reverse("moon_rental:index")).status_code == 302


def test_payment_amounts_remain_machine_readable_in_other_locales(
    client, manager, pilot, wallet, moon
):
    from django.utils.translation import override

    assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="123.45",
        duration_months=1,
        billing_mode="once",
    )
    client.force_login(pilot)
    with override("fr"):
        response = client.get(reverse("moon_rental:invoices"), HTTP_ACCEPT_LANGUAGE="fr")
    assert b'data-copy="123.45"' in response.content
