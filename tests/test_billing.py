from datetime import datetime
from datetime import timezone as tz
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from moon_rental.conf import retention_months
from moon_rental.models import Invoice
from moon_rental.services.billing import issue_invoices
from moon_rental.services.maintenance import purge_history, run_lifecycle
from moon_rental.services.rentals import assign_moon, end_rental

pytestmark = pytest.mark.django_db


def date_at(month, day=1, year=2026):
    return datetime(year, month, day, 12, tzinfo=tz.utc)


def test_monthly_first_with_catchup(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=date_at(1, 15),
    )
    assert rental.invoices.count() == 0
    assert issue_invoices(rental.pk, at=date_at(2)) == 1
    assert issue_invoices(rental.pk, at=date_at(2)) == 0
    assert issue_invoices(rental.pk, at=date_at(4, 15)) == 2
    assert list(rental.invoices.order_by("period").values_list("period__month", flat=True)) == [
        2,
        3,
        4,
    ]
    assert issue_invoices(rental.pk, at=date_at(5)) == 0


def test_first_day_award_invoiced_now(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager, moon_id=moon.pk, renter=pilot, price="100", duration_months=1, at=date_at(1)
    )
    assert rental.invoices.get().period == date_at(1).date()
    run_lifecycle(at=date_at(2))
    moon.refresh_from_db()
    assert moon.current_rental is None and rental.invoices.count() == 1


def test_once_immediate_and_no_duplicate(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="123.45",
        duration_months=3,
        billing_mode="once",
        at=date_at(1, 15),
    )
    assert rental.invoices.get().amount == Decimal("123.45")
    assert issue_invoices(rental.pk, at=date_at(3)) == 0


def test_early_end_stops_future_billing(manager, admin_user, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=12,
        at=date_at(1, 15),
    )
    end_rental(actor=admin_user, rental_id=rental.pk, reason="Ended early", at=date_at(3, 15))
    assert issue_invoices(rental.pk, at=date_at(8)) == 0
    assert list(rental.invoices.order_by("period").values_list("period__month", flat=True)) == [
        2,
        3,
    ]


def test_retention_preserves_unpaid_and_active(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=date_at(1, year=2024),
    )
    run_lifecycle(at=date_at(5, year=2024))
    paid = rental.invoices.order_by("period").first()
    Invoice.objects.filter(pk=paid.pk).update(status="paid", closed_at=date_at(1, year=2024))
    assert purge_history(at=date_at(6, year=2026)) == 1
    assert rental.invoices.filter(status="open").count() == 2
    run_lifecycle(at=date_at(7, year=2026))
    assert rental.invoices.count() == 2  # Purged paid periods must not be recreated.
    rental.refresh_from_db()
    assert rental.pk


def test_retention_config():
    assert retention_months() == 12
    with override_settings(MOON_RENTAL_RETENTION_MONTHS=6):
        assert retention_months() == 6
    for invalid in [0, -1, "12", True]:
        with (
            override_settings(MOON_RENTAL_RETENTION_MONTHS=invalid),
            pytest.raises(ImproperlyConfigured),
        ):
            retention_months()
