from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.utils import timezone

from moon_rental.models import BillingMode, Configuration, Invoice, Rental
from moon_rental.services.rentals import require_default_wallet, utc_day


def monthly_invoice_period(starts_on, month):
    """Keep the original start day, clamping only for shorter months."""
    return month.replace(day=1) + relativedelta(day=starts_on.day)


@transaction.atomic
def issue_invoices(rental_id, *, at=None):
    at = at or timezone.now()
    rental = Rental.objects.select_for_update().get(pk=rental_id)
    today = utc_day(at)
    last_day = min(today, rental.ends_on - relativedelta(days=1))
    if rental.ended_at:
        last_day = min(last_day, utc_day(rental.ended_at))
    if rental.starts_on > last_day:
        return 0
    if rental.billing_mode == BillingMode.ONCE:
        periods = [] if rental.billed_through else [rental.starts_on]
    else:
        month = (rental.billed_through or rental.starts_on).replace(day=1)
        if rental.billed_through:
            # Existing first-of-month invoices already cover their billing month.
            month += relativedelta(months=1)
        periods = []
        while (period := monthly_invoice_period(rental.starts_on, month)) <= last_day:
            periods.append(period)
            month += relativedelta(months=1)
    missing = set(periods) - set(rental.invoices.values_list("period", flat=True))
    if not missing:
        return 0
    # Serialize recipient selection with default changes and account disabling.
    Configuration.objects.select_for_update().get(pk=1)
    wallet = require_default_wallet()
    for period in sorted(missing):
        invoice = Invoice.objects.create(
            rental=rental,
            period=period,
            amount=rental.price,
            recipient=wallet,
            recipient_name=wallet.name,
        )
        from moon_rental.services.discord_messages import invoice_embed
        from moon_rental.services.notifications import queue_dm

        queue_dm(
            key=f"invoice:{invoice.pk}",
            user_id=rental.renter_id,
            message=f"Invoice received for {rental.moon.name}: {invoice.amount:,.2f} ISK "
            f"({period:%B %Y}). Send to {wallet.name} with payment reason {invoice.code}.",
            embed=invoice_embed(invoice),
        )
    rental.billed_through = max(missing)
    rental.save(update_fields=["billed_through"])
    return len(missing)
