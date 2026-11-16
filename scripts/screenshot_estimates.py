"""Verify clipboard/date UX and capture fictional auction estimates in both AA themes."""

import os
from datetime import timedelta
from types import SimpleNamespace

from screenshots import BASE, OUT, session  # isort: skip

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from moon_rental.models import Auction, Moon
from moon_rental.services.extraction import estimate_pull, ore_copy_text
from moon_rental.services.rentals import open_auction, place_bid


def capture():
    if (
        settings.SETTINGS_MODULE != "tests.settings"
        or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
    ):
        raise RuntimeError("Use only the disposable local SQLite demo.")
    OUT.mkdir(parents=True, exist_ok=True)
    admin = User.objects.get(username="Rental Administrator")
    pilot = User.objects.get(username="Astra Vale")
    products = [
        SimpleNamespace(
            amount=share,
            ore_type=SimpleNamespace(
                name=name, volume=10, extras=SimpleNamespace(current_price=price)
            ),
        )
        for name, share, price in [
            ("Loparite", 0.35, 1800),
            ("Monazite", 0.25, 1600),
            ("Bitumens", 0.4, 100),
        ]
    ]
    moons = []
    auctions = []
    errors = []
    try:
        for index, name in enumerate(["J-A5QD IV - 12", "M-OEE8 V - 9", "Q-CAB2 II - 6"]):
            estimate = estimate_pull(products) if index != 2 else {}
            if index == 1:
                estimate["value"] = None
            moons.append(
                Moon.objects.create(
                    pk=49991000 + index,
                    name=name,
                    solar_system=name.split()[0],
                    region="Vale of the Silent",
                    constellation="A3-T8B",
                    rarity=64,
                    composition=[
                        {"name": p.ore_type.name, "percent": p.amount * 100} for p in products
                    ],
                    pull_30d=estimate,
                    synced_at=timezone.now(),
                )
            )
        auction = open_auction(
            actor=admin,
            title="Winter expansion · Premium moons",
            moon_ids=[m.pk for m in moons[:2]],
            ends_at=timezone.now() + timedelta(days=3),
            starts_on=(timezone.now() + timedelta(days=5)).date(),
            duration_months=3,
            minimum_bid="100000000",
        )
        auctions.append(auction.pk)
        for lot in auction.lots.all():
            place_bid(actor=pilot, lot_id=lot.pk, amount="850000000")
        cookies = {
            (user.username, theme): session(user.username, theme)
            for user in (admin, pilot)
            for theme in ("darkly", "flatly")
        }
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            )
            for theme in ("darkly", "flatly"):
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1800},
                    ignore_https_errors=True,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                context.add_cookies([cookies[pilot.username, theme]])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.goto(
                    f"{BASE}/moon-rental/auctions/{auction.pk}/", wait_until="networkidle"
                )
                assert response.status == 200
                button = page.get_by_role("button", name=f"Copy 30-day ore for {moons[0].name}")
                button.click()
                expect(button).to_have_text("Copied")
                assert page.evaluate("navigator.clipboard.readText()") == ore_copy_text(
                    moons[0].pull_30d
                )
                assert str(moons[0].pull_30d["ores"][0]["units"]) not in page.inner_text("body")
                expect(button).to_have_text("Copy 30-day ore")
                assert "ore prices missing" in page.inner_text("body")
                page.locator(".mr-app").screenshot(path=str(OUT / f"auction-estimates-{theme}.png"))

                # Older/insecure clipboard environments still support a hidden copy selection.
                page.evaluate("""() => {
                    window.originalClipboard = navigator.clipboard;
                    Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
                }""")
                button.click()
                expect(button).to_have_text("Copied")
                page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {
                    value: window.originalClipboard, configurable: true
                })""")
                assert page.evaluate("navigator.clipboard.readText()") == ore_copy_text(
                    moons[0].pull_30d
                )

                context.add_cookies([cookies[admin.username, theme]])
                page.goto(f"{BASE}/moon-rental/auctions/new/", wait_until="networkidle")
                page.locator('[name="title"]').fill("Winter expansion · Setup window")
                page.locator('[name="starts_on"]').fill("03/02/2030")
                page.locator('[name="ends_at"]').fill("01/02/2030 18:30")
                page.locator(f'[name="moons"][value="{moons[2].pk}"]').check()
                page.get_by_role("button", name="Apply filters", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.locator('[name="starts_on"]').input_value() == "03/02/2030"
                assert page.locator('[name="ends_at"]').input_value() == "01/02/2030 18:30"
                terms = page.locator(".card").filter(has=page.locator('[name="starts_on"]'))
                terms.screenshot(path=str(OUT / f"auction-start-{theme}.png"))
                if theme == "flatly":
                    page.get_by_role("button", name="Open bidding", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    assert "/new/" not in page.url
                    auctions.append(int(page.url.rstrip("/").split("/")[-1]))
                    assert "03/02/2030" in page.inner_text("body")
                    expect(
                        page.get_by_role("button", name="Copy 30-day ore", exact=True)
                    ).to_be_disabled()
                context.close()

            mobile = browser.new_context(
                **{**p.devices["iPhone 13"], "device_scale_factor": 1}, ignore_https_errors=True
            )
            mobile.add_cookies([cookies[pilot.username, "darkly"]])
            page = mobile.new_page()
            page.goto(f"{BASE}/moon-rental/auctions/{auction.pk}/", wait_until="networkidle")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            # AA scrolls inside a fixed main panel. Give that panel enough height
            # to capture every card without clipping at the normal viewport edge.
            page.set_viewport_size({"width": 390, "height": 3000})
            page.locator(".mr-app").screenshot(path=str(OUT / "auction-estimates-mobile.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            mobile.add_cookies([cookies[admin.username, "darkly"]])
            page.goto(f"{BASE}/moon-rental/auctions/new/", wait_until="networkidle")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            browser.close()
    finally:
        Moon.objects.filter(pk__in=[m.pk for m in moons]).update(reserved_lot=None)
        Auction.objects.filter(pk__in=auctions).delete()
        Moon.objects.filter(pk__in=[m.pk for m in moons]).delete()
    assert not errors, errors
    print(
        "Five screenshots; clipboard, fallback, hidden quantities, day-first POST and mobile passed."
    )


if __name__ == "__main__":
    capture()
