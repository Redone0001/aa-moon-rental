from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from moon_rental.conf import retention_months
from moon_rental.models import Auction, AuditEvent, Invoice, Moon, Notification, Rental
from moon_rental.services.billing import issue_invoices
from moon_rental.services.rentals import release_expired, settle_auction


def run_lifecycle(*, at=None):
    at = at or timezone.now()
    for auction_id in Auction.objects.filter(settled_at__isnull=True, ends_at__lte=at).values_list(
        "pk", flat=True
    ):
        settle_auction(auction_id, at=at)
    # Include ended leases so delayed workers can catch up without losing due invoices.
    for rental_id in Rental.objects.values_list("pk", flat=True):
        issue_invoices(rental_id, at=at)
    for moon_id in Moon.objects.filter(current_rental__ends_on__lte=at.date()).values_list(
        "pk", flat=True
    ):
        with transaction.atomic():
            moon = Moon.objects.select_for_update().get(pk=moon_id)
            release_expired(moon, at)


@transaction.atomic
def purge_history(*, at=None):
    """Never erase active leases, pending auctions or unpaid invoice evidence."""
    at = at or timezone.now()
    cutoff = at - relativedelta(months=retention_months())
    invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.PAID, Invoice.Status.VOID], closed_at__lt=cutoff
    )
    count = invoices.count()
    invoices.delete()
    Rental.objects.filter(
        Q(ends_on__lt=cutoff.date()) | Q(ended_at__lt=cutoff),
        active_moon__isnull=True,
        invoices__isnull=True,
    ).delete()
    Auction.objects.filter(settled_at__lt=cutoff, lots__rental__isnull=True).exclude(
        lots__rental__isnull=False
    ).delete()
    AuditEvent.objects.filter(created_at__lt=cutoff).delete()
    Notification.objects.filter(Q(delivered_at__lt=cutoff) | Q(skipped_at__lt=cutoff)).delete()
    return count
