import re
from decimal import Decimal, InvalidOperation

import requests
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from moon_rental.models import Configuration, Invoice, PaymentReceipt, WalletAccount
from moon_rental.permissions import require_admin
from moon_rental.services.rentals import audit

ESI_BASE = "https://esi.evetech.net/latest"
SCOPES = {
    WalletAccount.Kind.CHARACTER: ["esi-wallet.read_character_wallet.v1"],
    WalletAccount.Kind.CORPORATION: ["esi-wallet.read_corporation_wallets.v1"],
}
REFERENCE = re.compile(r"(?<![\w.-])moon-rental-[A-Z0-9]{4}\.[A-Z0-9]{4}(?![\w.-])", re.I)


def default_wallet():
    config = Configuration.objects.select_related("default_wallet").filter(pk=1).first()
    return config.default_wallet if config else None


def esi_get(path, *, token=None, page=None):
    headers = {"User-Agent": "aa-moon-rental/0.1.0 (Alliance Auth community app)"}
    if token:
        headers["Authorization"] = f"Bearer {token.valid_access_token()}"
    response = requests.get(
        f"{ESI_BASE}/{path}",
        headers=headers,
        params={"page": page} if page else {},
        timeout=(10, 30),
    )
    response.raise_for_status()
    return response


def journal_path(kind, eve_id):
    if kind == WalletAccount.Kind.CORPORATION:
        # Division 1 is always the master wallet. Never iterate the other divisions.
        return f"corporations/{eve_id}/wallets/1/journal/"
    return f"characters/{eve_id}/wallet/journal/"


def connect_wallet(*, actor, token, kind):
    """Resolve identity from ESI and prove access before saving any account."""
    from allianceauth.authentication.models import CharacterOwnership

    require_admin(actor)
    if kind not in SCOPES or token.user_id != actor.pk:
        raise ValidationError("Choose a wallet authorized by your own EVE character.")
    if not CharacterOwnership.objects.filter(
        user=actor, character__character_id=token.character_id
    ).exists():
        raise ValidationError("Add this character to your Alliance Auth account first.")
    if not set(SCOPES[kind]).issubset(set(token.scopes.values_list("name", flat=True))):
        raise ValidationError("The token is missing the wallet scope.")
    character = esi_get(f"characters/{token.character_id}/").json()
    if kind == WalletAccount.Kind.CORPORATION:
        eve_id = int(character["corporation_id"])
        name = esi_get(f"corporations/{eve_id}/").json()["name"]
    else:
        eve_id, name = token.character_id, character["name"]
    # ESI also verifies the character's current corporation wallet role.
    esi_get(journal_path(kind, eve_id), token=token, page=1)
    return register_wallet(
        actor=actor, kind=kind, eve_id=eve_id, name=name, character_id=token.character_id
    )


@transaction.atomic
def register_wallet(*, actor, kind, eve_id, name, character_id):
    """Internal persistence after successful SSO/ESI verification."""
    require_admin(actor)
    config, _ = Configuration.objects.get_or_create(pk=1)
    config = Configuration.objects.select_for_update().get(pk=config.pk)
    account, _ = WalletAccount.objects.update_or_create(
        kind=kind,
        eve_id=eve_id,
        defaults={
            "name": name,
            "character_id": character_id,
            "authorized_by": actor,
            "enabled": True,
        },
    )
    if not config.default_wallet_id:
        config.default_wallet = account
        config.save(update_fields=["default_wallet"])
    audit(actor, "wallet.connect", account)
    return account


@transaction.atomic
def set_default_wallet(*, actor, account_id):
    require_admin(actor)
    config = Configuration.objects.select_for_update().get(pk=1)
    account = WalletAccount.objects.get(pk=account_id, enabled=True)
    config.default_wallet = account
    config.save(update_fields=["default_wallet"])
    audit(actor, "wallet.default", account)


@transaction.atomic
def toggle_wallet(*, actor, account_id):
    require_admin(actor)
    config = Configuration.objects.select_for_update().get(pk=1)
    account = WalletAccount.objects.select_for_update().get(pk=account_id)
    if account.enabled and (
        config.default_wallet_id == account.pk
        or account.invoice_set.filter(status=Invoice.Status.OPEN).exists()
    ):
        raise ValidationError(
            "Select another default and resolve this wallet's open invoices before disabling it."
        )
    account.enabled = not account.enabled
    account.save(update_fields=["enabled"])
    audit(actor, "wallet.toggle", account)


