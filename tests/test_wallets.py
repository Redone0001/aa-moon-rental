from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from moon_rental.models import PaymentReceipt
from moon_rental.services.rentals import assign_moon
from moon_rental.services.wallets import (
    default_wallet,
    esi_get,
    journal_path,
    record_payment,
    register_wallet,
    scan_wallet,
    set_default_wallet,
    toggle_wallet,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def invoice(manager, moon, pilot, wallet):
    rental = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=3,
        billing_mode="once",
    )
    return rental.invoices.get()


def entry(invoice, wallet, **changes):
    return {
        "id": 123,
        "amount": 100,
        "ref_type": "player_donation",
        "reason": invoice.code,
        "second_party_id": wallet.eve_id,
        "date": timezone.now().isoformat(),
        **changes,
    }


def test_partial_and_duplicate_payments(invoice, wallet):
    first = entry(invoice, wallet, amount="40.00")
    assert record_payment(wallet.pk, first)
    assert not record_payment(wallet.pk, first)
    invoice.refresh_from_db()
    assert invoice.status == "open" and invoice.outstanding == Decimal("60")
    assert record_payment(wallet.pk, entry(invoice, wallet, id=124, amount="60"))
    invoice.refresh_from_db()
    assert invoice.status == "paid" and invoice.paid_at
    assert PaymentReceipt.objects.count() == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": -100},
        {"amount": "NaN"},
        {"amount": "1.001"},
        {"ref_type": "market_transaction"},
        {"second_party_id": 7},
        {"reason": "unrelated"},
        {"date": "bad"},
        {"date": "2026-01-01T00:00:00"},
        {"date": "2000-01-01T00:00:00Z"},
        {"date": "2099-01-01T00:00:00Z"},
        {"id": -1},
    ],
)
def test_unrelated_or_invalid_entries_discarded(invoice, wallet, changes):
    assert not record_payment(wallet.pk, entry(invoice, wallet, **changes))
    assert PaymentReceipt.objects.count() == 0


def test_reference_boundaries(invoice, wallet):
    for reason in [
        invoice.code + "X",
        "X" + invoice.code,
        invoice.code + " " + invoice.code,
        "prefix-" + invoice.code,
    ]:
        assert not record_payment(wallet.pk, entry(invoice, wallet, reason=reason))
    assert record_payment(wallet.pk, entry(invoice, wallet, reason="Rent: " + invoice.code.lower()))


def test_default_switch_preserves_invoice(invoice, wallet, admin_user):
    other = register_wallet(
        actor=admin_user, kind="character", eve_id=90000003, name="Treasurer", character_id=90000003
    )
    set_default_wallet(actor=admin_user, account_id=other.pk)
    invoice.refresh_from_db()
    assert default_wallet().pk == other.pk
    assert invoice.recipient_id == wallet.pk and invoice.recipient_name == wallet.name
    with pytest.raises(ValidationError):
        toggle_wallet(actor=admin_user, account_id=wallet.pk)
    # Payments sent to another configured wallet are accepted by exact invoice code.
    assert record_payment(other.pk, entry(invoice, other))


def test_manager_cannot_configure_wallet(manager, wallet):
    with pytest.raises(PermissionDenied):
        set_default_wallet(actor=manager, account_id=wallet.pk)


def test_master_wallet_only():
    assert journal_path("corporation", 123) == "corporations/123/wallets/1/journal/"
    assert journal_path("character", 123) == "characters/123/wallet/journal/"


def test_esi_timeout_and_auth_headers():
    token = Mock()
    token.valid_access_token.return_value = "test-only"
    with patch("moon_rental.services.wallets.requests.get") as get:
        esi_get("corporations/123/wallets/1/journal/", token=token, page=2)
        assert get.call_args.kwargs["timeout"] == (10, 30)
        assert get.call_args.kwargs["params"] == {"page": 2}
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-only"


def test_scan_failure_after_receipt_is_replay_safe(invoice, wallet):
    payment = entry(invoice, wallet, amount=40)

    def broken(_):
        yield payment
        raise RuntimeError("ESI unavailable")

    with patch("moon_rental.services.wallets.wallet_entries", broken), pytest.raises(RuntimeError):
        scan_wallet(wallet.pk)
    wallet.refresh_from_db()
    assert wallet.last_scan_ok is False and wallet.last_scan_at is None
    with patch("moon_rental.services.wallets.wallet_entries", return_value=iter([payment])):
        assert scan_wallet(wallet.pk) == 0
    wallet.refresh_from_db()
    assert wallet.last_scan_ok is True and wallet.last_scan_at


def test_same_journal_cannot_pay_different_invoice(
    invoice, wallet, manager, moon, pilot, admin_user
):
    assert record_payment(wallet.pk, entry(invoice, wallet, amount=40))
    from moon_rental.services.rentals import end_rental

    end_rental(actor=admin_user, rental_id=invoice.rental_id, reason="new term")
    new = assign_moon(
        actor=manager,
        moon_id=moon.pk,
        renter=pilot,
        price="100",
        duration_months=1,
        billing_mode="once",
    ).invoices.get()
    assert not record_payment(wallet.pk, entry(new, wallet))
    assert new.paid_amount == 0
