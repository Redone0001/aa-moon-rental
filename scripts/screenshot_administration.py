"""Verify the requested workflows and capture them using disposable fictional records."""

import math
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from screenshots import BASE, OUT, session  # isort: skip

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from moon_rental.models import Auction, Bid, Invoice, Moon, Rental
from moon_rental.services.maintenance import run_lifecycle
from moon_rental.services.rentals import open_auction, place_bid, settle_auction
from moon_rental.services.wallets import default_wallet, record_payment


def capture():
    if (
        settings.SETTINGS_MODULE != "tests.settings"
        or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
    ):
        raise RuntimeError("Use only the disposable local SQLite demo.")
    OUT.mkdir(parents=True, exist_ok=True)
    admin = User.objects.get(username="Rental Administrator")
    pilot = User.objects.get(username="Astra Vale")
    other = User.objects.get(username="Orion Drake")
    now = timezone.now()
    month = now.date().replace(day=1)
    moons = []
    auctions = []
    async_setting = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    staff = admin.is_staff
    errors = []
    try:
        for i in range(10):
            moons.append(
                Moon.objects.create(
                    pk=49992000 + i,
                    name=[
                        "J-A5QD IV - 18",
                        "P3EN-E VII - 8",
                        "M-OEE8 V - 16",
                        "Q-CAB2 II - 11",
                        "H-W9TY IX - 6",
                    ][i % 5],
                    solar_system="J-A5QD" if i % 2 else "M-OEE8",
                    region="Vale of the Silent",
                    constellation="A3-T8B",
                    rarity=64 if i % 2 else 32,
                    synced_at=now,
                    composition=[
                        {"name": "Loparite", "percent": 60},
                        {"name": "Monazite", "percent": 40},
                    ],
                    pull_30d={
                        "volume_per_day": "960400",
                        "ores": [
                            {"name": "Loparite", "units": 1728720},
                            {"name": "Monazite", "units": 1152480},
                        ],
                        "value": "4955664000",
                        "pricing": "ore",
                    },
                )
            )
        prior_end = timezone.make_aware(
            timezone.datetime.combine(month, timezone.datetime.min.time())
        ) - timedelta(days=3)
        with patch(
            "moon_rental.services.rentals.timezone.now", return_value=prior_end - timedelta(days=3)
        ):
            past = open_auction(
                actor=admin,
                title="November mining · Core allocation",
                moon_ids=[m.pk for m in moons[:3]],
                ends_at=prior_end,
                starts_on=month,
                duration_months=3,
                minimum_bid="300000000",
                starting_prices={moons[0].pk: "500000000"},
            )
            auctions.append(past.pk)
            place_bid(actor=pilot, lot_id=past.lots.get(moon=moons[0]).pk, amount="650000000")
            place_bid(actor=other, lot_id=past.lots.get(moon=moons[1]).pk, amount="450000000")
        settle_auction(past.pk, at=prior_end)
        # More than one history page, reusing the same free moon across completed auctions.
        for i in range(6):
            end = prior_end - timedelta(days=(i + 1) * 3)
            with patch(
                "moon_rental.services.rentals.timezone.now", return_value=end - timedelta(days=2)
            ):
                history = open_auction(
                    actor=admin,
                    title=f"Reserve allocation · Round {6 - i}",
                    moon_ids=[moons[3].pk],
                    ends_at=end,
                    starts_on=(end + timedelta(days=1)).date(),
                    duration_months=1,
                    minimum_bid="200000000",
                )
                auctions.append(history.pk)
            settle_auction(history.pk, at=end)
        run_lifecycle(at=now)
        for i, invoice in enumerate(Invoice.objects.filter(rental__moon__in=moons).order_by("pk")):
            record_payment(
                default_wallet().pk,
                {
                    "id": 990000 + i,
                    "amount": str(invoice.amount if i else Decimal("250000000")),
                    "ref_type": "player_donation",
                    "second_party_id": default_wallet().eve_id,
                    "reason": invoice.code,
                    "date": timezone.now().isoformat(),
                },
            )
        live = open_auction(
            actor=admin,
            title="Winter expansion · Reserve release",
            moon_ids=[m.pk for m in moons[4:7]],
            ends_at=now + timedelta(days=2),
            starts_on=(now + timedelta(days=4)).date(),
            duration_months=3,
            minimum_bid="200000000",
            starting_prices={moons[5].pk: "500000000", moons[6].pk: "350000000"},
        )
        auctions.append(live.pk)
        place_bid(actor=other, lot_id=live.lots.get(moon=moons[4]).pk, amount="850000000")
        place_bid(actor=pilot, lot_id=live.lots.get(moon=moons[5]).pk, amount="1100000000")
        scheduled = open_auction(
            actor=admin,
            title="December expansion · Scheduled release",
            moon_ids=[m.pk for m in moons[7:9]],
            bidding_starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=7),
            starts_on=(now + timedelta(days=9)).date(),
            duration_months=6,
            minimum_bid="300000000",
        )
        auctions.append(scheduled.pk)
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        cookies = {theme: session(admin.username, theme) for theme in ("darkly", "flatly")}
        # Playwright sync runs an event loop; this isolated, single-threaded demo
        # performs ORM assertions between browser actions. Never enable in production.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for theme in ("darkly", "flatly"):
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1050}, ignore_https_errors=True
                )
                context.add_cookies([cookies[theme]])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))

                def goto(route, page=page):
                    response = page.goto(BASE + route, wait_until="networkidle")
                    assert response.status == 200, (route, response.status)

                def snap(name, page=page, theme=theme):
                    # AA scrolls its content pane, so grow the capture viewport to
                    # include the entire screen rather than clipping that pane.
                    app = page.locator(".mr-app")
                    if app.count():
                        page.set_viewport_size(
                            {"width": 1600, "height": math.ceil(app.bounding_box()["height"] + 140)}
                        )
                    page.screenshot(path=str(OUT / f"{name}-{theme}.png"), full_page=True)
                    page.set_viewport_size({"width": 1600, "height": 1050})

                goto("/moon-rental/administration/")
                expect(
                    page.get_by_role("heading", name="Your moon programme, at a glance.")
                ).to_be_visible()
                snap("admin-overview")
                page.get_by_text("Open global invoice ledger", exact=False).click()
                page.wait_for_load_state("networkidle")
                expect(page.get_by_role("heading", name="Global invoice ledger")).to_be_visible()
                page.locator("#ledger-status").select_option("all")
                page.get_by_role("button", name="Filter", exact=True).click()
                page.wait_for_load_state("networkidle")
                snap("admin-invoice-ledger")
                goto(f"/moon-rental/auctions/{live.pk}/")
                field = page.locator("input[data-bid-step]").first
                field.fill("900000000.01")
                field.press("ArrowUp")
                expect(field).to_have_value("1000000000.01")
                field.press("ArrowDown")
                expect(field).to_have_value("900000000.01")
                field.hover()
                page.mouse.wheel(0, -100)
                expect(field).to_have_value("1000000000.01")
                page.mouse.wheel(0, 100)
                expect(field).to_have_value("900000000.01")
                page.get_by_role("button", name="Increase bid by 100 million ISK").first.click()
                expect(field).to_have_value("1000000000.01")
                page.get_by_role("button", name="Decrease bid by 100 million ISK").first.click()
                expect(field).to_have_value("900000000.01")
                page.evaluate("window.scrollTo(0, 0)")
                snap("auction-bid-controls")
                if theme == "darkly":
                    field.fill("9999999999.99")
                    page.get_by_role("button", name="Place bid", exact=True).first.click()
                    page.wait_for_load_state("networkidle")
                    assert Bid.objects.filter(lot__auction=live, amount="9999999999.99").exists()
                    Bid.objects.filter(lot__auction=live, amount="9999999999.99").delete()
                goto("/moon-rental/auctions/new/")
                page.locator('[name="title"]').fill("Spring moon programme")
                page.locator('[name="starts_on"]').fill("04/02/2030")
                page.locator('[name="bidding_starts_at"]').fill("01/02/2030 16:20")
                page.locator('[name="ends_at"]').fill("03/02/2030 16:20")
                page.locator('[name="previous_q"]').fill("November mining")
                page.get_by_role("button", name="Search", exact=True).click()
                page.wait_for_load_state("networkidle")
                page.get_by_role("button", name="Add available moons").click()
                page.wait_for_load_state("networkidle")
                expect(page.locator("[data-moon-selection-count]")).to_have_text("1")
                expect(page.locator('[name="title"]')).to_have_value("Spring moon programme")
                expect(page.locator('[name="bidding_starts_at"]')).to_have_value("01/02/2030 16:20")
                field = page.locator(f'[name="starting_price_{moons[2].pk}"]')
                expect(field).to_have_value("300000000.00")
                field.fill("450000000.01")
                page.get_by_role("button", name="Apply filters").click()
                page.wait_for_load_state("networkidle")
                expect(field).to_have_value("450000000.01")
                field.scroll_into_view_if_needed()
                snap("auction-schedule-create")
                if theme == "flatly":
                    page.get_by_role("button", name="Create auction", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    created = Auction.objects.get(title="Spring moon programme")
                    auctions.append(created.pk)
                    assert created.lots.get().starting_price == Decimal("450000000.01")
                    assert created.bidding_starts_at.year == 2030
                    expect(page.get_by_role("button", name="Place bid")).to_have_count(0)
                if theme == "darkly":
                    goto(f"/moon-rental/auctions/{scheduled.pk}/")
                    expect(page.get_by_role("button", name="Place bid")).to_have_count(0)
                    snap("auction-scheduled")
                    goto("/admin/moon_rental/configuration/1/change/")
                    expect(page.locator('[name="discord_webhook_url"]')).to_be_visible()
                    page.locator('[name="discord_guild_id"]').fill("123456789012345678")
                    snap("discord-configuration")
                context.close()
            mobile = browser.new_context(**p.devices["iPhone 13"], ignore_https_errors=True)
            mobile.add_cookies([cookies["darkly"]])
            page = mobile.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            for route, name in [
                ("/moon-rental/administration/", "admin-overview"),
                ("/moon-rental/administration/invoices/", "admin-invoice-ledger"),
                ("/moon-rental/auctions/new/", "auction-schedule-create"),
            ]:
                response = page.goto(BASE + route, wait_until="networkidle")
                assert response.status == 200
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    route
                )
                height = math.ceil(page.locator(".mr-app").bounding_box()["height"] + 140)
                page.set_viewport_size({"width": 390, "height": height})
                page.screenshot(path=str(OUT / f"{name}-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
            browser.close()
        assert not errors, errors
        print(
            "Captured 13 screenshots. Browser checks passed for arrows, wheel, exact bid submission, reused moons, preserved terms/prices, scheduled bidding, and mobile overflow."
        )
    finally:
        if async_setting is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = async_setting
        admin.is_staff = staff
        admin.save(update_fields=["is_staff"])
        ids = [m.pk for m in moons]
        Moon.objects.filter(pk__in=ids).update(current_rental=None, reserved_lot=None)
        Rental.objects.filter(moon_id__in=ids).delete()
        Auction.objects.filter(pk__in=auctions).delete()
        Moon.objects.filter(pk__in=ids).delete()


if __name__ == "__main__":
    capture()
