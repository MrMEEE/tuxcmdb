from __future__ import annotations

import shlex
from typing import Any
from urllib.parse import urlencode

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from .auth import login_required
from .forms import APIUserForm, AssetCreateForm, AssetUpdateForm, AssignmentForm, AttributeForm, DatatypeForm, LoginForm
from .services import (
    ServiceError,
    api_request,
    authenticate_apiuser,
    create_apiuser,
    delete_apiuser,
    get_apiuser,
    list_audit_logs,
    list_apiusers,
    update_apiuser,
)


def notify_ui_update(entity: str, action: str, ref: str = "") -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        "tuxcmdb_updates",
        {
            "type": "ui.update",
            "entity": entity,
            "action": action,
            "ref": ref,
        },
    )


def _creds(request: HttpRequest) -> tuple[str, str]:
    username = request.session.get("api_username")
    password = request.session.get("api_password")
    if not username or not password:
        raise ServiceError("Login session expired")
    return username, password


def _param(request: HttpRequest, name: str) -> str:
    return (request.GET.get(name) or "").strip()


def _raw_param(request: HttpRequest, name: str) -> str:
    return request.GET.get(name) or ""


def _contains_text(value: Any, needle: str) -> bool:
    if not needle:
        return True
    return needle.lower() in str(value or "").lower()


def _matches_bool_filter(value: bool, expected: str) -> bool:
    if not expected:
        return True
    if expected == "yes":
        return bool(value) is True
    if expected == "no":
        return bool(value) is False
    return True


def _matches_exact_text(value: Any, expected: str) -> bool:
    if not expected:
        return True
    return str(value or "").lower() == expected.lower()


def _parse_bool_text(value: str) -> bool | None:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "active"}:
        return True
    if normalized in {"0", "false", "no", "off", "inactive", "decommissioned"}:
        return False
    return None


def _asset_filter_terms(query: str) -> list[tuple[str, str | None]]:
    if not query:
        return []
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()

    terms: list[tuple[str, str | None]] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            terms.append((key.strip().lower(), value.strip()))
        else:
            terms.append((token.lower(), None))
    return terms


