"""Fixed-duration estimates from Moon Mining's local survey and pricing data."""

from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ObjectDoesNotExist

PULL_DAYS = 30


def estimate_pull(products):
    """Snapshot whole uncompressed units and their gross value for a 30-day pull.

    Survey shares are fractions of the configured maximum volume. Never normalize
    incomplete surveys or assume that all ore types occupy the same unit volume.
    Missing volume prevents an export; missing prices leave the value unknown.
    """
    from moonmining import app_settings

    daily_volume = Decimal(str(app_settings.MOONMINING_VOLUME_PER_DAY))
    if not daily_volume.is_finite() or daily_volume <= 0:
        return {}
    ores = []
    value = Decimal(0)
    priced = True
    for product in products:
        share = Decimal(str(product.amount))
        if not share.is_finite():
            return {}
        if share <= 0:
            continue
        volume = Decimal(str(product.ore_type.volume or 0))
        if share > 1 or not volume.is_finite() or volume <= 0:
            return {}
        units = int((daily_volume * PULL_DAYS * share / volume).to_integral_value(ROUND_FLOOR))
        if not units:
            continue
        ores.append({"name": product.ore_type.name, "units": units})
        try:
            price = Decimal(str(product.ore_type.extras.current_price or 0))
        except ObjectDoesNotExist:
            price = Decimal(0)
        if not price.is_finite() or price <= 0:
            priced = False
        else:
            value += units * price
    if not ores:
        return {}
    return {
        "days": PULL_DAYS,
        "volume_per_day": str(daily_volume),
        "ores": sorted(ores, key=lambda ore: ore["name"]),
        "value": str(value.quantize(Decimal("0.01"))) if priced else None,
        "pricing": "reprocessed" if app_settings.MOONMINING_USE_REPROCESS_PRICING else "ore",
        "reprocessing_yield": str(app_settings.MOONMINING_REPROCESSING_YIELD),
    }


def ore_copy_text(estimate):
    """Plain item/quantity rows accepted by appraisal tools and spreadsheets."""
    return "\n".join(f"{ore['name']}\t{ore['units']}" for ore in estimate.get("ores", []))
