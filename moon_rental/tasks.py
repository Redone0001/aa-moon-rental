import logging

from celery import shared_task

from moon_rental.models import WalletAccount
from moon_rental.services.maintenance import purge_history, run_lifecycle
from moon_rental.services.wallets import scan_wallet

logger = logging.getLogger(__name__)


@shared_task
def run_regular_updates():
    run_lifecycle()


@shared_task
def scan_wallets():
    failed = []
    for pk in WalletAccount.objects.filter(enabled=True).values_list("pk", flat=True):
        try:
            scan_wallet(pk)
        except Exception:
            # Avoid logging wallet contents, bearer tokens or ESI response bodies.
            logger.warning(
                "Moon Rental wallet scan failed for account %s; reconnect or check ESI.", pk
            )
            failed.append(pk)
    if failed:
        raise RuntimeError(f"Wallet scanning failed for account IDs {failed}.")


@shared_task
def cleanup_history():
    return purge_history()