def _asset_matches_logic_query(item: dict[str, Any], query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    assetname = str(item.get("assetname") or "")
    attribute_rows = item.get("attributes", [])
    active_value = bool(item.get("active"))

    for key, value in terms:
        if value is None:
            if key in {"active", "inactive", "decommissioned"}:
                if key == "active" and not active_value:
                    return False
                if key in {"inactive", "decommissioned"} and active_value:
                    return False
                continue

            has_attribute = any(str(attribute.get("name") or "").lower() == key for attribute in attribute_rows)
            if has_attribute:
                continue
            if _contains_text(assetname, key):
                continue
            if any(_contains_text(attribute.get("value"), key) for attribute in attribute_rows):
                continue
            return False

        if key in {"active", "status"}:
            expected = _parse_bool_text(value)
            if expected is None or active_value is not expected:
                return False
            continue

        if key in {"asset", "assetname", "name"}:
            if not _contains_text(assetname, value):
                return False
            continue

        matching_attributes = [attribute for attribute in attribute_rows if str(attribute.get("name") or "").lower() == key]
        if not matching_attributes:
            return False
        if value and not any(_matches_exact_text(attribute.get("value"), value) for attribute in matching_attributes):
            return False

    return True


def _asset_api_params(filter_query: str) -> dict[str, str] | None:
    params: dict[str, str] = {}
    active_filter: bool | None = True
    for key, value in _asset_filter_terms(filter_query):
        if value is None:
            if key in {"inactive", "decommissioned"}:
                active_filter = False
            continue
        if key in {"active", "status"}:
            parsed = _parse_bool_text(value)
            if parsed is not None:
                active_filter = parsed

    if active_filter is not None:
        params["active"] = "true" if active_filter else "false"
    return params or None


def _matches_logic_text_fields(fields: list[Any], key: str, value: str | None = None) -> bool:
    if value is None:
        return any(_contains_text(field, key) for field in fields)
    return any(_contains_text(field, value) for field in fields)


def _attribute_matches_logic_query(item: dict[str, Any], query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    name = item.get("name")
    data_type = item.get("data_type")
    description = item.get("description")
    allow_multiple = bool(item.get("allow_multiple"))
    searchable = [name, data_type, description, "yes" if allow_multiple else "no"]

    for key, value in terms:
        if value is None:
            if key == "allow_multiple":
                if not allow_multiple:
                    return False
                continue
            if key in {"single", "allow_multiple=false", "multiple=false"}:
                if allow_multiple:
                    return False
                continue
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"name", "attribute", "attribute_name"}:
            if not _contains_text(name, value):
                return False
            continue
        if key in {"datatype", "data_type", "type"}:
            if not _matches_exact_text(data_type, value):
                return False
            continue
        if key in {"description", "desc"}:
            if not _contains_text(description, value):
                return False
            continue
        if key in {"allow_multiple", "multiple"}:
            parsed = _parse_bool_text(value)
            if parsed is None or allow_multiple is not parsed:
                return False
            continue
        return False

    return True


def _datatype_matches_logic_query(item: dict[str, Any], query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    name = item.get("name")
    builtin = item.get("builtin_validator")
    regex = item.get("regex_pattern")
    description = item.get("description")
    searchable = [name, builtin, regex, description]

    for key, value in terms:
        if value is None:
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"name", "datatype", "type"}:
            if not _contains_text(name, value):
                return False
            continue
        if key in {"builtin", "builtin_validator", "validator"}:
            if not _contains_text(builtin, value):
                return False
            continue
        if key in {"regex", "regex_pattern", "pattern"}:
            if not _contains_text(regex, value):
                return False
            continue
        if key in {"description", "desc"}:
            if not _contains_text(description, value):
                return False
            continue
        return False

    return True


def _apiuser_matches_logic_query(item: Any, query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    searchable = [
        item.username,
        item.name or "",
        item.description or "",
        "yes" if item.is_active else "no",
        "yes" if item.readonly else "no",
        item.changed_at,
    ]

    for key, value in terms:
        if value is None:
            if key == "active":
                if not item.is_active:
                    return False
                continue
            if key in {"inactive", "disabled"}:
                if item.is_active:
                    return False
                continue
            if key == "readonly":
                if not item.readonly:
                    return False
                continue
            if key in {"write", "writable", "readonly=false"}:
                if item.readonly:
                    return False
                continue
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"username", "user"}:
            if not _contains_text(item.username, value):
                return False
            continue
        if key == "name":
            if not _contains_text(item.name, value):
                return False
            continue
        if key in {"description", "desc"}:
            if not _contains_text(item.description, value):
                return False
            continue
        if key in {"active", "is_active", "status"}:
            parsed = _parse_bool_text(value)
            if parsed is None or item.is_active is not parsed:
                return False
            continue
        if key == "readonly":
            parsed = _parse_bool_text(value)
            if parsed is None or item.readonly is not parsed:
                return False
            continue
        if key in {"changed", "changed_at", "updated"}:
            if not _contains_text(item.changed_at, value):
                return False
            continue
        return False

    return True


def _audit_matches_logic_query(item: Any, query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    searchable = [item.actor_username, item.entity_type, item.entity_ref, item.action, item.details or "", item.created_at]

    for key, value in terms:
        if value is None:
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"actor", "actor_username", "user"}:
            if not _contains_text(item.actor_username, value):
                return False
            continue
        if key in {"entity", "entity_type", "type"}:
            if not _contains_text(item.entity_type, value):
                return False
            continue
        if key in {"ref", "entity_ref", "reference"}:
            if not _contains_text(item.entity_ref, value):
                return False
            continue
        if key == "action":
            if not _contains_text(item.action, value):
                return False
            continue
        if key in {"details", "detail"}:
            if not _contains_text(item.details, value):
                return False
            continue
        if key in {"when", "created", "created_at", "time"}:
            if not _contains_text(item.created_at, value):
                return False
            continue
        return False

    return True


def _sort_direction(request: HttpRequest) -> str:
    sort_dir = _param(request, "sort_dir").lower()
    return sort_dir if sort_dir in {"asc", "desc"} else "desc"


def _sort_items(items: list[Any], sort_by: str, sort_dir: str, key_map: dict[str, Any]) -> list[Any]:
    key_func = key_map.get(sort_by)
    if key_func is None:
        return items
    reverse = sort_dir == "desc"
    return sorted(items, key=lambda item: key_func(item), reverse=reverse)


def _current_actor(request: HttpRequest) -> str:
    return getattr(request.user, "username", "system") or "system"


def _query_string(params: dict[str, Any]) -> str:
    cleaned = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        cleaned[key] = value
    return urlencode(cleaned)


def _sort_link_data(base_params: dict[str, Any], current_sort_by: str, current_sort_dir: str, fields: list[str]) -> dict[str, dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    for field in fields:
        next_dir = "asc" if current_sort_by == field and current_sort_dir == "desc" else "desc"
        params = dict(base_params)
        params["sort_by"] = field
        params["sort_dir"] = next_dir
        links[field] = {
            "query": _query_string(params),
            "field": field,
            "next_dir": next_dir,
            "indicator": "↓" if current_sort_by == field and current_sort_dir == "desc" else "↑" if current_sort_by == field else "",
        }
    return links


def home(request: HttpRequest) -> HttpResponse:
    return redirect("assets") if getattr(request.user, "is_authenticated", False) else redirect("login")


def login_view(request: HttpRequest) -> HttpResponse:
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = authenticate_apiuser(form.cleaned_data["username"], form.cleaned_data["password"])
        if result is None:
            messages.error(request, "Invalid credentials")
        else:
            request.session["api_username"] = result["username"]
            request.session["api_password"] = form.cleaned_data["password"]
            request.session["api_readonly"] = result["readonly"]
            return redirect("assets")
    return render(request, "webui/login.html", {"form": form})


def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect("login")


@login_required
def assets_view(request: HttpRequest) -> HttpResponse:
    filter_query = _raw_param(request, "q") or _param(request, "assetname") or _param(request, "attribute_name")
    sort_by = _param(request, "sort_by") or "assetname"
    sort_dir = _sort_direction(request)
    create_form = AssetCreateForm(request.POST or None)
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("assets")
        if create_form.is_valid():
            try:
                api_request(*_creds(request), "POST", "/v1/assets", payload={"assetname": create_form.cleaned_data["assetname"]})
                messages.success(request, "Asset created")
                notify_ui_update("assets", "created", create_form.cleaned_data["assetname"])
                return redirect("assets")
            except ServiceError as exc:
                messages.error(request, str(exc))

    assets: list[dict[str, Any]] = []
    try:
        assets = api_request(*_creds(request), "GET", "/v1/assets", params=_asset_api_params(filter_query))
    except ServiceError as exc:
        messages.error(request, str(exc))

    filtered_assets: list[dict[str, Any]] = []
    for item in assets:
        if not _asset_matches_logic_query(item, filter_query):
            continue
        filtered_assets.append(item)

    assets = filtered_assets
    assets = _sort_items(
        assets,
        sort_by,
        sort_dir,
        {
            "assetname": lambda item: str(item.get("assetname") or "").lower(),
            "active": lambda item: 1 if item.get("active") else 0,
            "attributes_count": lambda item: len(item.get("attributes", [])),
        },
    )

    active_count = sum(1 for item in assets if item.get("active"))
    decommissioned_count = sum(1 for item in assets if not item.get("active"))
    base_params = {
        "q": filter_query,
    }
    return render(
        request,
        "webui/assets_list.html",
        {
            "assets": assets,
            "create_form": create_form,
            "active_count": active_count,
            "decommissioned_count": decommissioned_count,
            "filters": {
                "q": filter_query,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data(base_params, sort_by, sort_dir, ["assetname", "active", "attributes_count"]),
        },
    )


@login_required
def asset_detail_view(request: HttpRequest, asset_ref: str) -> HttpResponse:
    assignment_form = AssignmentForm()
    update_form = AssetUpdateForm()
    asset: dict[str, Any] | None = None
    attributes: list[dict[str, Any]] = []

    try:
        assets = api_request(*_creds(request), "GET", "/v1/assets", params={"active": "true", "q": asset_ref})
        exact = next((item for item in assets if item["assetname"] == asset_ref or str(item["id"]) == asset_ref), None)
        asset = exact or (assets[0] if assets else None)
        if asset is None:
            raise ServiceError("Asset not found")
        update_form = AssetUpdateForm(initial={"assetname": asset["assetname"]})
        attributes = api_request(*_creds(request), "GET", "/v1/attributes")
        attribute_choices = [(item["name"], item["name"]) for item in attributes if item.get("name")]
        assignment_form = AssignmentForm(attribute_choices=attribute_choices)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("assets")

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("asset-detail", asset_ref=asset_ref)

        action = request.POST.get("action")
        try:
            if action == "assign":
                assignment_form = AssignmentForm(request.POST, attribute_choices=attribute_choices)
                if assignment_form.is_valid():
                    payload = {
                        assignment_form.cleaned_data["attribute_name"]: assignment_form.cleaned_data["value"]
                    }
                    api_request(*_creds(request), "POST", f"/v1/assets/{asset['id']}/attributes", payload=payload)
                    messages.success(request, "Assignment added")
                    notify_ui_update("assignments", "created", asset["assetname"])
                    return redirect("asset-detail", asset_ref=asset["assetname"])
            elif action == "remove":
                attribute_name = request.POST.get("attribute_name", "")
                value = request.POST.get("value") or None
                api_request(
                    *_creds(request),
                    "DELETE",
                    f"/v1/assets/{asset['id']}/attributes/{attribute_name}",
                    params={"value": value} if value else None,
                )
                messages.success(request, "Assignment removed")
                notify_ui_update("assignments", "removed", asset["assetname"])
                return redirect("asset-detail", asset_ref=asset["assetname"])
            elif action == "update-asset":
                update_form = AssetUpdateForm(request.POST)
                if update_form.is_valid():
                    api_request(*_creds(request), "PATCH", f"/v1/assets/{asset['id']}", payload=update_form.cleaned_data)
                    messages.success(request, "Asset updated")
                    notify_ui_update("assets", "updated", update_form.cleaned_data["assetname"])
                    return redirect("asset-detail", asset_ref=update_form.cleaned_data["assetname"])
            elif action == "decommission":
                api_request(*_creds(request), "POST", f"/v1/assets/{asset['id']}/decommission")
                messages.success(request, "Asset decommissioned")
                notify_ui_update("assets", "decommissioned", asset["assetname"])
                return redirect("assets")
        except ServiceError as exc:
            messages.error(request, str(exc))

    try:
        asset = api_request(*_creds(request), "GET", f"/v1/assets/{asset['id']}")
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("assets")

    return render(
        request,
        "webui/asset_detail.html",
        {
            "asset": asset,
            "attributes": attributes,
            "assignment_form": assignment_form,
            "update_form": update_form,
        },
    )


@login_required
def asset_attribute_history_view(request: HttpRequest, asset_ref: str, attribute_ref: str) -> JsonResponse:
    try:
        history_entries = api_request(
            *_creds(request),
            "GET",
            f"/v1/assets/{asset_ref}/attributes/{attribute_ref}/history",
        )
        return JsonResponse({"attribute": attribute_ref, "history": history_entries})
    except ServiceError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@login_required
def attributes_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "name") or _param(request, "data_type")
    sort_by = _param(request, "sort_by") or "name"
    sort_dir = _sort_direction(request)
    datatypes: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass

    datatype_choices = [(item["name"], item["name"]) for item in datatypes if item.get("name")]
    create_form = AttributeForm(
        request.POST if request.method == "POST" else None,
        datatype_choices=datatype_choices,
    )
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("attributes")
        if create_form.is_valid():
            try:
                api_request(*_creds(request), "POST", "/v1/attributes", payload=create_form.cleaned_data)
                messages.success(request, "Attribute created")
                notify_ui_update("attributes", "created", create_form.cleaned_data["name"])
                return redirect("attributes")
            except ServiceError as exc:
                messages.error(request, str(exc))

    items: list[dict[str, Any]] = []
    try:
        items = api_request(*_creds(request), "GET", "/v1/attributes")
    except ServiceError as exc:
        messages.error(request, str(exc))

    filtered_items: list[dict[str, Any]] = []
    for item in items:
        if not _attribute_matches_logic_query(item, search):
            continue
        filtered_items.append(item)

    filtered_items = _sort_items(
        filtered_items,
        sort_by,
        sort_dir,
        {
            "name": lambda item: str(item.get("name") or "").lower(),
            "data_type": lambda item: str(item.get("data_type") or "").lower(),
            "allow_multiple": lambda item: 1 if item.get("allow_multiple") else 0,
            "description": lambda item: str(item.get("description") or "").lower(),
        },
    )

    return render(
        request,
        "webui/attributes_list.html",
        {
            "attributes": filtered_items,
            "create_form": create_form,
            "datatypes": datatypes,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["name", "data_type", "allow_multiple", "description"]),
        },
    )


@login_required
def attribute_form_view(request: HttpRequest, attribute_id: int | None = None) -> HttpResponse:
    datatypes: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass
    datatype_choices = [(item["name"], item["name"]) for item in datatypes if item.get("name")]

    attribute = None
    initial: dict[str, Any] = {}
    if attribute_id is not None:
        try:
            attributes = api_request(*_creds(request), "GET", "/v1/attributes")
            attribute = next((item for item in attributes if item["id"] == attribute_id), None)
            if attribute is None:
                messages.error(request, "Attribute not found")
                return redirect("attributes")
            initial = {
                "name": attribute["name"],
                "data_type": attribute["data_type"],
                "description": attribute.get("description") or "",
                "allow_multiple": attribute.get("allow_multiple", False),
            }
        except ServiceError as exc:
            messages.error(request, str(exc))
            return redirect("attributes")

    form = AttributeForm(request.POST or None, initial=initial, datatype_choices=datatype_choices)
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("attributes")
        if form.is_valid():
            try:
                if attribute_id is None:
                    api_request(*_creds(request), "POST", "/v1/attributes", payload=form.cleaned_data)
                    messages.success(request, "Attribute created")
                    notify_ui_update("attributes", "created", form.cleaned_data["name"])
                else:
                    api_request(*_creds(request), "PATCH", f"/v1/attributes/{attribute_id}", payload=form.cleaned_data)
                    messages.success(request, "Attribute updated")
                    notify_ui_update("attributes", "updated", form.cleaned_data["name"])
                return redirect("attributes")
            except ServiceError as exc:
                messages.error(request, str(exc))

    return render(request, "webui/attribute_form.html", {"form": form, "attribute": attribute, "datatypes": datatypes})


@login_required
def attribute_delete_view(request: HttpRequest, attribute_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("attributes")
    if request.user.readonly:
        messages.error(request, "This user has readonly access.")
        return redirect("attributes")
    try:
        api_request(*_creds(request), "DELETE", f"/v1/attributes/{attribute_id}")
        messages.success(request, "Attribute deleted")
        notify_ui_update("attributes", "deleted", str(attribute_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("attributes")


@login_required
def datatypes_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "name") or _param(request, "builtin_validator")
    sort_by = _param(request, "sort_by") or "name"
    sort_dir = _sort_direction(request)
    create_form = DatatypeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("datatypes")
        if create_form.is_valid():
            try:
                payload = {k: v or None for k, v in create_form.cleaned_data.items()}
                api_request(*_creds(request), "POST", "/v1/datatypes", payload=payload)
                messages.success(request, "Datatype created")
                notify_ui_update("datatypes", "created", create_form.cleaned_data["name"])
                return redirect("datatypes")
            except ServiceError as exc:
                messages.error(request, str(exc))

    datatypes: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError as exc:
        messages.error(request, str(exc))

    filtered_datatypes: list[dict[str, Any]] = []
    for item in datatypes:
        if not _datatype_matches_logic_query(item, search):
            continue
        filtered_datatypes.append(item)

    filtered_datatypes = _sort_items(
        filtered_datatypes,
        sort_by,
        sort_dir,
        {
            "name": lambda item: str(item.get("name") or "").lower(),
            "builtin_validator": lambda item: str(item.get("builtin_validator") or "").lower(),
            "regex_pattern": lambda item: str(item.get("regex_pattern") or "").lower(),
            "description": lambda item: str(item.get("description") or "").lower(),
        },
    )

    return render(
        request,
        "webui/datatypes_list.html",
        {
            "datatypes": filtered_datatypes,
            "create_form": create_form,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["name", "builtin_validator", "regex_pattern", "description"]),
        },
    )


@login_required
def apiusers_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "username") or _param(request, "name")
    sort_by = _param(request, "sort_by") or "username"
    sort_dir = _sort_direction(request)

    apiusers = []
    for item in list_apiusers():
        if not _apiuser_matches_logic_query(item, search):
            continue
        apiusers.append(item)

    apiusers = _sort_items(
        apiusers,
        sort_by,
        sort_dir,
        {
            "username": lambda item: str(item.username or "").lower(),
            "name": lambda item: str(item.name or "").lower(),
            "description": lambda item: str(item.description or "").lower(),
            "is_active": lambda item: 1 if item.is_active else 0,
            "readonly": lambda item: 1 if item.readonly else 0,
            "changed_at": lambda item: str(item.changed_at or ""),
        },
    )

    return render(
        request,
        "webui/apiusers_list.html",
        {
            "apiusers": apiusers,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["username", "name", "description", "is_active", "readonly", "changed_at"]),
        },
    )


