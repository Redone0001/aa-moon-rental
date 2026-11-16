from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from moon_rental.models import BillingMode, Moon
from moon_rental.services.rentals import validate_rent_start
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
        "ends_at",
        "duration_months",
        "billing_mode",
        "minimum_bid",
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
    minimum_bid = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=100000000,
        label="Minimum bid (ISK)",
        widget=IskInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["moons"].queryset = Moon.objects.filter(
            available=True, current_rental=None, reserved_lot=None
        )

    def clean(self):
        data = super().clean()
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
