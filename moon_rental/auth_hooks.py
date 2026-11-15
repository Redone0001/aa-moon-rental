from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from moon_rental import urls
from moon_rental.permissions import is_manager


class MoonRentalMenu(MenuItemHook):
    def __init__(self):
        super().__init__(
            "Moon Rental", "fas fa-moon fa-fw", "moon_rental:index", navactive=["moon_rental:"]
        )

    def render(self, request):
        if request.user.has_perm("moon_rental.basic_access") or is_manager(request.user):
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def menu():
    return MoonRentalMenu()


@hooks.register("url_hook")
def url():
    return UrlHook(urls, "moon_rental", r"^moon-rental/")
