import csv
import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from moon_rental.models import Moon
from moon_rental.services.rentals import assign_moon

pytestmark = pytest.mark.django_db


def read_export(response):
    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert content.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


@pytest.mark.parametrize("user_fixture", ["manager", "admin_user"])
def test_catalogue_users_can_download_csv(client, request, moon, user_fixture):
    client.force_login(request.getfixturevalue(user_fixture))
    response = client.get(reverse("moon_rental:moon_export"))
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == 'attachment; filename="moon-catalogue.csv"'
    assert read_export(response)[0]["Moon ID"] == str(moon.pk)


def test_export_requires_catalogue_access(client, moon):
    url = reverse("moon_rental:moon_export")
    assert client.get(url).status_code == 302
    client.force_login(User.objects.create_user("outsider"))
    assert client.get(url).status_code == 403


def test_csv_preserves_unicode_quotes_precision_and_utc(client, manager, moon):
    moon.name = 'Étoile, "Alpha"\nIV - 12'
    moon.estimated_value = Decimal("123456789012.34")
    moon.composition = [{"type_id": 45506, "name": 'Ore, "rare"', "percent": 35.1234}]
    moon.save()
    client.force_login(manager)
    with override("fr"), timezone.override("Europe/Brussels"):
        rows = read_export(client.get(reverse("moon_rental:moon_export")))
    assert rows == [
        {
            "Moon ID": str(moon.pk),
            "Moon": moon.name,
            "Solar system": moon.solar_system,
            "Region": moon.region,
            "Constellation": moon.constellation,
            "Rarity": "64",
            "Survey composition": 'Ore, "rare": 35.1234%',
            "Estimated monthly value (ISK)": "123456789012.34",
            "Status": "Available",
            "Last synced (UTC)": moon.synced_at.isoformat(),
        }
    ]


def test_export_includes_every_page_and_unknown_survey_values(client, manager, moon):
    Moon.objects.bulk_create(
        [Moon(id=i, name=f"Extra {i:02}", synced_at=timezone.now()) for i in range(100, 145)]
    )
    client.force_login(manager)
    page = client.get(reverse("moon_rental:moons"))
    assert len(page.context["moons"]) == 40
    rows = read_export(client.get(reverse("moon_rental:moon_export"), {"page": "2"}))
    assert len(rows) == 46
    assert rows[0]["Survey composition"] == ""
    assert rows[0]["Estimated monthly value (ISK)"] == ""
    assert [row["Moon"] for row in rows] == list(
        Moon.objects.order_by("name", "pk").values_list("name", flat=True)
    )


@pytest.mark.parametrize("query", ["J-A5QD IV", "j-a5qd", "  Vale of the Silent  ", "no match"])
def test_export_matches_catalogue_search(client, manager, moon, query):
    Moon.objects.create(id=2, name="Other moon", region="Tribute", synced_at=timezone.now())
    client.force_login(manager)
    data = {"q": query}
    page = client.get(reverse("moon_rental:moons"), data)
    rows = read_export(client.get(reverse("moon_rental:moon_export"), data))
    assert [row["Moon ID"] for row in rows] == [str(m.pk) for m in page.context["moons"]]


def test_empty_export_still_contains_column_headers(client, manager):
    client.force_login(manager)
    response = client.get(reverse("moon_rental:moon_export"))
    content = b"".join(response.streaming_content).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(content)))
    assert len(rows) == 1
    assert rows[0][0] == "Moon ID"
    assert rows[0][-1] == "Last synced (UTC)"


def test_export_status_matches_catalogue(client, pilot, manager, wallet, auction, moon):
    rented = Moon.objects.create(id=2, name="Rented moon", synced_at=timezone.now())
    assign_moon(actor=manager, renter=pilot, moon_id=rented.pk, price="100", duration_months=1)
    Moon.objects.create(id=3, name="Removed moon", available=False, synced_at=timezone.now())
    Moon.objects.create(id=4, name="Free moon", synced_at=timezone.now())
    # Existing leases and auction reservations take precedence over source availability.
    Moon.objects.filter(pk__in=[moon.pk, rented.pk]).update(available=False)
    client.force_login(manager)
    rows = read_export(client.get(reverse("moon_rental:moon_export")))
    assert {row["Moon ID"]: row["Status"] for row in rows} == {
        str(moon.pk): "In auction",
        "2": "Rented",
        "3": "Unavailable",
        "4": "Available",
    }


@pytest.mark.parametrize(
    "text", ["=1+1", "+1+1", "-1+1", "@SUM(1)", "  =1", "\tname", "\rname", "\nname"]
)
def test_formula_like_text_is_escaped(client, manager, moon, text):
    moon.name = text
    moon.solar_system = text
    moon.region = text
    moon.constellation = text
    moon.composition = [{"type_id": 45506, "name": text, "percent": 35}]
    moon.save()
    client.force_login(manager)
    row = read_export(client.get(reverse("moon_rental:moon_export")))[0]
    for column in ("Moon", "Solar system", "Region", "Constellation"):
        assert row[column] == "'" + text
    assert row["Survey composition"] == f"'{text}: 35%"


def test_export_link_preserves_search_without_page(client, manager, moon):
    client.force_login(manager)
    response = client.get(reverse("moon_rental:moons"), {"q": "J-A5QD & +", "page": "2"})
    url = reverse("moon_rental:moon_export") + "?q=J-A5QD%20%26%20%2B"
    assert f'href="{url}"'.encode() in response.content
    assert b"Export CSV" in response.content
