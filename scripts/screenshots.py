"""Capture and exercise the real locally running AA pages using fictional demo users."""

import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from moon_rental.models import Auction, Bid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
BASE = "http://127.0.0.1:8000"


def session(name, theme="darkly"):
    client = Client()
    client.force_login(User.objects.get(username=name))
    saved = client.session
    saved["THEME"] = f"allianceauth.theme.{theme}.auth_hooks.{theme.title()}ThemeHook"
    saved.save()
    return {"name": settings.SESSION_COOKIE_NAME, "value": saved.session_key, "url": BASE}


def capture():
    if settings.SETTINGS_MODULE != "tests.settings":
        raise RuntimeError("Screenshots must use the isolated local test/demo settings.")
    OUT.mkdir(parents=True, exist_ok=True)
    auction = Auction.objects.filter(settled_at=None).first()
    admin_cookie = session("Rental Administrator")
    pilot_cookie = session("Astra Vale")
    flatly_cookie = session("Astra Vale", "flatly")
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 1050},
            device_scale_factor=1,
            ignore_https_errors=True,
            permissions=["clipboard-read", "clipboard-write"],
        )
        context.add_cookies([admin_cookie])
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def snap(route, filename):
            response = page.goto(BASE + route, wait_until="networkidle")
            assert response.status == 200, (route, response.status)
            page.locator(".mr-app").wait_for()
            page.screenshot(path=str(OUT / filename), full_page=True)

        snap("/moon-rental/", "overview-darkly.png")
        snap(f"/moon-rental/auctions/{auction.pk}/", "auction-darkly.png")
        snap("/moon-rental/moons/", "catalogue-darkly.png")
        snap("/moon-rental/wallets/", "wallets-darkly.png")
        snap("/moon-rental/auctions/new/", "new-auction-darkly.png")
        context.add_cookies([pilot_cookie])
        snap("/moon-rental/my-moons/", "my-moons-darkly.png")
        snap("/moon-rental/invoices/", "invoices-darkly.png")
        page.get_by_role("button", name="Copy reference", exact=True).first.click()
        copied = page.evaluate("navigator.clipboard.readText()")
        assert copied.startswith("moon-rental-")
        page.get_by_role("button", name="Copy amount", exact=True).first.click()
        assert Decimal(page.evaluate("navigator.clipboard.readText()")) > 0
        page.get_by_role("button", name="Copy recipient", exact=True).first.click()
        assert page.evaluate("navigator.clipboard.readText()") == "Orbital Industries"

        # Real form POST, CSRF and database update. Restore demo bids afterward.
        page.goto(BASE + f"/moon-rental/auctions/{auction.pk}/", wait_until="networkidle")
        form = page.locator("form").filter(has=page.get_by_role("button", name="Place bid")).first
        form.locator("input[name=amount]").fill("9999999999.99")
        form.get_by_role("button", name="Place bid").click()
        page.wait_for_load_state("networkidle")
        assert "Your bid is now the highest bid." in page.locator("body").inner_text()
        context.add_cookies([flatly_cookie])
        snap("/moon-rental/invoices/", "invoices-flatly.png")
        # AA's navigation detects mobile devices by user agent, not viewport alone.
        mobile = browser.new_context(**p.devices["iPhone 13"], ignore_https_errors=True)
        mobile.add_cookies([pilot_cookie])
        page = mobile.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        snap("/moon-rental/invoices/", "invoices-mobile.png")
        assert page.locator(".mr-app").bounding_box()["width"] > 300
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            "Horizontal page overflow"
        )
        browser.close()
    Bid.objects.filter(lot__auction=auction, amount="9999999999.99").delete()
    assert not errors, errors
    print(
        f"Captured {len(list(OUT.glob('*.png')))} screenshots; bid POST, clipboard and mobile layout checks passed."
    )


if __name__ == "__main__":
    capture()
