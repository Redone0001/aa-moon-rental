from django import forms
from django.db.models import Q

from moon_rental.models import SurveyOre


class MoonFilters(forms.Form):
    q = forms.CharField(label="Moon or system", required=False, max_length=150)
    region = forms.ChoiceField(required=False)
    constellation = forms.ChoiceField(required=False)
    rarity = forms.ChoiceField(required=False)
    ore = forms.ChoiceField(label="Ore in survey", required=False)

    def __init__(self, data=None, *, moons):
        super().__init__(data, prefix="filter")
        for name in ("region", "constellation"):
            values = (
                moons.exclude(**{name: ""}).order_by(name).values_list(name, flat=True).distinct()
            )
            self.fields[name].choices = [("", f"All {name}s"), *((v, v) for v in values)]
        rarities = moons.order_by("rarity").values_list("rarity", flat=True).distinct()
        self.fields["rarity"].choices = [
            ("", "All rarities"),
            *((str(r), f"R{r}" if r else "Unknown") for r in rarities),
        ]
        ores = (
            SurveyOre.objects.filter(moon__in=moons)
            .order_by("name", "type_id")
            .values_list("type_id", "name")
            .distinct()
        )
        self.fields["ore"].choices = [("", "All surveyed ores"), *ores]

    def apply(self, moons):
        if not self.is_valid():
            return moons.none()
        for name in ("region", "constellation", "rarity"):
            if value := self.cleaned_data[name]:
                moons = moons.filter(**{name: value})
        if ore := self.cleaned_data["ore"]:
            moons = moons.filter(survey_ores__type_id=ore)
        if query := self.cleaned_data["q"]:
            moons = moons.filter(Q(name__icontains=query) | Q(solar_system__icontains=query))
        return moons
