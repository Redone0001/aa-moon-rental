import csv
from datetime import timezone

from django.http import StreamingHttpResponse


class CSVBuffer:
    def write(self, value):
        return value


def csv_cell(value):
    """Keep catalogue text from being interpreted as a spreadsheet formula."""
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


def moon_catalogue_csv(moons):
    def rows():
        # A UTF-8 BOM lets spreadsheet applications recognize non-ASCII survey names.
        yield "\ufeff"
        writer = csv.writer(CSVBuffer())
        yield writer.writerow(
            [
                "Moon ID",
                "Moon",
                "Solar system",
                "Region",
                "Constellation",
                "Rarity",
                "Survey composition",
                "Estimated monthly value (ISK)",
                "Status",
                "Last synced (UTC)",
            ]
        )
        for moon in moons.iterator(chunk_size=2000):
            if moon.current_rental_id:
                status = "Rented"
            elif moon.reserved_lot_id:
                status = "In auction"
            elif not moon.available:
                status = "Unavailable"
            else:
                status = "Available"
            yield writer.writerow(
                map(
                    csv_cell,
                    [
                        moon.pk,
                        moon.name,
                        moon.solar_system,
                        moon.region,
                        moon.constellation,
                        moon.rarity,
                        "; ".join(
                            f"{product['name']}: {product['percent']}%"
                            for product in moon.composition
                        ),
                        moon.estimated_value,
                        status,
                        moon.synced_at.astimezone(timezone.utc).isoformat(),
                    ],
                )
            )

    return StreamingHttpResponse(
        rows(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="moon-catalogue.csv"'},
    )
