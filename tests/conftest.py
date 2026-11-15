from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission, User
from django.utils import timezone

from moon_rental.models import Moon
from moon_rental.services.wallets import register_wallet


def make_user(name, permission):
    user = User.objects.create_user(name)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="moon_rental", codename=permission)
    )
    return user


@pytest.fixture
def admin_user(db):
    return make_user("rental-admin", "administer_rentals")


@pytest.fixture
def manager(db):
    return make_user("rental-manager", "manage_rentals")


@pytest.fixture
def pilot(db):
    return make_user("Astra Vale", "basic_access")


@pytest.fixture
def other_pilot(db):
    return make_user("Orion Drake", "basic_access")


@pytest.fixture
def wallet(admin_user):
    return register_wallet(
        actor=admin_user,
        kind="corporation",
        eve_id=98000001,
        name="Orbital Industries",
        character_id=90000001,
    )


@pytest.fixture
def moon(db):
    return Moon.objects.create(
        id=40000123,
        name="J-A5QD IV - 12",
        solar_system="J-A5QD",
        region="Vale of the Silent",
        rarity=64,
        synced_at=timezone.now(),
        composition=[
            {"type_id": 45506, "name": "Loparite", "percent": 35},
            {"type_id": 45511, "name": "Monazite", "percent": 25},
            {"type_id": 45492, "name": "Bitumens", "percent": 40},
        ],
    )


@pytest.fixture
def auction(manager, moon, wallet):
    from moon_rental.services.rentals import open_auction

    return open_auction(
        actor=manager,
        title="Winter moon programme",
        moon_ids=[moon.pk],
        ends_at=timezone.now() + timedelta(days=2),
        duration_months=3,
        minimum_bid="100",
    )
