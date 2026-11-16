"""UTC financial summaries across all rental periods, using invoice snapshots."""

from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone

from moon_rental.models import BillingMode, Invoice, Rental
from moon_rental.permissions import require_admin
from moon_rental.services.billing import monthly_invoice_period
from moon_rental.services.rentals import utc_day

ZERO = Decimal("0")


def monthly_summary(month, *, actor, at=None):
    require_admin(actor)
    at = at or timezone.now()
    end = month + relativedelta(months=1)
    invoices = list(
        Invoice.objects.filter(period__gte=month, period__lt=end).prefetch_related("receipts")
    )
    predicted = sum((i.amount for i in invoices if i.status != Invoice.Status.VOID), ZERO)
    paid = sum(
        (min(i.amount, i.paid_amount) for i in invoices if i.status != Invoice.Status.VOID), ZERO
    )
    issued = {i.rental_id for i in invoices}
    # Forecast only genuinely unissued charges. Voids and retained billing cursors
    # must not resurrect an intentionally closed or purged invoice.
    for rental in Rental.objects.filter(starts_on__lt=end, ends_on__gt=month):
        period = (
            monthly_invoice_period(rental.starts_on, month)
            if rental.billing_mode == BillingMode.MONTHLY
            else rental.starts_on
        )
        if (
            rental.pk not in issued
            and month <= period < end
            and rental.starts_on <= period < rental.ends_on
            and (not rental.ended_at or utc_day(rental.ended_at) >= period)
            and (not rental.billed_through or rental.billed_through < month)
        ):
            predicted += rental.price
    today = utc_day(at)
    active = Rental.objects.filter(starts_on__lte=today, ends_on__gt=today, ended_at=None)
    recurring = (
        active.filter(billing_mode=BillingMode.MONTHLY).aggregate(total=Sum("price"))["total"]
        or ZERO
    )
    return {
        "predicted": predicted,
        "paid": paid,
        "remaining": max(ZERO, predicted - paid),
        "paid_percent": (paid * 100 / predicted).quantize(Decimal("0.1")) if predicted else ZERO,
        "monthly_income": recurring,
        "rented_count": active.count(),
        "open_count": Invoice.objects.filter(status=Invoice.Status.OPEN).count(),
    }
