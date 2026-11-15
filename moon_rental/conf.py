from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def retention_months():
    value = getattr(settings, "MOON_RENTAL_RETENTION_MONTHS", 12)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImproperlyConfigured("MOON_RENTAL_RETENTION_MONTHS must be a positive integer.")
    return value
