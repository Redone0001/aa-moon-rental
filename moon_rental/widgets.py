import json

from django import forms


class MoonSelect(forms.Widget):
    """A bounded page of choices, retaining other selections in one hidden field."""

    template_name = "moon_rental/moon_select.html"

    def __init__(self, *, multiple=False):
        super().__init__()
        self.multiple = multiple
        self.moons = []

    def value_from_datadict(self, data, files, name):
        if not self.multiple:
            return data.get(name)
        if data.get("action") == "clear_selection":
            return []
        try:
            kept = json.loads(data.get(f"{name}_kept", "[]"))
            if not isinstance(kept, list) or any(
                not str(pk).isascii() or not str(pk).isdigit() for pk in kept
            ):
                raise ValueError
            values = {str(pk) for pk in kept} | set(data.getlist(name))
            if data.get("action") == "select_page":
                values.update(str(pk) for pk in json.loads(data.get(f"{name}_page", "[]")))
            values.discard(data.get("remove_moon"))
            return sorted(values)
        except (ValueError, TypeError):
            # Let ModelMultipleChoiceField report a normal invalid-choice error.
            return ["invalid selection"]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        values = value if self.multiple else ([value] if value else [])
        selected = {str(pk) for pk in values or []}
        visible = {str(moon.pk) for moon in self.moons}
        context.update(
            starting_prices=getattr(self, "starting_prices", False),
            multiple=self.multiple,
            choices=[(moon, str(moon.pk) in selected) for moon in self.moons],
            kept=json.dumps(sorted(selected - visible)),
            visible_ids=json.dumps(sorted(visible)),
            selected_count=len(selected),
            selected_value=next(iter(selected), ""),
            selected_visible=bool(selected & visible),
        )
        return context
