from datetime import date, datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from moon_rental.conf import retention_months
from moon_rental.models import Invoice, Rental
from moon_rental.services.billing import issue_invoices
from moon_rental.services.maintenance import purge_history, run_lifecycle
from moon_rental.services.rentals import assign_moon, end_rental

pytestmark = pytest.mark.django_db


def date_at(month, day=1, year=2026):
    return datetime(year, month, day, 12, tzinfo=tz.utc)


def test_monthly_anniversary_with_catchup(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=date_at(1, 15),
    )
    assert rental.invoices.get().period == date(2026, 1, 15)
    assert issue_invoices(rental.pk, at=date_at(2)) == 0
    assert issue_invoices(rental.pk, at=date_at(2, 14)) == 0
    assert issue_invoices(rental.pk, at=date_at(2, 15)) == 1
    assert issue_invoices(rental.pk, at=date_at(2, 15)) == 0
    assert issue_invoices(rental.pk, at=date_at(4, 15)) == 1
    assert list(rental.invoices.order_by("period").values_list("period", flat=True)) == [
        date(2026, 1, 15),
        date(2026, 2, 15),
        date(2026, 3, 15),
    ]
    assert issue_invoices(rental.pk, at=date_at(5)) == 0


@pytest.mark.parametrize(
    "periods,ends_on",
    [
        ([date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)], date(2026, 4, 30)),
        ([date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31)], date(2028, 4, 30)),
        ([date(2026, 1, 30), date(2026, 2, 28), date(2026, 3, 30)], date(2026, 4, 30)),
        ([date(2026, 1, 29), date(2026, 2, 28), date(2026, 3, 29)], date(2026, 4, 29)),
        ([date(2028, 2, 29), date(2028, 3, 29), date(2028, 4, 29)], date(2028, 5, 29)),
        ([date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)], date(2027, 1, 31)),
        ([date(2026, 4, 30), date(2026, 5, 30), date(2026, 6, 30)], date(2026, 7, 30)),
        ([date(2026, 1, 31)], date(2026, 2, 28)),
    ],
)
def test_month_end_anniversaries_do_not_drift(manager, pilot, wallet, moon, periods, ends_on):
    start = datetime.combine(periods[0], datetime.min.time(), tzinfo=tz.utc)
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="123.45",
        duration_months=len(periods),
        at=start,
    )
    assert rental.invoices.get().period == periods[0]
    for period in periods[1:]:
        due = datetime.combine(period, datetime.min.time(), tzinfo=tz.utc)
        assert issue_invoices(rental.pk, at=due - timedelta(seconds=1)) == 0
        assert issue_invoices(rental.pk, at=due) == 1
        assert issue_invoices(rental.pk, at=due) == 0
    assert rental.ends_on == ends_on
    end = datetime.combine(ends_on, datetime.min.time(), tzinfo=tz.utc)
    run_lifecycle(at=end)
    assert list(rental.invoices.order_by("period").values_list("period", flat=True)) == periods
    assert all(invoice.amount == Decimal("123.45") for invoice in rental.invoices.all())
    rental.refresh_from_db()
    moon.refresh_from_db()
    assert rental.billed_through == periods[-1]
    assert moon.current_rental_id is None


def test_assignment_uses_utc_start_and_anniversary(manager, pilot, wallet, moon):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=datetime(2026, 2, 1, 0, 30, tzinfo=tz(timedelta(hours=2))),
    )
    assert rental.starts_on == date(2026, 1, 31)
    assert rental.invoices.get().period == date(2026, 1, 31)
    assert (
        issue_invoices(rental.pk, at=datetime(2026, 2, 28, 0, 30, tzinfo=tz(timedelta(hours=2))))
        == 0
    )
    assert issue_invoices(rental.pk, at=date_at(2, 28)) == 1


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


@pytest.mark.parametrize("end_day,months", [(14, [1, 2]), (15, [1, 2, 3])])
def test_early_end_stops_future_billing(manager, admin_user, pilot, wallet, moon, end_day, months):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=12,
        at=date_at(1, 15),
    )
    end_rental(actor=admin_user, rental_id=rental.pk, reason="Ended early", at=date_at(3, end_day))
    assert issue_invoices(rental.pk, at=date_at(8)) == 0
    assert (
        list(rental.invoices.order_by("period").values_list("period__month", flat=True)) == months
    )


@pytest.mark.parametrize("start_day", [1, 31])
def test_retention_preserves_unpaid_and_active(manager, pilot, wallet, moon, start_day):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        at=date_at(1, start_day, year=2024),
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


@pytest.mark.parametrize("retain_invoice", [True, False])
def test_existing_billed_months_are_preserved(manager, pilot, wallet, moon, retain_invoice):
    # Reproduce the previous rule without changing issued invoice snapshots on upgrade.
    with patch("moon_rental.services.billing.issue_invoices", return_value=0):
        rental = assign_moon(
            actor=manager,
            moon_id=moon.pk,
            renter=pilot,
            price="100",
            duration_months=4,
            at=date_at(1, 31),
        )
    invoice = Invoice.objects.create(
        rental=rental,
        period=date(2026, 2, 1),
        amount="90",
        recipient=wallet,
        recipient_name=wallet.name,
    )
    Rental.objects.filter(pk=rental.pk).update(billed_through=invoice.period)
    if not retain_invoice:
        invoice.delete()  # Paid history may already have been purged.
    assert issue_invoices(rental.pk, at=date_at(2, 28)) == 0
    assert issue_invoices(rental.pk, at=date_at(3, 30)) == 0
    assert issue_invoices(rental.pk, at=date_at(3, 31)) == 1
    assert issue_invoices(rental.pk, at=date_at(3, 31)) == 0
    assert rental.invoices.filter(period=date(2026, 3, 31), amount="100").exists()
    assert rental.invoices.count() == 1 + retain_invoice
    if retain_invoice:
        invoice.refresh_from_db()
        assert invoice.period == date(2026, 2, 1) and invoice.amount == Decimal("90")


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
