from django.urls import include, path

urlpatterns = [
    path("", include("webui.urls")),
]
