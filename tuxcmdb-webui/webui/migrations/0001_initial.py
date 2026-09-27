from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AssetListPreference",
            fields=[
                ("username", models.CharField(max_length=120, primary_key=True, serialize=False)),
                ("selected_attributes", models.JSONField(default=list)),
                ("changed_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "webui_assetlistpreference",
            },
        ),
    ]