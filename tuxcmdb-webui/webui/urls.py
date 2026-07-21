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
    path(
        "assets/<str:asset_ref>/attributes/<str:attribute_ref>/restore/",
        views.asset_attribute_restore_view,
        name="asset-attribute-restore",
    ),
    path("attributes/", views.attributes_view, name="attributes"),
    path("attributes/new/", views.attribute_form_view, name="attribute-create"),
    path("attributes/<int:attribute_id>/edit/", views.attribute_form_view, name="attribute-edit"),
    path("attributes/<int:attribute_id>/delete/", views.attribute_delete_view, name="attribute-delete"),
    path("datatypes/", views.datatypes_view, name="datatypes"),
    path("operatingsystems/", views.operatingsystems_view, name="operatingsystems"),
    path("operatingsystems/new/", views.operatingsystem_form_view, name="operatingsystem-create"),
    path(
        "operatingsystems/<int:operatingsystem_id>/edit/",
        views.operatingsystem_form_view,
        name="operatingsystem-edit",
    ),
    path(
        "operatingsystems/<int:operatingsystem_id>/delete/",
        views.operatingsystem_delete_view,
        name="operatingsystem-delete",
    ),
    path("apiusers/", views.apiusers_view, name="apiusers"),
    path("apiusers/new/", views.apiuser_form_view, name="apiuser-create"),
    path("apiusers/<int:user_id>/edit/", views.apiuser_form_view, name="apiuser-edit"),
    path("apiusers/<int:user_id>/delete/", views.apiuser_delete_view, name="apiuser-delete"),
    path("apiusers/ldap-sources/", views.ldap_sources_view, name="ldap-sources"),
    path("apiusers/ldap-sources/new/", views.ldap_source_form_view, name="ldap-source-create"),
    path("apiusers/ldap-sources/<int:source_id>/edit/", views.ldap_source_form_view, name="ldap-source-edit"),
    path("apiusers/ldap-sources/<int:source_id>/delete/", views.ldap_source_delete_view, name="ldap-source-delete"),
    path("apiusers/ldap-users/", views.ldap_users_view, name="ldap-users"),
    path("apiusers/ldap-group-mappings/", views.ldap_group_mappings_view, name="ldap-group-mappings"),
    path("docs/", views.docs_view, name="docs"),
    path("audit/", views.audit_view, name="audit"),
]
