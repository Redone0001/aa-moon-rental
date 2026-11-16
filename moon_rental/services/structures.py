"""Read current, permitted structure details without copying or changing upstream data."""

from collections import defaultdict

from django.apps import apps


def attach_moon_structures(moons, *, user):
    """Load displayed moons with one structure query and aa-structures visibility rules."""
    moons = list(moons)
    available = apps.is_installed("structures") and user.has_perm("structures.basic_access")
    by_moon = defaultdict(list)
    if available and moons:
        Structure = apps.get_model("structures", "Structure")
        structures = (
            Structure.objects.visible_for_user(user)
            .filter(eve_moon_id__in={moon.pk for moon in moons})
            .order_by("eve_type__name", "name", "pk")
            .values("eve_moon_id", "eve_type__name", "name")
        )
        for structure in structures:
            by_moon[structure["eve_moon_id"]].append(
                {"type_name": structure["eve_type__name"], "name": structure["name"]}
            )
    for moon in moons:
        moon.structure_info_available = available
        moon.visible_structures = by_moon[moon.pk]
