from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("assets/", views.assets_view, name="assets"),
    path("assets/<str:asset_ref>/", views.asset_detail_view, name="asset-detail"),
    path(
        "assets/<str:asset_ref>/attributes/<str:attribute_ref>/history/",
        views.asset_attribute_history_view,
        name="asset-attribute-history",
    ),
    path("attributes/", views.attributes_view, name="attributes"),
    path("attributes/new/", views.attribute_form_view, name="attribute-create"),
    path("attributes/<int:attribute_id>/edit/", views.attribute_form_view, name="attribute-edit"),
    path("attributes/<int:attribute_id>/delete/", views.attribute_delete_view, name="attribute-delete"),
    path("datatypes/", views.datatypes_view, name="datatypes"),
    path("apiusers/", views.apiusers_view, name="apiusers"),
    path("apiusers/new/", views.apiuser_form_view, name="apiuser-create"),
    path("apiusers/<int:user_id>/edit/", views.apiuser_form_view, name="apiuser-edit"),
    path("apiusers/<int:user_id>/delete/", views.apiuser_delete_view, name="apiuser-delete"),
    path("audit/", views.audit_view, name="audit"),
]
