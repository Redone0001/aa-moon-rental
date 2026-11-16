from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from moon_rental.models import Moon, SurveyOre
from moon_rental.permissions import require_admin
from moon_rental.services.extraction import estimate_pull
from moon_rental.services.rentals import audit


@transaction.atomic
def sync_moons(*, actor):
    from moonmining.models import Moon as MiningMoon

    require_admin(actor)
    at = timezone.now()
    source = MiningMoon.objects.select_related(
        "eve_moon__eve_planet__eve_solar_system__eve_constellation__eve_region"
    ).prefetch_related("products__ore_type__extras")
    seen = []
    for moon in source:
        snapshot, _ = Moon.objects.update_or_create(
            pk=moon.pk,
            defaults={
                "name": moon.name,
                "solar_system": moon.solar_system().name,
                "region": moon.region().name,
                "constellation": moon.solar_system().eve_constellation.name,
                "rarity": moon.rarity_class,
                "composition": [
                    {
                        "type_id": p.ore_type_id,
                        "name": p.ore_type.name,
                        "percent": round(p.amount * 100, 4),
                    }
                    for p in moon.products.all()
                ],
                "estimated_value": Decimal(str(moon.value)).quantize(Decimal("0.01"))
                if moon.value is not None
                else None,
                "pull_30d": estimate_pull(moon.products.all()),
                "available": True,
                "synced_at": at,
            },
        )
        snapshot.survey_ores.all().delete()
        SurveyOre.objects.bulk_create(
            [
                SurveyOre(moon=snapshot, type_id=p.ore_type_id, name=p.ore_type.name)
                for p in moon.products.all()
                if p.amount > 0
            ]
        )
        seen.append(moon.pk)
    # Removed upstream moons remain visible to existing renters, but cannot be offered again.
    Moon.objects.exclude(pk__in=seen).update(available=False)
    audit(actor, "moons.sync", actor, f"{len(seen)} moons")
    return len(seen)
