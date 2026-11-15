from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError

from moon_rental.services.wallets import SCOPES, connect_wallet, wallet_entries

pytestmark = pytest.mark.django_db


@pytest.fixture
def ownership():
    with patch("allianceauth.authentication.models.CharacterOwnership.objects.filter") as query:
        query.return_value.exists.return_value = True
        yield query


def token_for(admin_user, kind="corporation"):
    token = Mock(user_id=admin_user.pk, character_id=90000001)
    token.scopes.values_list.return_value = SCOPES[kind]
    return token


def test_connect_checks_identity_and_proves_corporate_access(admin_user, ownership):
    token = token_for(admin_user)
    with patch("moon_rental.services.wallets.esi_get") as get:
        get.side_effect = [
            Mock(json=lambda: {"corporation_id": 98000001}),
            Mock(json=lambda: {"name": "Orbital Industries"}),
            Mock(),
        ]
        account = connect_wallet(actor=admin_user, token=token, kind="corporation")
        assert account.eve_id == 98000001 and account.authorized_by_id == admin_user.pk
        assert get.call_args.args[0] == "corporations/98000001/wallets/1/journal/"
        assert get.call_args.kwargs == {"token": token, "page": 1}


def test_connect_character_uses_resolved_name(admin_user, ownership):
    token = token_for(admin_user, "character")
    with patch("moon_rental.services.wallets.esi_get") as get:
        get.side_effect = [Mock(json=lambda: {"name": "Verified Pilot"}), Mock()]
        account = connect_wallet(actor=admin_user, token=token, kind="character")
        assert account.eve_id == token.character_id and account.name == "Verified Pilot"


def test_connect_rejects_foreign_character_and_missing_scope(admin_user, ownership):
    token = token_for(admin_user)
    token.user_id = 9999
    with pytest.raises(ValidationError):
        connect_wallet(actor=admin_user, token=token, kind="corporation")
    token.user_id = admin_user.pk
    ownership.return_value.exists.return_value = False
    with pytest.raises(ValidationError):
        connect_wallet(actor=admin_user, token=token, kind="corporation")
    ownership.return_value.exists.return_value = True
    token.scopes.values_list.return_value = []
    with pytest.raises(ValidationError):
        connect_wallet(actor=admin_user, token=token, kind="corporation")


def test_all_master_wallet_pages_fetched(wallet, ownership):
    token = Mock()
    with (
        patch("esi.models.Token.objects.filter") as tokens,
        patch("moon_rental.services.wallets.esi_get") as get,
    ):
        tokens.return_value.require_scopes.return_value.require_valid.return_value.first.return_value = token
        get.side_effect = [
            Mock(json=lambda: {"corporation_id": wallet.eve_id}),
            Mock(headers={"X-Pages": "2"}, json=lambda: [{"id": 1}]),
            Mock(json=lambda: [{"id": 2}]),
        ]
        assert list(wallet_entries(wallet)) == [{"id": 1}, {"id": 2}]
        assert [call.args[0] for call in get.call_args_list[1:]] == [
            "corporations/98000001/wallets/1/journal/",
            "corporations/98000001/wallets/1/journal/",
        ]
        assert get.call_args.kwargs["page"] == 2
        tokens.assert_called_once_with(
            user_id=wallet.authorized_by_id, character_id=wallet.character_id, user__is_active=True
        )


def test_scan_rejects_lost_ownership_and_expired_token(wallet, ownership):
    ownership.return_value.exists.return_value = False
    with pytest.raises(ValidationError, match="no longer owned"):
        list(wallet_entries(wallet))
    ownership.return_value.exists.return_value = True
    with patch("esi.models.Token.objects.filter") as tokens:
        tokens.return_value.require_scopes.return_value.require_valid.return_value.first.return_value = None
        with pytest.raises(ValidationError, match="expired"):
            list(wallet_entries(wallet))


def test_scan_rejects_changed_corporation(wallet, ownership):
    with (
        patch("esi.models.Token.objects.filter"),
        patch("moon_rental.services.wallets.esi_get") as get,
    ):
        get.return_value.json.return_value = {"corporation_id": 1}
        with pytest.raises(ValidationError, match="left"):
            list(wallet_entries(wallet))
