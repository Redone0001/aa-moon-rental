"""Render local embed previews from real payload builders using fictional data.

These are layout previews, not screenshots from a connected Discord account.
"""

import html
import json
import re
from datetime import date, datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from screenshots import OUT  # isort: skip

from django.test import override_settings
from playwright.sync_api import sync_playwright

from moon_rental.services.discord_messages import invoice_embed, outbid_embed, reminder_embed

STYLE = """
* { box-sizing: border-box; } body { margin: 0; padding: 36px; font: 15px/1.45 system-ui, sans-serif; background: var(--page); color: var(--text); }
body.dark { --page:#1e1f22; --card:#2b2d31; --text:#f2f3f5; --muted:#b5bac1; --code:#1e1f22; --line:#3f4147; --link:#6ab9ff; }
body.light { --page:#f2f3f5; --card:#fff; --text:#2e3338; --muted:#5c6169; --code:#f2f3f5; --line:#d9dce0; --link:#006ce7; }
h1 { font-size:27px; margin:0 0 4px; } .intro { color:var(--muted); margin:0 0 28px; }
.cards { display:grid; gap:24px; align-items:start; } .payments { grid-template-columns:repeat(3,minmax(0,1fr)); } .auctions { grid-template-columns:repeat(2,minmax(0,1fr)); }
article { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:5px; padding:22px; min-width:0; }
h2 { font-size:18px; margin:0 0 12px; color:var(--link); } h2 a { color:inherit; text-decoration:none; }
.description { margin:0 0 20px; } .fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px 16px; }
.field { min-width:0; } .field.wide { grid-column:1 / -1; } .label { font-size:13px; font-weight:700; margin-bottom:5px; }
.value { white-space:pre-line; overflow-wrap:anywhere; } pre { margin:0; padding:11px 13px; border:1px solid var(--line); border-radius:4px; background:var(--code); overflow-wrap:anywhere; white-space:pre-wrap; font:14px/1.4 ui-monospace, monospace; }
.footer { border-top:1px solid var(--line); padding-top:15px; margin-top:22px; font-size:11px; color:var(--muted); }
@media(max-width:700px) { body{padding:18px}.cards{grid-template-columns:1fr} }
"""


def samples():
    at = datetime(2026, 11, 16, 16, 20, tzinfo=tz.utc)
    moon = SimpleNamespace(
        name="J-A5QD IV - 12", solar_system="J-A5QD", region="Vale of the Silent"
    )
    wallet = SimpleNamespace(
        name="Orbital Industries",
        eve_id=98000001,
        get_kind_display=lambda: "Corporation (master wallet)",
    )
    rental = SimpleNamespace(
        moon=moon,
        starts_on=date(2026, 11, 1),
        ends_on=date(2027, 2, 1),
        get_billing_mode_display=lambda: "Per month",
    )
    invoice = SimpleNamespace(
        rental=rental,
        amount=Decimal("3000000000.01"),
        paid_amount=Decimal("0"),
        status="open",
        period=date(2026, 11, 1),
        recipient=wallet,
        recipient_name=wallet.name,
        code="moon-rental-A1B2.C3D4",
        created_at=at,
    )
    receipt = SimpleNamespace(
        amount=Decimal("1000000000"), received_at=at + timedelta(hours=1), account=wallet
    )
    with override_settings(SITE_URL="https://auth.example.invalid"):
        issued = invoice_embed(invoice)
        invoice.paid_amount = Decimal("1000000000")
        partial = invoice_embed(invoice, receipt=receipt)
        invoice.paid_amount = invoice.amount
        invoice.status = "paid"
        receipt.amount = Decimal("2000000000.01")
        receipt.received_at = at + timedelta(hours=2)
        paid = invoice_embed(invoice, receipt=receipt)
        auction = SimpleNamespace(
            pk=42,
            title="Winter expansion · Premium moons",
            ends_at=at + timedelta(hours=6),
            starts_on=date(2026, 11, 20),
            duration_months=3,
            get_billing_mode_display=lambda: "Per month",
            lots=SimpleNamespace(count=lambda: 12),
        )
        lot = SimpleNamespace(moon=moon, starting_price=Decimal("500000000"))
        previous = SimpleNamespace(amount=Decimal("800000000"))
        new = SimpleNamespace(amount=Decimal("900000000.01"), created_at=at)
        return {
            "payments": [issued, partial, paid],
            "auctions": [
                outbid_embed(auction, lot, previous_bid=previous, new_bid=new),
                reminder_embed(auction, 6, at=at),
            ],
        }


def render_value(value):
    if value.startswith("```\n") and value.endswith("\n```"):
        return f"<pre><code>{html.escape(value[4:-4])}</code></pre>"

    # The preview resolves Discord's timestamp syntax to illustrative readable text.
    def stamp(match):
        when = datetime.fromtimestamp(int(match[1]), tz=tz.utc)
        return (
            when.strftime("%d %B %Y at %H:%M UTC")
            if match[2] == "F"
            else "Relative time shown by Discord"
        )

    value = re.sub(r"<t:(\d+):([FR])>", stamp, value)
    value = re.sub(r"\\([\\`*_{}\[\]()<>~|])", r"\1", value)
    return f'<div class="value">{html.escape(value)}</div>'


def render_cards(embeds):
    cards = []
    for embed in embeds:
        fields = "".join(
            f'<div class="field {"" if field["inline"] else "wide"}"><div class="label">{html.escape(field["name"])}</div>{render_value(field["value"])}</div>'
            for field in embed["fields"]
        )
        title = html.escape(embed["title"])
        if "url" in embed:
            title = f'<a href="{html.escape(embed["url"], quote=True)}">{title}</a>'
        cards.append(
            f'<article style="--accent:#{embed["color"]:06x}"><h2>{title}</h2><p class="description">{html.escape(embed["description"])}</p><div class="fields">{fields}</div><div class="footer">{html.escape(embed["footer"]["text"])}</div></article>'
        )
    return "".join(cards)


def capture():
    OUT.mkdir(parents=True, exist_ok=True)
    data = samples()
    preview = Path("/tmp/moon-rental-discord-embeds.html")
    payloads = Path("/tmp/moon-rental-discord-embeds.json")
    payloads.write_text(json.dumps(data, indent=2))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in ["dark", "light"]:
            page = browser.new_page(viewport={"width": 1640, "height": 1400})
            for kind, embeds in data.items():
                page.set_content(
                    f'<!doctype html><html><head><meta charset="utf-8"><title>Moon Rental Discord embed previews</title><style>{STYLE}</style></head><body class="{theme}"><h1>Discord notification previews</h1><p class="intro">Generated from the actual notification payloads · Fictional data · Local preview, not a connected Discord client</p><main class="cards {kind}">{render_cards(embeds)}</main></body></html>'
                )
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(OUT / f"discord-{kind}-{theme}.png"), full_page=True)
                if kind == "payments" and theme == "dark":
                    preview.write_text(page.content())
                    assert page.locator("pre code").nth(0).inner_text() == "3000000000.01"
                    assert page.locator("pre code").nth(3).inner_text() == "2000000000.01"
            page.close()
        browser.close()
    print("Captured four Discord embed previews; exact copy fields and layout checks passed.")
    print(f"Preview: {preview}; payloads: {payloads}")


if __name__ == "__main__":
    capture()
