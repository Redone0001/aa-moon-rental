"""Exercise large moon selections and capture the real AA UI with fictional surveys."""

import json
from datetime import timedelta

from screenshots import BASE, OUT, session  # isort: skip

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from playwright.sync_api import sync_playwright

from moon_rental.models import Auction, Moon, SurveyOre
from moon_rental.services.rentals import open_auction


def capture():
    if (
        settings.SETTINGS_MODULE != "tests.settings"
        or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
    ):
        raise RuntimeError("Use only the disposable local SQLite demo.")
    moons = []
    for i in range(72):
        region = "Vale of the Silent" if i % 2 else "Tribute"
        moons.append(
            Moon(
                id=49990000 + i,
                name=f"{'J-A5QD' if i % 2 else 'M-OEE8'} IV - {i + 1}",
                solar_system="J-A5QD" if i % 2 else "M-OEE8",
                region=region,
                constellation="A3-T8B" if i % 2 else "4-7M1U",
                rarity=64 if i % 3 else 32,
                composition=[
                    {"type_id": 45506, "name": "Loparite", "percent": 35},
                    {"type_id": 45511, "name": "Monazite", "percent": 25},
                    {"type_id": 45492, "name": "Bitumens", "percent": 40},
                ],
                synced_at=timezone.now(),
            )
        )
    Moon.objects.bulk_create(moons)
    SurveyOre.objects.bulk_create(
        [
            SurveyOre(moon=moon, type_id=ore["type_id"], name=ore["name"])
            for moon in moons
            for ore in moon.composition
        ]
    )
    admin = User.objects.get(username="Rental Administrator")
    auction = open_auction(
        actor=admin,
        title="Regional moon programme",
        moon_ids=[moon.pk for moon in moons[:30]],
        ends_at=timezone.now() + timedelta(days=3),
        starts_on=(timezone.now() + timedelta(days=5)).date(),
        duration_months=3,
        minimum_bid="100000000",
    )
    cookies = {theme: session(admin.username, theme) for theme in ("darkly", "flatly")}
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for theme in cookies:
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1050}, ignore_https_errors=True
                )
                context.add_cookies([cookies[theme]])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))

                def navigate(route, page=page):
                    response = page.goto(BASE + route, wait_until="networkidle")
                    assert response.status == 200

                def click(name, page=page):
                    page.get_by_role("button", name=name, exact=True).click()
                    page.wait_for_load_state("networkidle")

                navigate("/moon-rental/auctions/new/")
                page.locator('[name="title"]').fill("Regional moon programme")
                page.locator('[name="starts_on"]').fill("03/01/2030")
                page.locator('[name="ends_at"]').fill("01/01/2030 18:00")
                page.locator('[name="duration_months"]').fill("6")
                page.locator('[name="minimum_bid"]').fill("1200000000.12")
                page.locator('[name="billing_mode"]').select_option("once")
                page.locator('input[type="checkbox"][name="moons"]').first.check()
                assert page.locator("[data-moon-selection-count]").inner_text() == "1"
                click("Next moons")
                assert page.locator("[data-moon-selection-count]").inner_text() == "1"
                page.locator('input[type="checkbox"][name="moons"]').first.check()
                assert page.locator("[data-moon-selection-count]").inner_text() == "2"
                page.locator('[name="filter-region"]').select_option("Vale of the Silent")
                page.locator('[name="filter-constellation"]').select_option("A3-T8B")
                page.locator('[name="filter-rarity"]').select_option("64")
                page.locator('[name="filter-ore"]').select_option("45506")
                click("Apply filters")
                assert page.locator("[data-moon-selection-count]").inner_text() == "2"
                assert page.locator('[name="title"]').input_value() == "Regional moon programme"
                assert page.locator('[name="minimum_bid"]').input_value() == "1200000000.12"
                assert page.locator('[name="billing_mode"]').input_value() == "once"
                assert not page.locator(".invalid-feedback").count()
                page.screenshot(path=str(OUT / f"moon-filters-auction-{theme}.png"), full_page=True)
                click("Select this page")
                assert int(page.locator("[data-moon-selection-count]").inner_text()) > 2
                click("Clear filters")
                assert page.locator('[name="filter-region"]').input_value() == ""

                navigate("/moon-rental/rentals/assign/")
                page.locator('[name="price"]').fill("1200000000.12")
                page.locator('input[type="radio"][name="moon"]').first.check()
                click("Next moons")
                hidden = page.locator('input[type="hidden"][name="moon"]').input_value()
                page.locator('input[type="radio"][name="moon"]').first.check()
                click("Apply filters")
                assert page.locator('input[type="hidden"][name="moon"]').input_value() != hidden
                page.locator('[name="filter-region"]').select_option("Vale of the Silent")
                page.locator('[name="filter-rarity"]').select_option("64")
                click("Apply filters")
                assert page.locator('[name="price"]').input_value() == "1200000000.12"
                page.screenshot(
                    path=str(OUT / f"moon-filters-assignment-{theme}.png"), full_page=True
                )

                navigate(f"/moon-rental/auctions/{auction.pk}/")
                assert page.locator("article").count() == 24
                page.locator('[name="filter-region"]').select_option("Vale of the Silent")
                page.locator('[name="filter-rarity"]').select_option("64")
                click("Apply filters")
                assert page.locator("article").count() == 10
                page.screenshot(path=str(OUT / f"moon-filters-bidding-{theme}.png"), full_page=True)
                context.close()

            mobile = browser.new_context(
                **{**p.devices["iPhone 13"], "device_scale_factor": 1}, ignore_https_errors=True
            )
            mobile.add_cookies([cookies["darkly"]])
            page = mobile.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            for route in ("/moon-rental/auctions/new/", "/moon-rental/rentals/assign/"):
                page.goto(BASE + route, wait_until="networkidle")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(OUT / "moon-filters-mobile.png"), full_page=True)
            browser.close()
    finally:
        Moon.objects.filter(reserved_lot__auction=auction).update(reserved_lot=None)
        Auction.objects.filter(pk=auction.pk).delete()
        Moon.objects.filter(pk__in=[moon.pk for moon in moons]).delete()
    assert not errors, errors
    print(json.dumps({"screenshots": 7, "browser_errors": errors, "workflow": "passed"}))


if __name__ == "__main__":
    capture()
