import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from moon_rental.services.rentals import assign_moon

pytestmark = pytest.mark.django_db


def catalogue_url(name, moon):
    return reverse(f"moon_rental:{name}", args=[moon.pk] if name == "moon" else [])


@pytest.mark.parametrize("name", ["moons", "moon", "moon_export"])
@pytest.mark.parametrize("role", ["manager", "admin_user", "superuser"])
def test_catalogue_is_accessible_to_managers_and_admins(client, request, moon, name, role):
    user = (
        User.objects.create_superuser("superuser", password=None)
        if role == "superuser"
        else request.getfixturevalue(role)
    )
    client.force_login(user)
    response = client.get(catalogue_url(name, moon))
    assert response.status_code == 200
    response.close()


@pytest.mark.parametrize("name", ["moons", "moon", "moon_export"])
@pytest.mark.parametrize("role", ["member", "staff", "outsider"])
def test_catalogue_direct_urls_are_forbidden_without_manager_permission(
    client, pilot, moon, name, role
):
    if role == "staff":
        pilot.is_staff = True
        pilot.save(update_fields=["is_staff"])
    user = User.objects.create_user("outsider") if role == "outsider" else pilot
    client.force_login(user)
    url = catalogue_url(name, moon)
    assert client.get(url).status_code == 403
    assert client.post(url).status_code == 403


@pytest.mark.parametrize("name", ["moons", "moon", "moon_export"])
def test_anonymous_and_inactive_managers_cannot_access_catalogue(client, manager, moon, name):
    url = catalogue_url(name, moon)
    assert client.get(url).status_code == 302
    manager.is_active = False
    manager.save(update_fields=["is_active"])
    client.force_login(manager)
    assert client.get(url).status_code == 302


@pytest.mark.parametrize("role", ["pilot", "manager", "admin_user"])
def test_auction_catalogue_links_follow_permissions(client, request, auction, moon, role):
    client.force_login(request.getfixturevalue(role))
    response = client.get(reverse("moon_rental:auction", args=[auction.pk]))
    assert response.status_code == 200
    assert moon.name.encode() in response.content
    assert b"Loparite" in response.content
    for url in (reverse("moon_rental:moons"), reverse("moon_rental:moon", args=[moon.pk])):
        assert (f'href="{url}"'.encode() in response.content) == (role != "pilot")
    if role == "pilot":
        assert b"Place bid" in response.content


@pytest.mark.parametrize("role", ["pilot", "manager", "admin_user"])
def test_personal_rentals_remain_accessible_with_appropriate_moon_links(
    client, request, manager, wallet, moon, role
):
    user = request.getfixturevalue(role)
    assign_moon(actor=manager, renter=user, moon_id=moon.pk, price="100", duration_months=1)
    client.force_login(user)
    response = client.get(reverse("moon_rental:my_moons"))
    assert response.status_code == 200
    assert moon.name.encode() in response.content
    for url in (reverse("moon_rental:moons"), reverse("moon_rental:moon", args=[moon.pk])):
        assert (f'href="{url}"'.encode() in response.content) == (role != "pilot")
