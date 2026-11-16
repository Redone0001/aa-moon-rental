import sys
from datetime import timedelta
from types import ModuleType
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from moon_rental.models import Configuration, Notification
from moon_rental.services.notifications import (
    configure_notifications,
    queue_dm,
    queue_reminders,
    send_pending,
)
from moon_rental.services.rentals import assign_moon, place_bid
from moon_rental.services.wallets import record_payment
from tests.test_wallets import entry

pytestmark = pytest.mark.django_db
WEBHOOK = "https://discord.com/api/webhooks/123/fictional-test-token"


@pytest.fixture
def config(wallet):
    config = Configuration.objects.get(pk=1)
    config.discord_webhook_url = WEBHOOK
    config.discord_guild_id = 123
    config.save()
    return config


def test_absent_optional_module_skips_dms(config, pilot):
    queue_dm(key="missing", user_id=pilot.pk, message="Test")
    assert not Notification.objects.exists()


def test_bid_invoice_and_payment_events_are_transactional(
    config, manager, pilot, other_pilot, moon, auction, wallet
):
    with patch("moon_rental.services.notifications.apps.is_installed", return_value=True):
        lot = auction.lots.get()
        place_bid(actor=pilot, lot_id=lot.pk, amount=100)
        place_bid(actor=pilot, lot_id=lot.pk, amount=200)
        assert not Notification.objects.exists()
        place_bid(actor=other_pilot, lot_id=lot.pk, amount=300)
        notice = Notification.objects.get()
        assert notice.user_id == pilot.pk and "outbid" in notice.message
        with pytest.raises(RuntimeError), transaction.atomic():
            place_bid(actor=pilot, lot_id=lot.pk, amount=400)
            raise RuntimeError("rollback")
        assert Notification.objects.count() == 1
        # Free a different moon for an immediate one-payment invoice.
        from moon_rental.models import Moon

        other = Moon.objects.create(id=1, name="Invoice moon", synced_at=timezone.now())
        rental = assign_moon(
            actor=manager,
            moon_id=other.pk,
            renter=pilot,
            price=100,
            duration_months=1,
            billing_mode="once",
        )
        invoice = rental.invoices.get()
        assert "Invoice received" in Notification.objects.get(key=f"invoice:{invoice.pk}").message
        payment = entry(invoice, wallet, amount=40)
        assert record_payment(wallet.pk, payment)
        assert not record_payment(wallet.pk, payment)
        assert record_payment(wallet.pk, entry(invoice, wallet, id=124, amount=60))
        messages = list(
            Notification.objects.filter(key__startswith="payment:").values_list(
                "message", flat=True
            )
        )
        assert (
            len(messages) == 2 and "partially paid" in messages[0] and "fully paid" in messages[1]
        )


def test_reminder_thresholds_deduplication_and_downtime(config, auction):
    at = auction.ends_at - timedelta(hours=24)
    auction.bidding_starts_at = at - timedelta(hours=1)
    auction.save()
    queue_reminders(at=at - timedelta(seconds=1))
    assert not Notification.objects.exists()
    queue_reminders(at=at)
    queue_reminders(at=at)
    assert Notification.objects.count() == 1
    # After a worker outage, stale 24h messages must not accompany the 6h reminder.
    queue_reminders(at=auction.ends_at - timedelta(hours=5))
    assert Notification.objects.filter(skipped_at=None).get().key == f"reminder:{auction.pk}:6"
    queue_reminders(at=auction.ends_at)
    assert Notification.objects.count() == 2


def test_no_premature_or_disabled_reminders(config, auction):
    auction.bidding_starts_at = auction.ends_at - timedelta(minutes=30)
    auction.reminder_hours = [24, 12, 6, 1]
    auction.save()
    queue_reminders(at=auction.ends_at - timedelta(minutes=20))
    assert not Notification.objects.exists()
    auction.bidding_starts_at = None
    auction.reminder_hours = []
    auction.save()
    queue_reminders(at=auction.ends_at - timedelta(minutes=20))
    assert not Notification.objects.exists()


