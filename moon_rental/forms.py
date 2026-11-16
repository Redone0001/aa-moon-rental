import json
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from moon_rental.models import BillingMode, Moon
from moon_rental.services.rentals import isk, validate_rent_start
from moon_rental.widgets import MoonSelect


class IskInput(forms.NumberInput):
    def __init__(self, attrs=None):
        super().__init__({"data-isk-input": "", "inputmode": "decimal", **(attrs or {})})


class TermsForm(forms.Form):
    duration_months = forms.IntegerField(min_value=1, max_value=120, initial=3)
    billing_mode = forms.ChoiceField(choices=BillingMode.choices, initial=BillingMode.MONTHLY)


class AuctionForm(TermsForm):
    field_order = [
        "title",
        "moons",
        "starts_on",
        "bidding_starts_at",
        "ends_at",
        "duration_months",
        "billing_mode",
        "minimum_bid",
        "reminder_hours",
        "starting_prices",
    ]
    title = forms.CharField(max_length=160)
    moons = forms.ModelMultipleChoiceField(
        queryset=Moon.objects.none(), widget=MoonSelect(multiple=True)
    )
    starts_on = forms.DateField(
        label="Start rent (UTC)",
        input_formats=["%d/%m/%Y", "%Y-%m-%d"],
        widget=forms.DateInput(format="%d/%m/%Y", attrs={"placeholder": "dd/mm/yyyy"}),
        help_text="Rent starts at 00:00 UTC. Choose a date after bidding ends to allow in-game setup.",
    )
    ends_at = forms.DateTimeField(
        label="Bid end (UTC)",
        input_formats=["%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%d/%m/%Y %H:%M", attrs={"placeholder": "dd/mm/yyyy hh:mm"}
        ),
    )
    bidding_starts_at = forms.DateTimeField(
        label="Bid start (UTC)",
        required=False,
        input_formats=["%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%d/%m/%Y %H:%M", attrs={"placeholder": "dd/mm/yyyy hh:mm"}
        ),
        help_text="Leave blank to open now. Bids are accepted automatically between start and end.",
    )
    reminder_hours = forms.TypedMultipleChoiceField(
        choices=[(h, f"{h}h remaining") for h in [24, 12, 6, 1]],
        coerce=int,
        required=False,
        initial=[24, 12, 6, 1],
        widget=forms.CheckboxSelectMultiple,
        help_text="Public reminders use the webhook configured in Django admin.",
    )
    starting_prices = forms.JSONField(required=False, initial=dict, widget=forms.HiddenInput)
    minimum_bid = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=100000000,
        label="Default starting price (ISK)",
        widget=IskInput(),
        help_text="Used for moons without an individual starting price. All starting prices are public.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            data = self.data.copy()
            try:
                prices = json.loads(data.get("starting_prices") or "{}")
                if prices is None:
                    prices = {}
                if not isinstance(prices, dict):
                    raise ValueError
                for name in data:
                    if name.startswith("starting_price_"):
                        pk = name.removeprefix("starting_price_")
                        if data[name].strip():
                            prices[pk] = data[name].strip()
                        else:
                            prices.pop(pk, None)
                data["starting_prices"] = json.dumps(prices)
                self.data = data
            except (ValueError, TypeError):
                pass  # JSONField supplies the validation error.
        self.fields["moons"].queryset = Moon.objects.filter(
            available=True, current_rental=None, reserved_lot=None
        )

    def clean(self):
        data = super().clean()
        if data.get("bidding_starts_at") and data.get("ends_at"):
            if data["bidding_starts_at"] >= data["ends_at"]:
                self.add_error("bidding_starts_at", "Bidding must start before the deadline.")
        prices = data.get("starting_prices") or {}
        if not isinstance(prices, dict):
            self.add_error("starting_prices", "Invalid starting prices.")
        elif data.get("moons") is not None:
            selected = {str(m.pk) for m in data["moons"]}
            try:
                data["starting_prices"] = {
                    pk: isk(value) for pk, value in prices.items() if pk in selected
                }
            except ValidationError as exc:
                self.add_error(None, exc)
        if data.get("starts_on") and data.get("ends_at"):
            try:
                validate_rent_start(data["starts_on"], data["ends_at"])
            except ValidationError as exc:
                self.add_error("starts_on", exc)
        return data


class BidForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Your bid (ISK)",
        widget=IskInput(),
    )


class AssignmentForm(TermsForm):
    moon = forms.ModelChoiceField(queryset=Moon.objects.none(), widget=MoonSelect())
    renter = forms.ModelChoiceField(
        queryset=get_user_model().objects.filter(is_active=True).order_by("username")
    )
    price = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Rent (ISK)",
        widget=IskInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["moon"].queryset = Moon.objects.filter(
            available=True, current_rental=None, reserved_lot=None
        )


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 2}))
