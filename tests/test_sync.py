from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.utils import timezone
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
from moonmining.models import EveOreType, EveOreTypeExtras, MoonProduct
from moonmining.models import Moon as MiningMoon

from moon_rental.models import Moon, SurveyOre
from moon_rental.services.sync import sync_moons

pytestmark = pytest.mark.django_db


def test_real_moonmining_adapter(admin_user, moon):
    category = EveCategory.objects.create(id=25, name="Asteroid", published=True)
    group = EveGroup.objects.create(
        id=1884, name="Exceptional Moon Asteroids", eve_category=category, published=True
    )
    ore = EveOreType.objects.create(
        id=45506, name="Loparite", eve_group=group, published=True, volume=10
    )
    EveOreTypeExtras.objects.create(ore_type=ore, current_price=2.5)
    planet_type = EveType.objects.create(id=11, name="Planet", eve_group=group, published=True)
    region = EveRegion.objects.create(id=10000003, name="Vale of the Silent")
    constellation = EveConstellation.objects.create(id=20000001, name="Test", eve_region=region)
    system = EveSolarSystem.objects.create(
        id=30000001, name="J-A5QD", eve_constellation=constellation, security_status=-0.5
    )
    planet = EvePlanet.objects.create(
        id=40000001, name="J-A5QD IV", eve_solar_system=system, eve_type=planet_type
    )
    eve_moon = EveMoon.objects.create(id=40000002, name="J-A5QD IV - Moon 2", eve_planet=planet)
    source = MiningMoon.objects.create(eve_moon=eve_moon, rarity_class=64, value=123456.789)
    MoonProduct.objects.create(moon=source, ore_type=ore, amount=0.35)
    assert sync_moons(actor=admin_user) == 1
    copy = Moon.objects.get(pk=eve_moon.pk)
    assert copy.name == "J-A5QD IV - 2" and copy.solar_system == "J-A5QD"
    assert copy.constellation == "Test"
    assert list(copy.survey_ores.values_list("type_id", "name")) == [(45506, "Loparite")]
    assert copy.composition == [{"type_id": 45506, "name": "Loparite", "percent": 35.0}]
    assert str(copy.estimated_value) == "123456.79"
    assert copy.pull_30d["ores"] == [{"name": "Loparite", "units": 1008420}]
    assert copy.pull_30d["value"] == "2521050.00"
    # Existing installations can recover the same filters without a fresh ESI sync.
    SurveyOre.objects.all().delete()
    Moon.objects.filter(pk=copy.pk).update(constellation="")
    populate = import_module("moon_rental.migrations.0004_moon_filters").populate_filters
    populate(apps, SimpleNamespace(connection=connection))
    copy.refresh_from_db()
    assert copy.constellation == "Test"
    assert list(copy.survey_ores.values_list("type_id", flat=True)) == [45506]
    moon.refresh_from_db()
    assert not moon.available
    source.value = 999
    source.save()
    assert sync_moons(actor=admin_user) == 1
    assert Moon.objects.count() == 2
    source.products.all().update(amount=0)
    sync_moons(actor=admin_user)
    assert not copy.survey_ores.exists()
    copy.refresh_from_db()
    assert copy.pull_30d == {}


def test_sync_restricted(manager):
    with pytest.raises(PermissionDenied):
        sync_moons(actor=manager)


def test_empty_source_marks_unavailable(admin_user):
    moon = Moon.objects.create(
        id=42, name="Removed", solar_system="A", region="B", synced_at=timezone.now()
    )
    assert sync_moons(actor=admin_user) == 0
    moon.refresh_from_db()
    assert not moon.available
