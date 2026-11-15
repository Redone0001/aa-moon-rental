from unittest.mock import patch

import pytest

from moon_rental import tasks

pytestmark = pytest.mark.django_db


def test_lifecycle_and_cleanup_delegate():
    with patch("moon_rental.tasks.run_lifecycle") as run:
        tasks.run_regular_updates()
        run.assert_called_once_with()
    with patch("moon_rental.tasks.purge_history", return_value=2):
        assert tasks.cleanup_history() == 2


def test_wallet_failure_does_not_stop_other_accounts(wallet, admin_user):
    from moon_rental.services.wallets import register_wallet

    other = register_wallet(
        actor=admin_user, kind="character", eve_id=90000003, name="Another", character_id=90000003
    )
    with patch("moon_rental.tasks.scan_wallet", side_effect=[RuntimeError("ESI"), 0]) as scan:
        with pytest.raises(RuntimeError, match="Wallet scanning failed"):
            tasks.scan_wallets()
        assert scan.call_count == 2
        assert sorted(c.args[0] for c in scan.call_args_list) == sorted([wallet.pk, other.pk])
