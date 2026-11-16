"""Discord embeds snapshotted with each event, with exact copyable payment values."""

import re
from datetime import timezone as dt_timezone
from urllib.parse import urlencode, urlsplit

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.urls import reverse

from moon_rental.models import Invoice

BLUE = 0x3498DB
AMBER = 0xF0B232
GREEN = 0x2ECC71
FOOTER = "Moon Rental · Snapshot at notification time · Rental dates are UTC"
MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>~|])")


def text(value):
    """Display untrusted names literally rather than as Discord Markdown or links."""
    return MARKDOWN.sub(r"\\\1", str(value))


def copy_value(value):
    value = str(value)
    # Never insert invisible characters or escape sequences into copyable values.
    # A name containing backticks uses escaped display text instead of a broken fence.
    return text(value) if "`" in value else f"```\n{value}\n```"


def field(name, value, *, inline=True):
    return {"name": name, "value": str(value), "inline": inline}


def money(value):
    return f"{value:,.2f}"


def app_url(view, *, args=None, query=None):
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    url = base + reverse(view, args=args)
    if query:
        url += "?" + urlencode(query)
    return url if len(url) <= 2000 else None


def timestamp(value):
    seconds = int(value.timestamp())
    return f"<t:{seconds}:F>\n<t:{seconds}:R>"


def lease_period(starts, ends):
    return f"{starts:%d %b %Y} → {ends:%d %b %Y}\nEnd date exclusive"


def moon_fields(moon):
    return [
        field("Moon", text(moon.name), inline=False),
        field("System", text(moon.solar_system) or "Unknown"),
        field("Region", text(moon.region) or "Unknown"),
    ]


def embed(*, title, description, color, fields, at, url=None):
    result = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "timestamp": at.astimezone(dt_timezone.utc).isoformat(),
        "footer": {"text": FOOTER},
    }
    if url:
        result["url"] = url
    return result


def invoice_embed(invoice, *, receipt=None):
    rental = invoice.rental
    paid = invoice.paid_amount
    remaining = max(invoice.amount - paid, 0)
    settled = invoice.status == Invoice.Status.PAID
    if receipt:
        title = "Invoice fully paid" if settled else "Invoice partially paid"
        description = (
            "Payment confirmed. No further payment is needed for this invoice."
            if settled
            else "Payment confirmed. Copy the remaining amount, recipient and payment reason below to complete payment."
        )
        color = GREEN if settled else AMBER
        at = receipt.received_at
    else:
        title = "Invoice received"
        description = "Your rental invoice is ready. Copy the exact amount and recipient below, and paste the payment reference into the EVE transfer reason."
        color = BLUE
        at = invoice.created_at
    fields = moon_fields(rental.moon) + [
        field("Billing period", f"{invoice.period:%B %Y}"),
        field("Billing", rental.get_billing_mode_display()),
        field("Rental period (UTC)", lease_period(rental.starts_on, rental.ends_on), inline=False),
        field("Invoice total (ISK)", money(invoice.amount)),
        field("Total paid (ISK)", money(paid)),
    ]
    if receipt:
        fields.extend(
            [
                field("This payment (ISK)", money(receipt.amount)),
                field("Payment received", timestamp(receipt.received_at)),
                field("Received by", text(receipt.account.name)),
            ]
        )
    fields.extend(
        [
            field(
                "Remaining to pay (ISK)" if receipt else "Amount to pay (ISK)",
                copy_value(f"{remaining:.2f}"),
                inline=False,
            ),
            field("Send to", copy_value(invoice.recipient_name), inline=False),
            field("Payment reason / reference", copy_value(invoice.code), inline=False),
        ]
    )
    if invoice.recipient:
        fields.append(
            field(
                "Recipient wallet",
                f"{invoice.recipient.get_kind_display()} · ID {invoice.recipient.eve_id}",
                inline=False,
            )
        )
    return embed(
        title=title,
        description=description,
        color=color,
        fields=fields,
        at=at,
        url=app_url("moon_rental:invoices", query={"status": "all", "reference": invoice.code}),
    )


def outbid_embed(auction, lot, *, previous_bid, new_bid):
    fields = moon_fields(lot.moon) + [
        field("Auction", text(auction.title), inline=False),
        field("Your previous bid (ISK)", money(previous_bid.amount)),
        field("Current highest bid (ISK)", copy_value(f"{new_bid.amount:.2f}")),
        field("Starting price (ISK)", money(lot.starting_price)),
        field("Billing", auction.get_billing_mode_display()),
        field("Bidding closes", timestamp(auction.ends_at), inline=False),
        field(
            "Rental period (UTC)",
            lease_period(
                auction.starts_on, auction.starts_on + relativedelta(months=auction.duration_months)
            ),
            inline=False,
        ),
    ]
    return embed(
        title="You have been outbid",
        description="Another member has taken the lead on this moon. Open the auction to review the current bids and place a higher bid.",
        color=AMBER,
        fields=fields,
        at=new_bid.created_at,
        url=app_url("moon_rental:auction", args=[auction.pk]),
    )


def reminder_embed(auction, hours, *, at):
    return embed(
        title=f"Auction reminder · {hours}h",
        description="Bidding is open. Check the closing time below for the exact time remaining and open the auction to see each moon’s starting price and highest bid.",
        color=AMBER if hours == 1 else BLUE,
        at=at,
        fields=[
            field("Auction", text(auction.title), inline=False),
            field("Moons offered", auction.lots.count()),
            field("Billing", auction.get_billing_mode_display()),
            field("Bidding closes", timestamp(auction.ends_at), inline=False),
            field(
                "Rental period (UTC)",
                lease_period(
                    auction.starts_on,
                    auction.starts_on + relativedelta(months=auction.duration_months),
                ),
                inline=False,
            ),
            field("Rental term", f"{auction.duration_months} months"),
        ],
        url=app_url("moon_rental:auction", args=[auction.pk]),
    )
