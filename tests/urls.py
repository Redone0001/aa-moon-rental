from copy import deepcopy

from django.urls import URLResolver, include, path

from moon_rental import urls

test_patterns = deepcopy(urls.urlpatterns)


def contains_rental(pattern):
    if not isinstance(pattern, URLResolver):
        return False
    return pattern.app_name == "moon_rental" or any(
        contains_rental(p) for p in pattern.url_patterns
    )


def core_patterns():
    import allianceauth.urls

    return [p for p in allianceauth.urls.urlpatterns if not contains_rental(p)]


# Core mutates URL callbacks in-place to add its main-character gate. Copy this
# app's patterns before loading it, while keeping all real AA navigation URLs.
urlpatterns = [path("moon-rental/", include((test_patterns, "moon_rental"))), *core_patterns()]
