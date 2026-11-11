from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("webui", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="assetlistpreference",
            name="page_size",
            field=models.PositiveIntegerField(default=25),
        ),
    ]
