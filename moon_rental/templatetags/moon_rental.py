from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def isk(value):
    try:
        return f"{Decimal(value):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "—"
