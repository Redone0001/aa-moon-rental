import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from moon_rental.models import Configuration, Notification
from moon_rental.services.discord_messages import (
    app_url,
    copy_value,
    invoice_embed,
    outbid_embed,
    reminder_embed,
)
from moon_rental.services.notifications import deliver, queue_reminders
from moon_rental.services.rentals import assign_moon, place_bid
from moon_rental.services.wallets import record_payment, register_wallet, set_default_wallet
from tests.test_wallets import entry

pytestmark = pytest.mark.django_db


def fields(embed):
    return {field["name"]: field["value"] for field in embed["fields"]}


@pytest.fixture
def dm_config(wallet):
    config = Configuration.objects.get(pk=1)
    config.discord_guild_id = 123
    config.discord_webhook_url = "https://discord.com/api/webhooks/123/fictional-test-token"
    config.save()
    return config


@pytest.fixture
def invoice(manager, pilot, moon, wallet):
    return assign_moon(
        actor=manager,
        renter=pilot,
        moon_id=moon.pk,
        price="3000000000.01",
        duration_months=3,
        billing_mode="once",
    ).invoices.get()


def assert_embed_limits(embed):
    # Count UTF-16 units as well so supplementary Unicode remains within limits.
    def length(value):
        return len(value.encode("utf-16-le")) // 2

    assert length(embed["title"]) <= 256
    assert length(embed["description"]) <= 4096
    assert len(embed["fields"]) <= 25
    total = length(embed["title"]) + length(embed["description"]) + length(embed["footer"]["text"])
    for field in embed["fields"]:
        assert length(field["name"]) <= 256
        assert 0 < length(field["value"]) <= 1024
        total += length(field["name"]) + length(field["value"])
    assert total <= 6000
    json.dumps(embed)  # Decimal and datetime must not escape into the JSON payload.


def test_invoice_embeds_include_exact_payment_details_and_context(invoice, wallet):
    with override_settings(SITE_URL="https://auth.example.com"):
        embed = invoice_embed(invoice)
    values = fields(embed)
    assert embed["title"] == "Invoice received"
    assert values["Moon"] == invoice.rental.moon.name
    assert values["Billing"] == "One payment"
    assert values["Billing period"] == f"{invoice.period:%B %Y}"
    assert "End date exclusive" in values["Rental period (UTC)"]
    assert values["Amount to pay (ISK)"] == "```\n3000000000.01\n```"
    assert values["Send to"] == f"```\n{invoice.recipient_name}\n```"
    assert values["Payment reason / reference"] == f"```\n{invoice.code}\n```"
    assert str(wallet.eve_id) in values["Recipient wallet"]
    assert parse_qs(urlsplit(embed["url"]).query) == {
        "status": ["all"],
        "reference": [invoice.code],
    }
    assert_embed_limits(embed)


def test_payment_embeds_restate_full_payment_instructions_and_keep_snapshots(
    dm_config, invoice, wallet, admin_user
):
    with patch("moon_rental.services.notifications.apps.is_installed", return_value=True):
        assert record_payment(wallet.pk, entry(invoice, wallet, amount="1000000000.00"))
        partial = Notification.objects.get(key__startswith="payment:")
        values = fields(partial.embed)
        assert partial.embed["title"] == "Invoice partially paid"
        assert values["Remaining to pay (ISK)"] == "```\n2000000000.01\n```"
        assert values["Total paid (ISK)"] == "1,000,000,000.00"
        assert values["This payment (ISK)"] == "1,000,000,000.00"
        assert values["Send to"] == f"```\n{wallet.name}\n```"
        before = deepcopy(partial.embed)
        other = register_wallet(
            actor=admin_user, kind="character", eve_id=999, name="Other recipient", character_id=999
        )
        set_default_wallet(actor=admin_user, account_id=other.pk)
        assert record_payment(wallet.pk, entry(invoice, wallet, id=124, amount="2000000000.01"))
        paid = Notification.objects.exclude(pk=partial.pk).get(key__startswith="payment:")
        assert paid.embed["title"] == "Invoice fully paid"
        assert "No further payment" in paid.embed["description"]
        assert fields(paid.embed)["Remaining to pay (ISK)"] == "```\n0.00\n```"
        assert fields(paid.embed)["Send to"] == f"```\n{wallet.name}\n```"
        partial.refresh_from_db()
        assert partial.embed == before
        assert_embed_limits(partial.embed)
        assert_embed_limits(paid.embed)


