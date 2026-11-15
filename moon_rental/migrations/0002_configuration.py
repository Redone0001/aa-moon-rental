from django.db import migrations


def seed_configuration(apps, schema_editor):
    apps.get_model("moon_rental", "Configuration").objects.using(
        schema_editor.connection.alias
    ).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("moon_rental", "0001_initial")]
    operations = [migrations.RunPython(seed_configuration, migrations.RunPython.noop)]
