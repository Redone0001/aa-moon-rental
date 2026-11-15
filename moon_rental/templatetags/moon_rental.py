from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def isk(value):
    try:
        return f"{Decimal(value):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "—"


@register.filter
def compact_isk(value):
    """Compact display only; keep amounts as Decimal throughout formatting."""
    try:
        amount = Decimal(value)
        if not amount.is_finite():
            return "—"
        for scale, suffix in [(10**12, "t"), (10**9, "b"), (10**6, "m"), (10**3, "k"), (1, "")]:
            if abs(amount) >= scale or scale == 1:
                rounded = (amount / scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                number = format(rounded, "f").rstrip("0").rstrip(".")
                return number.replace(".", ",") + suffix
    except (InvalidOperation, TypeError, ValueError):
        return "—"
