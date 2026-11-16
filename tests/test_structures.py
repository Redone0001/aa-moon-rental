from copy import copy

import pytest
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import reverse
from eveuniverse.models import (
    EveCategory,
    EveConstellation,
    EveGroup,
    EveMoon,
    EvePlanet,
    EveRegion,
    EveSolarSystem,
    EveType,
)
from structures.models import Owner, Structure

from moon_rental.models import Moon
from moon_rental.services.rentals import assign_moon
from moon_rental.services.structures import attach_moon_structures

pytestmark = pytest.mark.django_db


def grant_structure_access(user, visibility="view_all_structures"):
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="structures", codename__in=["basic_access", visibility]
        )
    )
    for cache in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(cache, None)


@pytest.fixture
def structure(moon):
    category = EveCategory.objects.create(id=65, name="Structure", published=True)
    group = EveGroup.objects.create(id=1406, name="Refinery", eve_category=category, published=True)
    drill = EveType.objects.create(
        id=81826, name="Metenox Moon Drill", eve_group=group, published=True
    )
    region = EveRegion.objects.create(id=10000003, name=moon.region)
    constellation = EveConstellation.objects.create(
        id=20000001, name=moon.constellation, eve_region=region
    )
    system = EveSolarSystem.objects.create(
        id=30000001, name=moon.solar_system, eve_constellation=constellation, security_status=-0.5
    )
    planet = EvePlanet.objects.create(
        id=40000001, name="Demo IV", eve_solar_system=system, eve_type=drill
    )
    eve_moon = EveMoon.objects.create(id=moon.pk, name=moon.name, eve_planet=planet)
    corporation = EveCorporationInfo.objects.create(
        corporation_id=98000001, corporation_name="Demo Mining", corporation_ticker="DEMO"
    )
    owner = Owner.objects.create(corporation=corporation)
    return Structure.objects.create(
        id=1000000000001,
        name="My metenox name",
        eve_type=drill,
        eve_moon=eve_moon,
        eve_solar_system=system,
        owner=owner,
    )


@pytest.mark.parametrize("name", ["moons", "moon", "assign", "create_auction"])
def test_catalogue_and_selection_show_structure_type_and_name(
    client, manager, moon, structure, name
):
    grant_structure_access(manager)
    client.force_login(manager)
    url = reverse(f"moon_rental:{name}", args=[moon.pk] if name == "moon" else [])
    response = client.get(url)
    assert response.status_code == 200
    assert 'Metenox Moon Drill "My metenox name"' in response.content.decode()


@pytest.mark.parametrize("name", ["rentals", "my_moons"])
def test_assignments_show_current_structure_without_resync(
    client, manager, pilot, moon, wallet, structure, name
):
    assign_moon(actor=manager, renter=pilot, moon_id=moon.pk, price="100", duration_months=1)
    viewer = pilot if name == "my_moons" else manager
    grant_structure_access(viewer)
    client.force_login(viewer)
    url = reverse(f"moon_rental:{name}")
    assert 'Metenox Moon Drill "My metenox name"' in client.get(url).content.decode()
    structure.name = "Renamed drill"
    structure.save(update_fields=["name"])
    html = client.get(url).content.decode()
    assert 'Metenox Moon Drill "Renamed drill"' in html
    assert "My metenox name" not in html
    structure.delete()
    html = client.get(url).content.decode()
    assert "Renamed drill" not in html
    assert "No visible structure" in html


def test_matches_ids_and_shows_multiple_structures_in_one_query(
    manager, moon, structure, django_assert_num_queries
):
    grant_structure_access(manager)
    manager.get_all_permissions()
    other = Moon(id=42, name=moon.name, synced_at=moon.synced_at)
    other_eve_moon = EveMoon.objects.create(
        id=other.pk, name=moon.name, eve_planet=structure.eve_moon.eve_planet
    )
    athanor = EveType.objects.create(
        id=35835, name="Athanor", eve_group=structure.eve_type.eve_group, published=True
    )
    Structure.objects.create(
        id=structure.pk + 1,
        name="Second structure",
        eve_type=athanor,
        eve_moon=structure.eve_moon,
        eve_solar_system=structure.eve_solar_system,
        owner=structure.owner,
    )
    Structure.objects.create(
        id=structure.pk + 2,
        name="Other moon's drill",
        eve_type=structure.eve_type,
        eve_moon=other_eve_moon,
        eve_solar_system=structure.eve_solar_system,
        owner=structure.owner,
    )
    Structure.objects.create(
        id=structure.pk + 3,
        name="Unlinked structure",
        eve_type=structure.eve_type,
        eve_solar_system=structure.eve_solar_system,
        owner=structure.owner,
    )
    duplicate = copy(moon)  # Multiple historical assignments can share the same moon.
    with django_assert_num_queries(1):
        attach_moon_structures([moon, other, duplicate], user=manager)
    assert [s["name"] for s in moon.visible_structures] == ["Second structure", structure.name]
    assert duplicate.visible_structures == moon.visible_structures
    assert other.visible_structures == [
        {"name": "Other moon's drill", "type_name": "Metenox Moon Drill"}
    ]


@pytest.mark.parametrize("permission", [None, "basic_access", "view_all_structures"])
def test_rental_permissions_alone_do_not_expose_structures(client, manager, structure, permission):
    if permission:
        manager.user_permissions.add(
            Permission.objects.get(content_type__app_label="structures", codename=permission)
        )
    client.force_login(manager)
    html = client.get(reverse("moon_rental:moons")).content.decode()
    assert structure.name not in html
    assert "Metenox Moon Drill" not in html


def test_corporation_visibility_uses_upstream_rules(client, manager, moon, structure):
    grant_structure_access(manager, "view_corporation_structures")
    client.force_login(manager)
    url = reverse("moon_rental:moons")
    assert structure.name not in client.get(url).content.decode()
    character = EveCharacter.objects.create(
        character_id=90000001,
        character_name="Demo Pilot",
        corporation_id=structure.owner.corporation.corporation_id,
    )
    CharacterOwnership.objects.create(user=manager, character=character, owner_hash="demo")
    assert structure.name in client.get(url).content.decode()


def test_missing_optional_app_never_queries_structures(
    client, manager, moon, structure, settings, django_assert_num_queries
):
    grant_structure_access(manager)
    installed = [app for app in settings.INSTALLED_APPS if app != "structures"]
    with override_settings(INSTALLED_APPS=installed):
        with django_assert_num_queries(0):
            attach_moon_structures([moon], user=manager)
        assert not moon.structure_info_available
        assert moon.visible_structures == []
        client.force_login(manager)
        response = client.get(reverse("moon_rental:moons"))
        assert response.status_code == 200
        assert structure.name not in response.content.decode()


def test_structure_names_are_escaped(client, manager, structure):
    grant_structure_access(manager)
    structure.name = '<script>alert("structure")</script>'
    structure.save(update_fields=["name"])
    client.force_login(manager)
    html = client.get(reverse("moon_rental:moons")).content.decode()
    assert structure.name not in html
    assert "&lt;script&gt;alert(&quot;structure&quot;)&lt;/script&gt;" in html


def test_off_page_assignment_selection_keeps_structure_details(client, manager, moon, structure):
    grant_structure_access(manager)
    client.force_login(manager)
    response = client.post(
        reverse("moon_rental:assign"),
        {
            "action": "filter",
            "moon": moon.pk,
            "filter-q": "unmatched system",
        },
    )
    assert response.status_code == 200
    assert "Selected outside this page" in response.content.decode()
    assert structure.name in response.content.decode()
