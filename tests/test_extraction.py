from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ObjectDoesNotExist

from moon_rental.services.extraction import estimate_pull, ore_copy_text


def product(name, share, volume, price):
    ore = SimpleNamespace(name=name, volume=volume, extras=SimpleNamespace(current_price=price))
    return SimpleNamespace(amount=share, ore_type=ore)


def test_30_days_uses_configured_volume_whole_units_and_partial_survey(monkeypatch):
    monkeypatch.setattr("moonmining.app_settings.MOONMINING_VOLUME_PER_DAY", 720000)
    estimate = estimate_pull(
        [product("Loparite", 0.35, 10, 123.45), product("Bitumens", 0.1000001, 20, 10)]
    )
    assert estimate["ores"] == [
        {"name": "Bitumens", "units": 108000},
        {"name": "Loparite", "units": 756000},
    ]
    assert Decimal(estimate["value"]) == Decimal("94408200.00")
    assert ore_copy_text(estimate) == "Bitumens\t108000\nLoparite\t756000"


@pytest.mark.parametrize("price", [None, 0])
def test_missing_price_keeps_quantities_and_does_not_report_partial_worth(price):
    estimate = estimate_pull([product("A", 0.5, 10, 10), product("B", 0.5, 10, price)])
    assert len(estimate["ores"]) == 2
    assert estimate["value"] is None


def test_absent_price_record_keeps_ore_export():
    class Ore:
        name = "Loparite"
        volume = 10

        @property
        def extras(self):
            raise ObjectDoesNotExist

    estimate = estimate_pull([SimpleNamespace(amount=0.35, ore_type=Ore())])
    assert estimate["ores"] and estimate["value"] is None


@pytest.mark.parametrize("volume", [None, 0, -10])
def test_missing_volume_does_not_export_a_partial_pull(volume):
    assert estimate_pull([product("A", 0.5, 10, 10), product("B", 0.5, volume, 20)]) == {}


def test_empty_survey_and_zero_shares():
    assert estimate_pull([]) == {}
    assert estimate_pull([product("A", 0, None, None)]) == {}


@pytest.mark.parametrize("share", ["NaN", "Infinity", "1.1"])
def test_invalid_survey_cannot_generate_misleading_quantities(share):
    assert estimate_pull([product("A", share, 10, 10)]) == {}


def test_reprocessed_pricing_basis_is_snapshotted(monkeypatch):
    monkeypatch.setattr("moonmining.app_settings.MOONMINING_USE_REPROCESS_PRICING", True)
    monkeypatch.setattr("moonmining.app_settings.MOONMINING_REPROCESSING_YIELD", 0.8)
    estimate = estimate_pull([product("A", 1, 10, 10)])
    assert estimate["pricing"] == "reprocessed"
    assert estimate["reprocessing_yield"] == "0.8"
