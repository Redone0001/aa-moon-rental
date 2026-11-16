"""Optional Discord delivery, isolated from bidding and financial transactions."""

import logging
import re
from datetime import timedelta

import requests
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from moon_rental.models import Auction, Configuration, Notification

logger = logging.getLogger(__name__)
WEBHOOK = re.compile(r"https://discord\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+\Z")
API = "https://discord.com/api/v10"


def validate_webhook(value):
    if value and not WEBHOOK.fullmatch(value):
        raise ValidationError(
            "Use an HTTPS discord.com/api/webhooks/… URL without query parameters."
        )


def queue_dm(*, key, user_id, message):
    config = Configuration.objects.filter(pk=1).first()
    if (
        apps.is_installed("aadiscordmultiverse")
        and config
        and config.discord_dm_enabled
        and config.discord_guild_id
    ):
        Notification.objects.get_or_create(
            key=key, defaults={"user_id": user_id, "message": message}
        )


def queue_reminders(*, at=None):
    at = at or timezone.now()
    config = Configuration.objects.filter(pk=1).first()
    if not config or not config.discord_webhook_url:
        return
    for auction in Auction.objects.filter(settled_at=None, ends_at__gt=at):
        if auction.bidding_starts_at and auction.bidding_starts_at > at:
            continue
        # Send only the most recent crossed threshold after worker downtime.
        due = [
            h
            for h in auction.reminder_hours
            if at >= auction.ends_at - timedelta(hours=h)
            and (
                not auction.bidding_starts_at
                or auction.bidding_starts_at <= auction.ends_at - timedelta(hours=h)
            )
        ]
        if due:
            hour = min(due)
            key = f"reminder:{auction.pk}:{hour}"
            Notification.objects.filter(
                auction=auction, delivered_at=None, skipped_at=None
            ).exclude(key=key).update(skipped_at=at)
            Notification.objects.get_or_create(
                key=key,
                defaults={
                    "auction": auction,
                    "message": f"{auction.title}: {hour}h auction reminder · "
                    f"bidding closes {auction.ends_at:%d/%m/%Y %H:%M} UTC. "
                    f"{getattr(settings, 'SITE_URL', '').rstrip('/')}/moon-rental/auctions/{auction.pk}/",
                },
            )


class RetryDelivery(Exception):
    def __init__(self, seconds=60):
        self.seconds = min(86400, max(1, int(seconds)))


def discord_post(url, *, payload, headers=None):
    response = requests.post(
        url, json=payload, headers=headers or {}, timeout=(5, 15), allow_redirects=False
    )
    if response.status_code == 429:
        try:
            seconds = float(response.json().get("retry_after", 60)) + 1
        except (ValueError, TypeError):
            seconds = 60
        raise RetryDelivery(seconds)
    response.raise_for_status()
    if not 200 <= response.status_code < 300:
        raise RetryDelivery()
    return response


def deliver(notification, config):
    payload = {"content": notification.message[:2000], "allowed_mentions": {"parse": []}}
    if notification.user_id:
        if not (
            apps.is_installed("aadiscordmultiverse")
            and config.discord_dm_enabled
            and config.discord_guild_id
        ):
            return False
        from aadiscordmultiverse.app_settings import DISCORD_BOT_TOKEN
        from aadiscordmultiverse.models import MultiDiscordUser

        account = MultiDiscordUser.objects.filter(
            user_id=notification.user_id,
            user__is_active=True,
            guild_id=config.discord_guild_id,
        ).first()
        if not account or not DISCORD_BOT_TOKEN:
            return False
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        channel = discord_post(
            f"{API}/users/@me/channels", payload={"recipient_id": str(account.uid)}, headers=headers
        ).json()["id"]
        # Stable nonce suppresses immediate duplicate DMs after an interrupted delivery.
        payload.update(nonce=str(notification.pk), enforce_nonce=True)
        discord_post(f"{API}/channels/{channel}/messages", payload=payload, headers=headers)
    else:
        if not config.discord_webhook_url:
            return False
        validate_webhook(config.discord_webhook_url)
        discord_post(config.discord_webhook_url + "?wait=true", payload=payload)
    return True


def send_pending(*, at=None, limit=20):
    at = at or timezone.now()
    config = Configuration.objects.filter(pk=1).first()
    if not config:
        return 0
    ids = list(
        Notification.objects.filter(delivered_at=None, skipped_at=None, next_attempt_at__lte=at)
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    sent = 0
    for pk in ids:
        with transaction.atomic():
            notice = Notification.objects.select_for_update().select_related("auction").get(pk=pk)
            if notice.delivered_at or notice.skipped_at or notice.next_attempt_at > at:
                continue
            notice.attempts += 1
            expired = notice.auction_id and (
                notice.auction.settled_at or notice.auction.ends_at <= at
            )
            try:
                success = False if expired else deliver(notice, config)
            except Exception as exc:
                delay = (
                    exc.seconds
                    if isinstance(exc, RetryDelivery)
                    else min(3600, 60 * 2 ** min(notice.attempts, 6))
                )
                notice.next_attempt_at = at + timedelta(seconds=delay)
                # Do not log exception text: HTTP errors may contain webhook credentials.
                logger.warning("Moon Rental notification %s failed; retry scheduled.", pk)
            else:
                if success:
                    notice.delivered_at = at
                    sent += 1
                else:
                    notice.skipped_at = at
            notice.save()
    return sent


@transaction.atomic
def configure_notifications(*, actor, webhook_url, dm_enabled, guild_id):
    from moon_rental.permissions import require_admin
    from moon_rental.services.rentals import audit

    require_admin(actor)
    validate_webhook(webhook_url)
    config, _ = Configuration.objects.get_or_create(pk=1)
    config = Configuration.objects.select_for_update().get(pk=config.pk)
    config.discord_webhook_url = webhook_url
    config.discord_dm_enabled = dm_enabled
    config.discord_guild_id = guild_id
    config.full_clean(exclude=["default_wallet"])
    config.save(update_fields=["discord_webhook_url", "discord_dm_enabled", "discord_guild_id"])
    audit(actor, "notifications.configure", config)
    return config