def wallet_entries(account):
    from allianceauth.authentication.models import CharacterOwnership
    from esi.models import Token

    if (
        not account.authorized_by_id
        or not CharacterOwnership.objects.filter(
            user_id=account.authorized_by_id, character__character_id=account.character_id
        ).exists()
    ):
        raise ValidationError("The wallet character is no longer owned by the authorizing user.")
    token = (
        Token.objects.filter(
            user_id=account.authorized_by_id,
            character_id=account.character_id,
            user__is_active=True,
        )
        .require_scopes(SCOPES[account.kind])
        .require_valid()
        .first()
    )
    if not token:
        raise ValidationError("Wallet authorization expired; an admin must reconnect it.")
    if account.kind == WalletAccount.Kind.CORPORATION:
        character = esi_get(f"characters/{account.character_id}/").json()
        if int(character["corporation_id"]) != account.eve_id:
            raise ValidationError("The scanning character has left the configured corporation.")
    first = esi_get(journal_path(account.kind, account.eve_id), token=token, page=1)
    pages = int(first.headers.get("X-Pages", "1"))
    if not 1 <= pages <= 10000:
        raise ValidationError("ESI returned an invalid journal page count.")
    yield from first.json()
    for page in range(2, pages + 1):
        yield from esi_get(
            journal_path(account.kind, account.eve_id), token=token, page=page
        ).json()


@transaction.atomic
def record_payment(account_id, entry):
    """A reference matches one invoice; positive incoming donations may pay it in parts."""
    account = WalletAccount.objects.select_for_update().get(pk=account_id)
    if not account.enabled or entry.get("ref_type") != "player_donation":
        return False
    try:
        amount = Decimal(str(entry["amount"]))
        journal_id = int(entry["id"])
        recipient = int(entry["second_party_id"])
        received = parse_datetime(entry["date"])
        if (
            not amount.is_finite()
            or amount <= 0
            or amount >= Decimal("1e22")
            or amount != amount.quantize(Decimal("0.01"))
            or journal_id <= 0
            or recipient != account.eve_id
            or not received
            or timezone.is_naive(received)
            or received > timezone.now()
        ):
            return False
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return False
    codes = REFERENCE.findall(str(entry.get("reason", "")))
    if len(codes) != 1:
        return False
    invoice = (
        Invoice.objects.select_for_update()
        .filter(code__iexact=codes[0], status=Invoice.Status.OPEN)
        .first()
    )
    if not invoice or received < invoice.created_at:
        return False
    receipt, created = PaymentReceipt.objects.get_or_create(
        account=account,
        journal_id=journal_id,
        defaults={"invoice": invoice, "amount": amount, "received_at": received},
    )
    if created and invoice.paid_amount >= invoice.amount:
        invoice.status = Invoice.Status.PAID
        invoice.paid_at = received
        invoice.closed_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at", "closed_at"])
    if created:
        from moon_rental.services.discord_messages import invoice_embed
        from moon_rental.services.notifications import queue_dm

        state = "fully paid" if invoice.status == Invoice.Status.PAID else "partially paid"
        queue_dm(
            key=f"payment:{receipt.pk}",
            user_id=invoice.rental.renter_id,
            message=f"Invoice {invoice.code} for {invoice.rental.moon.name} is {state}. "
            f"Received {amount:,.2f} ISK; total paid {invoice.paid_amount:,.2f} ISK; "
            f"remaining {invoice.outstanding:,.2f} ISK.",
            embed=invoice_embed(invoice, receipt=receipt),
        )
    return created


def scan_wallet(account_id):
    """Keep only matching receipts. Do not persist raw journal entries or errors."""
    account = WalletAccount.objects.get(pk=account_id)
    if not account.enabled:
        return 0
    count = 0
    try:
        for entry in wallet_entries(account):
            count += bool(record_payment(account.pk, entry))
    except Exception:
        WalletAccount.objects.filter(pk=account_id).update(last_scan_ok=False)
        raise
    WalletAccount.objects.filter(pk=account_id).update(
        last_scan_ok=True, last_scan_at=timezone.now()
    )
    return count
