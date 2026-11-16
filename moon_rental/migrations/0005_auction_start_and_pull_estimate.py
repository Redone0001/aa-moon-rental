import datetime

from django.db import migrations, models


def preserve_auction_starts(apps, schema_editor):
    Auction = apps.get_model("moon_rental", "Auction")
    database = schema_editor.connection.alias
    for auction in Auction.objects.using(database).all().iterator():
        auction.starts_on = auction.ends_at.astimezone(datetime.timezone.utc).date()
        auction.save(using=database, update_fields=["starts_on"])


class Migration(migrations.Migration):
    dependencies = [("moon_rental", "0004_moon_filters")]

    operations = [
        migrations.AddField(
            model_name="moon",
            name="pull_30d",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="auction",
            name="starts_on",
            field=models.DateField(null=True),
        ),
        migrations.RunPython(preserve_auction_starts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auction",
            name="starts_on",
            field=models.DateField(help_text="Rent starts at 00:00 UTC on this date"),
        ),
    ]
