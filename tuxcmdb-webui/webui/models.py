from django.db import models


class AssetListPreference(models.Model):
    username = models.CharField(max_length=120, primary_key=True)
    selected_attributes = models.JSONField(default=list)
    page_size = models.PositiveIntegerField(default=25)
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "webui_assetlistpreference"