from django.core.exceptions import PermissionDenied


def is_admin(user):
    return user.is_active and user.has_perm("moon_rental.administer_rentals")


def is_manager(user):
    return user.is_active and (is_admin(user) or user.has_perm("moon_rental.manage_rentals"))


def require_access(user):
    if not user.is_active or not (is_manager(user) or user.has_perm("moon_rental.basic_access")):
        raise PermissionDenied


def require_manager(user):
    if not is_manager(user):
        raise PermissionDenied


def require_admin(user):
    if not is_admin(user):
        raise PermissionDenied