@login_required
def audit_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "actor_username") or _param(request, "entity_type")
    sort_by = _param(request, "sort_by") or "created_at"
    sort_dir = _sort_direction(request)

    audit_logs = []
    for item in list_audit_logs():
        if not _audit_matches_logic_query(item, search):
            continue
        audit_logs.append(item)

    audit_logs = _sort_items(
        audit_logs,
        sort_by,
        sort_dir,
        {
            "created_at": lambda item: str(item.created_at or ""),
            "actor_username": lambda item: str(item.actor_username or "").lower(),
            "entity_type": lambda item: str(item.entity_type or "").lower(),
            "entity_ref": lambda item: str(item.entity_ref or "").lower(),
            "action": lambda item: str(item.action or "").lower(),
            "details": lambda item: str(item.details or "").lower(),
        },
    )

    return render(
        request,
        "webui/audit_list.html",
        {
            "audit_logs": audit_logs,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["created_at", "actor_username", "entity_type", "entity_ref", "action", "details"]),
        },
    )


@login_required
def apiuser_form_view(request: HttpRequest, user_id: int | None = None) -> HttpResponse:
    user_record = get_apiuser(user_id) if user_id is not None else None
    if user_id is not None and user_record is None:
        messages.error(request, "API user not found")
        return redirect("apiusers")

    initial = {}
    if user_record is not None:
        initial = {
            "username": user_record.username,
            "name": user_record.name or "",
            "description": user_record.description or "",
            "is_active": user_record.is_active,
            "readonly": user_record.readonly,
        }
    form = APIUserForm(request.POST or None, initial=initial, require_password=user_id is None)

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("apiusers")
        if form.is_valid():
            try:
                if user_id is None:
                    create_apiuser(
                        _current_actor(request),
                        form.cleaned_data["username"],
                        form.cleaned_data["password"],
                        form.cleaned_data["is_active"],
                        form.cleaned_data["readonly"],
                        form.cleaned_data["name"] or None,
                        form.cleaned_data["description"] or None,
                    )
                    messages.success(request, "API user created")
                    notify_ui_update("apiusers", "created", form.cleaned_data["username"])
                else:
                    update_apiuser(
                        _current_actor(request),
                        user_id,
                        form.cleaned_data["username"],
                        form.cleaned_data["password"] or None,
                        form.cleaned_data["is_active"],
                        form.cleaned_data["readonly"],
                        form.cleaned_data["name"] or None,
                        form.cleaned_data["description"] or None,
                    )
                    messages.success(request, "API user updated")
                    notify_ui_update("apiusers", "updated", form.cleaned_data["username"])
                return redirect("apiusers")
            except ServiceError as exc:
                messages.error(request, str(exc))

    return render(request, "webui/apiuser_form.html", {"form": form, "user_record": user_record})


@login_required
def apiuser_delete_view(request: HttpRequest, user_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("apiusers")
    if request.user.readonly:
        messages.error(request, "This user has readonly access.")
        return redirect("apiusers")
    try:
        delete_apiuser(_current_actor(request), user_id)
        messages.success(request, "API user deleted")
        notify_ui_update("apiusers", "deleted", str(user_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("apiusers")