def test_invoice_creation_queues_embed_in_same_transaction(dm_config, manager, pilot, moon):
    with patch("moon_rental.services.notifications.apps.is_installed", return_value=True):
        invoice = assign_moon(
            actor=manager,
            renter=pilot,
            moon_id=moon.pk,
            price="123.45",
            duration_months=1,
            billing_mode="once",
        ).invoices.get()
    notice = Notification.objects.get(key=f"invoice:{invoice.pk}")
    assert fields(notice.embed)["Amount to pay (ISK)"] == "```\n123.45\n```"
    assert invoice.code in fields(notice.embed)["Payment reason / reference"]


def test_outbid_embed_is_queued_with_previous_bid_and_auction_terms(
    dm_config, auction, pilot, other_pilot
):
    lot = auction.lots.get()
    with patch("moon_rental.services.notifications.apps.is_installed", return_value=True):
        previous = place_bid(actor=pilot, lot_id=lot.pk, amount="500000000.01")
        new = place_bid(actor=other_pilot, lot_id=lot.pk, amount="600000000.02")
    notice = Notification.objects.get(key=f"outbid:{new.pk}")
    values = fields(notice.embed)
    assert notice.user_id == pilot.pk
    assert values["Your previous bid (ISK)"] == "500,000,000.01"
    assert values["Current highest bid (ISK)"] == "```\n600000000.02\n```"
    assert values["Billing"] == "Per month"
    assert f"<t:{int(auction.ends_at.timestamp())}:R>" in values["Bidding closes"]
    assert reverse("moon_rental:auction", args=[auction.pk]) in notice.embed["url"]
    assert notice.embed == outbid_embed(auction, lot, previous_bid=previous, new_bid=new)
    assert_embed_limits(notice.embed)


def test_public_reminders_have_auction_details_without_private_invoice_fields(dm_config, auction):
    at = auction.ends_at - timedelta(hours=1)
    queue_reminders(at=at)
    notice = Notification.objects.get()
    assert notice.embed["title"] == "Auction reminder · 1h"
    assert fields(notice.embed)["Moons offered"] == "1"
    assert "Rental period (UTC)" in fields(notice.embed)
    assert "Payment reason / reference" not in fields(notice.embed)
    response = Mock(status_code=200)
    with patch("moon_rental.services.notifications.requests.post", return_value=response) as post:
        assert deliver(notice, dm_config)
        payload = post.call_args.kwargs["json"]
    assert payload["embeds"] == [notice.embed]
    assert "content" not in payload
    assert payload["allowed_mentions"] == {"parse": []}
    assert_embed_limits(notice.embed)


def test_legacy_notifications_keep_their_plain_text(dm_config):
    notice = Notification.objects.create(key="legacy", message="Existing queued reminder")
    with patch(
        "moon_rental.services.notifications.requests.post", return_value=Mock(status_code=200)
    ) as post:
        assert deliver(notice, dm_config)
    assert post.call_args.kwargs["json"] == {
        "content": notice.message,
        "allowed_mentions": {"parse": []},
    }


def test_large_values_long_unicode_names_and_markdown_stay_valid(invoice):
    invoice.rental.moon.name = "🛰" * 255
    invoice.rental.moon.solar_system = "_" * 255
    invoice.rental.moon.region = "🛰" * 255
    invoice.recipient_name = "🛰" * 255
    invoice.amount = Decimal("999999999999999999.99")
    rich = invoice_embed(invoice)
    assert fields(rich)["Amount to pay (ISK)"] == "```\n999999999999999999.99\n```"
    assert fields(rich)["System"] == "\\_" * 255
    assert_embed_limits(rich)


def test_markdown_in_auction_names_is_escaped(auction):
    auction.title = "[click](https://example.invalid) @everyone"
    reminder = reminder_embed(auction, 24, at=timezone.now())
    assert "\\[click\\]" in fields(reminder)["Auction"]
    assert copy_value("Name `with` ticks") == "Name \\`with\\` ticks"
    assert_embed_limits(reminder)


@pytest.mark.parametrize(
    "base",
    [
        "",
        "ftp://auth.example.com",
        "https://secret:password@auth.example.com",
        "https://auth.example.com?token=secret",
        "https://auth.example.com/" + "a" * 2000,
    ],
)
def test_invalid_or_secret_bearing_site_urls_are_omitted(base):
    with override_settings(SITE_URL=base):
        assert app_url("moon_rental:invoices") is None


def test_invoice_links_only_show_the_intended_invoice_to_its_owner(
    client, invoice, pilot, other_pilot
):
    url = reverse("moon_rental:invoices") + f"?status=all&reference={invoice.code}"
    client.force_login(pilot)
    response = client.get(url)
    assert response.status_code == 200 and response.context["invoices"].paginator.count == 1
    assert invoice.code.encode() in response.content and b"Show all invoices" in response.content
    assert b"reference=" in response.content
    client.force_login(other_pilot)
    response = client.get(url)
    assert response.status_code == 200 and response.context["invoices"].paginator.count == 0
