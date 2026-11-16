"""Fictional demo records, only for the disposable tests.settings SQLite database."""

import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import Permission, User  # noqa: E402
from django.utils import timezone  # noqa: E402

from moon_rental.models import Auction, Moon  # noqa: E402
from moon_rental.services.billing import issue_invoices  # noqa: E402
from moon_rental.services.rentals import assign_moon, open_auction, place_bid  # noqa: E402
from moon_rental.services.wallets import record_payment, register_wallet  # noqa: E402


def seed():
    if (
        settings.SETTINGS_MODULE != "tests.settings"
        or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
    ):
        raise RuntimeError("Demo seeding is only supported with tests.settings and SQLite.")
    if Auction.objects.exists():
        print("Demo records already exist; leaving them intact.")
        return
    users = {}
    for name, permission in [
        ("Rental Administrator", "administer_rentals"),
        ("Astra Vale", "basic_access"),
        ("Orion Drake", "basic_access"),
        ("Elara Voss", "basic_access"),
    ]:
        user, _ = User.objects.get_or_create(username=name)
        user.set_unusable_password()
        user.save()
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="moon_rental", codename=permission)
        )
        users[name] = user
    admin = users["Rental Administrator"]
    pilot = users["Astra Vale"]
    wallet = register_wallet(
        actor=admin,
        kind="corporation",
        eve_id=98000001,
        name="Orbital Industries",
        character_id=90000001,
    )
    register_wallet(
        actor=admin, kind="character", eve_id=90000002, name="Elara Voss", character_id=90000002
    )
    now = timezone.now()
    wallet.last_scan_at = now
    wallet.last_scan_ok = True
    wallet.save()
    records = [
        (
            "J-A5QD IV - 12",
            "J-A5QD",
            "Vale of the Silent",
            64,
            [("Loparite", 35), ("Monazite", 25), ("Bitumens", 40)],
            3200000000,
        ),
        (
            "P3EN-E VII - 4",
            "P3EN-E",
            "Vale of the Silent",
            32,
            [("Carnotite", 42), ("Cinnabar", 28), ("Coesite", 30)],
            1850000000,
        ),
        (
            "H-W9TY VI - 8",
            "H-W9TY",
            "Tribute",
            64,
            [("Xenotime", 38), ("Ytterbite", 22), ("Sylvite", 40)],
            2900000000,
        ),
        (
            "Q-CAB2 II - 6",
            "Q-CAB2",
            "Vale of the Silent",
            32,
            [("Pollucite", 45), ("Zircon", 30), ("Bitumens", 25)],
            1650000000,
        ),
        (
            "M-OEE8 V - 9",
            "M-OEE8",
            "Tribute",
            16,
            [("Chromite", 50), ("Sperrylite", 20), ("Coesite", 30)],
            980000000,
        ),
        (
            "D7-ZAC III - 2",
            "D7-ZAC",
            "Tribute",
            16,
            [("Otavite", 40), ("Vanadinite", 35), ("Sylvite", 25)],
            920000000,
        ),
        (
            "E-OGL4 VIII - 1",
            "E-OGL4",
            "Vale of the Silent",
            8,
            [("Cobaltite", 60), ("Euxenite", 20), ("Bitumens", 20)],
            620000000,
        ),
    ]
    moons = []
    for index, (name, system, region, rarity, products, value) in enumerate(records):
        moon, _ = Moon.objects.update_or_create(
            pk=40000100 + index,
            defaults={
                "name": name,
                "solar_system": system,
                "region": region,
                "rarity": rarity,
                "composition": [
                    {"type_id": 45500 + n, "name": nme, "percent": pct}
                    for n, (nme, pct) in enumerate(products)
                ],
                "estimated_value": value,
                "synced_at": now,
            },
        )
        moons.append(moon)
    for index, price in [(1, "450000000"), (2, "750000000")]:
        rental = assign_moon(
            actor=admin,
            moon_id=moons[index].pk,
            renter=pilot,
            price=price,
            duration_months=6,
            at=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        )
        issue_invoices(rental.pk, at=now)
    # One paid invoice for another renter, providing both invoice status views.
    rental = assign_moon(
        actor=admin,
        moon_id=moons[5].pk,
        renter=users["Orion Drake"],
        price="1200000000",
        duration_months=3,
        billing_mode="once",
    )
    invoice = rental.invoices.get()
    record_payment(
        wallet.pk,
        {
            "id": 12345,
            "amount": "1200000000",
            "ref_type": "player_donation",
            "second_party_id": wallet.eve_id,
            "reason": invoice.code,
            "date": timezone.now().isoformat(),
        },
    )
    auction = open_auction(
        actor=admin,
        title="Winter expansion · Premium moons",
        moon_ids=[moons[i].pk for i in [0, 3, 4]],
        ends_at=now + timedelta(days=4, hours=2),
        starts_on=(now + timedelta(days=6)).date(),
        duration_months=3,
        minimum_bid="100000000",
    )
    for lot in auction.lots.select_related("moon"):
        initial = 850000000 if lot.moon.rarity == 64 else 350000000
        place_bid(actor=pilot, lot_id=lot.pk, amount=str(initial))
        place_bid(actor=users["Orion Drake"], lot_id=lot.pk, amount=str(initial + 50000000))
    print("Seeded fictional demo users, surveys, auctions, leases and invoices.")


if __name__ == "__main__":
    seed()
