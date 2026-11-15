from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from moon_rental.models import BillingMode, Moon


class IskInput(forms.NumberInput):
    def __init__(self, attrs=None):
        super().__init__({"data-isk-input": "", "inputmode": "decimal", **(attrs or {})})


class TermsForm(forms.Form):
    duration_months = forms.IntegerField(min_value=1, max_value=120, initial=3)
    billing_mode = forms.ChoiceField(choices=BillingMode.choices, initial=BillingMode.MONTHLY)


class AuctionForm(TermsForm):
    title = forms.CharField(max_length=160)
    moons = forms.ModelMultipleChoiceField(
        queryset=Moon.objects.none(), widget=forms.CheckboxSelectMultiple
    )
    ends_at = forms.DateTimeField(
        label="Bidding deadline (UTC)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
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


class BidForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Your bid (ISK)",
        widget=IskInput(),
    )


class AssignmentForm(TermsForm):
    moon = forms.ModelChoiceField(queryset=Moon.objects.none())
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