def test_webhook_retries_honour_rate_limits_and_expire(config, auction):
    at = timezone.now()
    notice = Notification.objects.create(
        key="webhook", auction=auction, message="Reminder", next_attempt_at=at
    )
    response = Mock(status_code=429)
    response.json.return_value = {"retry_after": 120}
    with patch("moon_rental.services.notifications.requests.post", return_value=response) as post:
        assert send_pending(at=at) == 0
        notice.refresh_from_db()
        assert notice.attempts == 1 and notice.next_attempt_at == at + timedelta(seconds=121)
        assert post.call_args.kwargs["json"]["allowed_mentions"] == {"parse": []}
        send_pending(at=at + timedelta(seconds=60))
        assert post.call_count == 1
    response.status_code = 200
    with patch("moon_rental.services.notifications.requests.post", return_value=response) as post:
        assert send_pending(at=at + timedelta(seconds=121)) == 1
        assert send_pending(at=at + timedelta(seconds=122)) == 0
        assert post.call_count == 1
    Notification.objects.create(key="expired", auction=auction, message="Old", next_attempt_at=at)
    with patch("moon_rental.services.notifications.requests.post") as post:
        send_pending(at=auction.ends_at)
        post.assert_not_called()


def test_multiverse_account_and_bot_token_are_used(config, pilot):
    account_model = Mock()
    account_model.objects.filter.return_value.first.return_value = Mock(uid=999)
    models = ModuleType("aadiscordmultiverse.models")
    models.MultiDiscordUser = account_model
    settings = ModuleType("aadiscordmultiverse.app_settings")
    settings.DISCORD_BOT_TOKEN = "test-only"
    at = timezone.now()
    Notification.objects.create(
        key="dm",
        user=pilot,
        message="Outbid",
        embed={"title": "You have been outbid"},
        next_attempt_at=at,
    )
    response = Mock(status_code=200)
    response.json.return_value = {"id": "1234"}
    with (
        patch.dict(
            sys.modules,
            {"aadiscordmultiverse.models": models, "aadiscordmultiverse.app_settings": settings},
        ),
        patch("moon_rental.services.notifications.apps.is_installed", return_value=True),
        patch("moon_rental.services.notifications.requests.post", return_value=response) as post,
    ):
        assert send_pending(at=at) == 1
        assert post.call_count == 2
        assert post.call_args_list[0].kwargs["json"] == {"recipient_id": "999"}
        assert post.call_args_list[1].args[0].endswith("/channels/1234/messages")
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bot test-only"}
        assert post.call_args.kwargs["json"]["embeds"] == [{"title": "You have been outbid"}]
        account_model.objects.filter.assert_called_once_with(
            user_id=pilot.pk, user__is_active=True, guild_id=123
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/x",
        "https://example.com/webhook",
        "https://discord.com@127.0.0.1/api/webhooks/1/x",
        "https://discord.com/api/webhooks/1/x?redirect=foo",
    ],
)
def test_webhook_validation_and_service_permissions(admin_user, manager, url):
    with pytest.raises(ValidationError):
        configure_notifications(actor=admin_user, webhook_url=url, dm_enabled=True, guild_id=123)
    with pytest.raises(PermissionDenied):
        configure_notifications(actor=manager, webhook_url=WEBHOOK, dm_enabled=True, guild_id=123)


def test_django_admin_changes_only_discord_settings(client, admin_user, manager, config):
    admin_user.is_staff = True
    admin_user.save()
    client.force_login(admin_user)
    url = reverse("admin:moon_rental_configuration_change", args=[1])
    response = client.post(
        url,
        {
            "discord_webhook_url": WEBHOOK,
            "discord_dm_enabled": "on",
            "discord_guild_id": 456,
            "_save": "Save",
        },
    )
    assert response.status_code == 302, response.content.decode()[:1000]
    config.refresh_from_db()
    assert config.discord_guild_id == 456 and config.default_wallet_id
    manager.is_staff = True
    manager.save()
    client.force_login(manager)
    assert client.get(url).status_code == 403
