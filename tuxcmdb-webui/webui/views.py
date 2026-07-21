from __future__ import annotations

import shlex
import json
import re
from typing import Any
from urllib.parse import urlencode

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from .auth import login_required
from .forms import (
    APIUserForm,
    AssetCreateForm,
    AssetUpdateForm,
    AssignmentForm,
    AttributeForm,
    DatatypeForm,
    LDAPGroupRoleMappingForm,
    LDAPSourceForm,
    LDAPUserAccessForm,
    LoginForm,
    OperatingSystemForm,
)
from .services import (
    ServiceError,
    api_request,
    authenticate_apiuser,
    create_apiuser,
    create_ldap_group_role_mapping,
    create_ldap_source,
    delete_apiuser,
    delete_ldap_group_role_mapping,
    delete_ldap_source,
    delete_ldap_user_access,
    get_apiuser,
    get_ldap_source,
    list_audit_logs,
    list_apiusers,
    list_ldap_group_role_mappings,
    list_ldap_sources,
    list_ldap_user_access,
    update_ldap_group_role_mapping,
    update_ldap_source,
    update_ldap_user_access,
    update_apiuser,
)

APPROVAL_NOT_PENDING = 0
APPROVAL_PENDING = 1
APPROVAL_APPROVED = 2
APPROVAL_REJECTED = 3


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


def _parse_aliases_text(value: str) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,\n]", value or ""):
        alias = token.strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _normalize_os_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _asset_os_value(asset: dict[str, Any]) -> str:
    for item in asset.get("attributes", []):
        if _normalize_os_text(item.get("name")) != "os":
            continue
        value = str(item.get("value") or "").strip()
        if value:
            return value
    return ""


def _os_matches_value(operatingsystem: dict[str, Any], value: str) -> bool:
    needle = _normalize_os_text(value)
    if not needle:
        return False
    names = [_normalize_os_text(operatingsystem.get("name"))]
    names.extend(_normalize_os_text(alias) for alias in (operatingsystem.get("aliases") or []))
    return needle in names


def _find_matching_operatingsystem(operating_systems: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    for item in operating_systems:
        if _os_matches_value(item, value):
            return item
    return None


def _append_alias(existing_aliases: list[Any], new_alias: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(existing_aliases or []) + [new_alias]:
        alias = str(raw or "").strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(alias)
    return out


_FETCHMETHOD_ROW_PATTERN = re.compile(r"^fetchmethod_command_(\d+)$")


def _parse_fetchmethod_rows(post: Any) -> list[dict[str, Any]]:
    row_indices: set[int] = set()
    for key in post.keys():
        match = _FETCHMETHOD_ROW_PATTERN.match(key)
        if match:
            row_indices.add(int(match.group(1)))

    entries: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    os_to_command: dict[str, str] = {}
    for index in sorted(row_indices):
        command = (post.get(f"fetchmethod_command_{index}") or "").strip()
        if not command:
            continue

        command_key = command.lower()
        if command_key in seen_commands:
            raise ValueError(f"Duplicate fetch method command: {command}")
        seen_commands.add(command_key)

        supported_operatingsystems: list[str] = []
        seen_os: set[str] = set()
        for raw_name in post.getlist(f"fetchmethod_os_{index}"):
            name = (raw_name or "").strip().lower()
            if not name or name in seen_os:
                continue

            existing_command = os_to_command.get(name)
            if existing_command and existing_command != command:
                raise ValueError(
                    f"Operating system '{name}' is already assigned to fetch method '{existing_command}'. "
                    "Each OS can only belong to one fetch method per attribute."
                )

            os_to_command[name] = command
            seen_os.add(name)
            supported_operatingsystems.append(name)

        if not supported_operatingsystems:
            raise ValueError(f"Fetch method '{command}' must include at least one operating system")

        entries.append(
            {
                "command": command,
                "supported_operatingsystems": supported_operatingsystems,
            }
        )

    return entries


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
    fetchmethods = item.get("fetchmethods") or []
    fetch_commands = [str(entry.get("command") or "") for entry in fetchmethods]
    supported_os = [
        str(os_name)
        for entry in fetchmethods
        for os_name in (entry.get("supported_operatingsystems") or [])
    ]
    searchable = [name, data_type, description, " ".join(fetch_commands), "yes" if allow_multiple else "no", " ".join(supported_os)]

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
        if key in {"fetchmethod", "fetch", "command"}:
            if not any(_contains_text(command, value) for command in fetch_commands):
                return False
            continue
        if key in {"supportedos", "supported_os", "operatingsystem", "os"}:
            if not any(_contains_text(entry, value) for entry in supported_os):
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


def _operatingsystem_matches_logic_query(item: dict[str, Any], query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    name = item.get("name")
    description = item.get("description")
    aliases = item.get("aliases") or []
    aliases_text = " ".join(str(alias) for alias in aliases)

    for key, value in terms:
        if value is None:
            if not _matches_logic_text_fields([name, description, aliases_text], key):
                return False
            continue

        if key in {"name", "os", "operatingsystem"}:
            if not _contains_text(name, value):
                return False
            continue
        if key in {"description", "desc"}:
            if not _contains_text(description, value):
                return False
            continue
        if key in {"alias", "aliases"}:
            if not any(_contains_text(alias, value) for alias in aliases):
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


def _ldap_source_matches_logic_query(item: Any, query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    searchable = [
        item.name,
        item.hostname,
        item.protocol,
        item.server_type,
        item.base_dn,
        item.group_base_dn or "",
        item.group_membership,
        item.ldap_filter,
        item.attr_username,
        item.attr_first_name,
        item.attr_last_name,
        item.attr_email,
        "yes" if item.is_active else "no",
    ]

    for key, value in terms:
        if value is None:
            if key == "active" and not item.is_active:
                return False
            if key in {"inactive", "disabled"} and item.is_active:
                return False
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"name", "source"}:
            if not _contains_text(item.name, value):
                return False
            continue
        if key in {"host", "hostname"}:
            if not _contains_text(item.hostname, value):
                return False
            continue
        if key in {"protocol", "server_type", "group_membership"}:
            if not _matches_exact_text(getattr(item, key), value):
                return False
            continue
        if key in {"base_dn", "group_base_dn", "ldap_filter", "attr_username", "attr_email"}:
            if not _contains_text(getattr(item, key), value):
                return False
            continue
        if key in {"active", "is_active", "status"}:
            parsed = _parse_bool_text(value)
            if parsed is None or item.is_active is not parsed:
                return False
            continue
        return False

    return True


def _ldap_user_access_matches_logic_query(item: Any, query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    searchable = [
        item.username,
        item.source_name or "",
        "yes" if item.readonly else "no",
        "yes" if item.is_active else "no",
        item.last_login_at,
    ]
    for key, value in terms:
        if value is None:
            if key == "readonly" and not item.readonly:
                return False
            if key in {"write", "writable", "readonly=false"} and item.readonly:
                return False
            if key == "active" and not item.is_active:
                return False
            if key in {"inactive", "disabled"} and item.is_active:
                return False
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"username", "user"}:
            if not _contains_text(item.username, value):
                return False
            continue
        if key in {"source", "source_name"}:
            if not _contains_text(item.source_name, value):
                return False
            continue
        if key == "readonly":
            parsed = _parse_bool_text(value)
            if parsed is None or item.readonly is not parsed:
                return False
            continue
        if key in {"active", "is_active", "status"}:
            parsed = _parse_bool_text(value)
            if parsed is None or item.is_active is not parsed:
                return False
            continue
        if key in {"last_login", "last_login_at"}:
            if not _contains_text(item.last_login_at, value):
                return False
            continue
        return False

    return True


def _ldap_group_mapping_matches_logic_query(item: Any, query: str) -> bool:
    terms = _asset_filter_terms(query)
    if not terms:
        return True

    searchable = [
        item.source_name or "",
        item.group_name,
        "yes" if item.readonly else "no",
        "yes" if item.is_active else "no",
        item.changed_at,
    ]
    for key, value in terms:
        if value is None:
            if key == "readonly" and not item.readonly:
                return False
            if key in {"write", "writable", "readonly=false"} and item.readonly:
                return False
            if key == "active" and not item.is_active:
                return False
            if key in {"inactive", "disabled"} and item.is_active:
                return False
            if not _matches_logic_text_fields(searchable, key):
                return False
            continue

        if key in {"source", "source_name"}:
            if not _contains_text(item.source_name, value):
                return False
            continue
        if key in {"group", "group_name"}:
            if not _contains_text(item.group_name, value):
                return False
            continue
        if key == "readonly":
            parsed = _parse_bool_text(value)
            if parsed is None or item.readonly is not parsed:
                return False
            continue
        if key in {"active", "is_active", "status"}:
            parsed = _parse_bool_text(value)
            if parsed is None or item.is_active is not parsed:
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

    attribute_catalog: list[dict[str, Any]] = []
    operating_systems: list[dict[str, Any]] = []
    try:
        attribute_catalog = api_request(*_creds(request), "GET", "/v1/attributes")
    except ServiceError:
        attribute_catalog = []
    try:
        operating_systems = api_request(*_creds(request), "GET", "/v1/operatingsystems")
    except ServiceError:
        operating_systems = []

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("assets")
        action = request.POST.get("action")
        if action == "approve":
            asset_id = request.POST.get("asset_id", "").strip()
            if not asset_id.isdigit():
                messages.error(request, "Invalid asset id")
                return redirect("assets")
            try:
                approved_asset = api_request(*_creds(request), "POST", f"/v1/assets/{asset_id}/approve")
                messages.success(request, f"Asset approved: {approved_asset.get('assetname')}")
                notify_ui_update("assets", "approved", approved_asset.get("assetname", ""))
            except ServiceError as exc:
                messages.error(request, str(exc))
            return redirect("assets")

        if action == "approve-all":
            try:
                api_request(*_creds(request), "POST", "/v1/assets/approve-all")
                messages.success(request, "All assets approved")
                notify_ui_update("assets", "approve-all", "*")
            except ServiceError as exc:
                messages.error(request, str(exc))
            return redirect("assets")

        if action == "match-os":
            asset_id = request.POST.get("asset_id", "").strip()
            operating_system_id = request.POST.get("operatingsystem_id", "").strip()
            source_os_value = (request.POST.get("source_os_value") or "").strip()
            if not asset_id.isdigit() or not operating_system_id.isdigit() or not source_os_value:
                messages.error(request, "Invalid operating system matching request")
                return redirect("assets")

            target = next((item for item in operating_systems if int(item.get("id", 0)) == int(operating_system_id)), None)
            if target is None:
                messages.error(request, "Selected operating system not found")
                return redirect("assets")

            if _os_matches_value(target, source_os_value):
                messages.info(request, "OS value is already matched")
                return redirect("assets")

            updated_aliases = _append_alias(target.get("aliases") or [], source_os_value)
            try:
                api_request(
                    *_creds(request),
                    "PATCH",
                    f"/v1/operatingsystems/{target['id']}",
                    payload={"aliases": updated_aliases},
                )
                messages.success(request, f"Added alias '{source_os_value}' to OS '{target.get('name')}'")
                notify_ui_update("operatingsystems", "alias-added", target.get("name", ""))
            except ServiceError as exc:
                messages.error(request, str(exc))
            return redirect("assets")

        if create_form.is_valid():
            try:
                created_asset = api_request(*_creds(request), "POST", "/v1/assets", payload={"assetname": create_form.cleaned_data["assetname"]})

                asset_ref = str(created_asset.get("id") or create_form.cleaned_data["assetname"])
                attribute_names = request.POST.getlist("new_attribute_name")
                attribute_values = request.POST.getlist("new_attribute_value")
                assignment_errors: list[str] = []

                for index, raw_name in enumerate(attribute_names):
                    attribute_name = (raw_name or "").strip().lower()
                    if not attribute_name:
                        continue

                    raw_value = attribute_values[index] if index < len(attribute_values) else ""
                    value = raw_value if raw_value != "" else None
                    try:
                        api_request(
                            *_creds(request),
                            "POST",
                            f"/v1/assets/{asset_ref}/attributes",
                            payload={"attribute_name": attribute_name, "value": value},
                        )
                    except ServiceError as exc:
                        assignment_errors.append(f"{attribute_name}: {exc}")

                if assignment_errors:
                    messages.warning(request, "Asset created, but some assignments failed: " + "; ".join(assignment_errors))

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
        asset_os_value = _asset_os_value(item)
        matched_os = _find_matching_operatingsystem(operating_systems, asset_os_value) if asset_os_value else None
        item["asset_os_value"] = asset_os_value
        item["os_mismatch"] = bool(asset_os_value) and matched_os is None
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
    pending_approval_count = sum(1 for item in assets if int(item.get("approved", APPROVAL_NOT_PENDING)) == APPROVAL_PENDING)
    base_params = {
        "q": filter_query,
    }
    return render(
        request,
        "webui/assets_list.html",
        {
            "assets": assets,
            "create_form": create_form,
            "attribute_catalog": attribute_catalog,
            "operating_systems": operating_systems,
            "active_count": active_count,
            "decommissioned_count": decommissioned_count,
            "pending_approval_count": pending_approval_count,
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
    operating_systems: list[dict[str, Any]] = []

    try:
        assets = api_request(*_creds(request), "GET", "/v1/assets", params={"active": "true", "q": asset_ref})
        exact = next((item for item in assets if item["assetname"] == asset_ref or str(item["id"]) == asset_ref), None)
        asset = exact or (assets[0] if assets else None)
        if asset is None:
            raise ServiceError("Asset not found")
        update_form = AssetUpdateForm(initial={"assetname": asset["assetname"]})
        attributes = api_request(*_creds(request), "GET", "/v1/attributes")
        operating_systems = api_request(*_creds(request), "GET", "/v1/operatingsystems")
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
            elif action == "edit-assignment":
                attribute_name = (request.POST.get("attribute_name") or "").strip()
                if not attribute_name:
                    messages.error(request, "Missing attribute name")
                    return redirect("asset-detail", asset_ref=asset["assetname"])

                raw_value = request.POST.get("value")
                value = raw_value if raw_value not in {None, ""} else None
                payload = {attribute_name: value}
                result = api_request(*_creds(request), "POST", f"/v1/assets/{asset['id']}/attributes", payload=payload)
                result_message = str((result or {}).get("message") or "")
                if result_message == "Assignment unchanged":
                    messages.info(request, f"Assignment unchanged for '{attribute_name}'")
                else:
                    messages.success(request, f"Assignment updated for '{attribute_name}'")
                    notify_ui_update("assignments", "updated", asset["assetname"])
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
            elif action == "match-os":
                source_os_value = (request.POST.get("source_os_value") or "").strip()
                operating_system_id = request.POST.get("operatingsystem_id", "").strip()
                if not source_os_value or not operating_system_id.isdigit():
                    messages.error(request, "Invalid operating system matching request")
                    return redirect("asset-detail", asset_ref=asset["assetname"])

                target = next((item for item in operating_systems if int(item.get("id", 0)) == int(operating_system_id)), None)
                if target is None:
                    messages.error(request, "Selected operating system not found")
                    return redirect("asset-detail", asset_ref=asset["assetname"])

                if _os_matches_value(target, source_os_value):
                    messages.info(request, "OS value is already matched")
                    return redirect("asset-detail", asset_ref=asset["assetname"])

                updated_aliases = _append_alias(target.get("aliases") or [], source_os_value)
                api_request(
                    *_creds(request),
                    "PATCH",
                    f"/v1/operatingsystems/{target['id']}",
                    payload={"aliases": updated_aliases},
                )
                messages.success(request, f"Added alias '{source_os_value}' to OS '{target.get('name')}'")
                notify_ui_update("operatingsystems", "alias-added", target.get("name", ""))
                return redirect("asset-detail", asset_ref=asset["assetname"])
        except ServiceError as exc:
            messages.error(request, str(exc))

    try:
        asset = api_request(*_creds(request), "GET", f"/v1/assets/{asset['id']}")
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("assets")

    asset_os_value = _asset_os_value(asset)
    asset_os_mismatch = bool(asset_os_value) and _find_matching_operatingsystem(operating_systems, asset_os_value) is None

    return render(
        request,
        "webui/asset_detail.html",
        {
            "asset": asset,
            "attributes": attributes,
            "assignment_form": assignment_form,
            "update_form": update_form,
            "operating_systems": operating_systems,
            "asset_os_value": asset_os_value,
            "asset_os_mismatch": asset_os_mismatch,
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
        rows = []
        for entry in history_entries:
            row = dict(entry)
            row["can_restore"] = not request.user.readonly and row.get("value") is not None
            rows.append(row)
        return JsonResponse(
            {
                "asset_ref": asset_ref,
                "attribute": attribute_ref,
                "readonly": request.user.readonly,
                "history": rows,
            }
        )
    except ServiceError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@login_required
def asset_attribute_restore_view(request: HttpRequest, asset_ref: str, attribute_ref: str) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    if request.user.readonly:
        return JsonResponse({"detail": "This user has readonly access."}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload"}, status=400)

    if "value" not in payload:
        return JsonResponse({"detail": "Missing value"}, status=400)

    try:
        asset: dict[str, Any] | None = None
        if str(asset_ref).isdigit():
            try:
                asset = api_request(*_creds(request), "GET", f"/v1/assets/{asset_ref}")
            except ServiceError:
                asset = None
        if asset is None:
            assets = api_request(*_creds(request), "GET", "/v1/assets", params={"q": asset_ref})
            asset = next((item for item in assets if item["assetname"] == asset_ref or str(item["id"]) == asset_ref), None)
        if asset is None:
            return JsonResponse({"detail": "Asset not found"}, status=404)

        restore_payload = {attribute_ref: payload.get("value")}
        api_request(*_creds(request), "POST", f"/v1/assets/{asset['id']}/attributes", payload=restore_payload)
        notify_ui_update("assignments", "restored", asset["assetname"])
        return JsonResponse({"status": "ok", "message": "Value restored"})
    except ServiceError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@login_required
def attributes_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "name") or _param(request, "data_type")
    sort_by = _param(request, "sort_by") or "name"
    sort_dir = _sort_direction(request)
    datatypes: list[dict[str, Any]] = []
    operating_systems: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass
    try:
        operating_systems = api_request(*_creds(request), "GET", "/v1/operatingsystems")
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
                payload = dict(create_form.cleaned_data)
                payload["fetchmethods"] = _parse_fetchmethod_rows(request.POST)
                api_request(*_creds(request), "POST", "/v1/attributes", payload=payload)
                messages.success(request, "Attribute created")
                notify_ui_update("attributes", "created", create_form.cleaned_data["name"])
                return redirect("attributes")
            except ValueError as exc:
                messages.error(request, str(exc))
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
            "operating_systems": operating_systems,
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
    operating_systems: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass
    try:
        operating_systems = api_request(*_creds(request), "GET", "/v1/operatingsystems")
    except ServiceError:
        pass
    datatype_choices = [(item["name"], item["name"]) for item in datatypes if item.get("name")]

    attribute = None
    fetchmethod_rows: list[dict[str, Any]] = []
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
            fetchmethod_rows = attribute.get("fetchmethods") or []
        except ServiceError as exc:
            messages.error(request, str(exc))
            return redirect("attributes")

    form = AttributeForm(
        request.POST or None,
        initial=initial,
        datatype_choices=datatype_choices,
    )
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("attributes")
        if attribute is not None and attribute.get("immutable"):
            messages.error(request, f"Attribute '{attribute.get('name')}' is immutable and cannot be changed")
            return redirect("attributes")
        if form.is_valid():
            try:
                payload = dict(form.cleaned_data)
                payload["fetchmethods"] = _parse_fetchmethod_rows(request.POST)
                if attribute_id is None:
                    api_request(*_creds(request), "POST", "/v1/attributes", payload=payload)
                    messages.success(request, "Attribute created")
                    notify_ui_update("attributes", "created", form.cleaned_data["name"])
                else:
                    api_request(*_creds(request), "PATCH", f"/v1/attributes/{attribute_id}", payload=payload)
                    messages.success(request, "Attribute updated")
                    notify_ui_update("attributes", "updated", form.cleaned_data["name"])
                return redirect("attributes")
            except ValueError as exc:
                messages.error(request, str(exc))
            except ServiceError as exc:
                messages.error(request, str(exc))

    return render(
        request,
        "webui/attribute_form.html",
        {
            "form": form,
            "attribute": attribute,
            "datatypes": datatypes,
            "operating_systems": operating_systems,
            "fetchmethod_rows": fetchmethod_rows,
        },
    )


@login_required
def attribute_delete_view(request: HttpRequest, attribute_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("attributes")
    if request.user.readonly:
        messages.error(request, "This user has readonly access.")
        return redirect("attributes")
    try:
        attributes = api_request(*_creds(request), "GET", "/v1/attributes")
        attribute = next((item for item in attributes if item.get("id") == attribute_id), None)
        if attribute is not None and attribute.get("immutable"):
            messages.error(request, f"Attribute '{attribute.get('name')}' is immutable and cannot be deleted")
            return redirect("attributes")

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
def operatingsystems_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "name")
    sort_by = _param(request, "sort_by") or "name"
    sort_dir = _sort_direction(request)
    create_form = OperatingSystemForm(request.POST if request.method == "POST" else None)

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("operatingsystems")
        if create_form.is_valid():
            try:
                payload = {
                    "name": create_form.cleaned_data["name"],
                    "description": create_form.cleaned_data["description"] or None,
                    "aliases": _parse_aliases_text(create_form.cleaned_data["aliases"]),
                }
                api_request(*_creds(request), "POST", "/v1/operatingsystems", payload=payload)
                messages.success(request, "Operating system created")
                notify_ui_update("operatingsystems", "created", create_form.cleaned_data["name"])
                return redirect("operatingsystems")
            except ServiceError as exc:
                messages.error(request, str(exc))

    items: list[dict[str, Any]] = []
    try:
        items = api_request(*_creds(request), "GET", "/v1/operatingsystems")
    except ServiceError as exc:
        messages.error(request, str(exc))

    filtered_items: list[dict[str, Any]] = []
    for item in items:
        if not _operatingsystem_matches_logic_query(item, search):
            continue
        filtered_items.append(item)

    filtered_items = _sort_items(
        filtered_items,
        sort_by,
        sort_dir,
        {
            "name": lambda item: str(item.get("name") or "").lower(),
            "description": lambda item: str(item.get("description") or "").lower(),
            "aliases": lambda item: " ".join(item.get("aliases") or []).lower(),
        },
    )

    return render(
        request,
        "webui/operatingsystems_list.html",
        {
            "operatingsystems": filtered_items,
            "create_form": create_form,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["name", "aliases", "description"]),
        },
    )


@login_required
def operatingsystem_form_view(request: HttpRequest, operatingsystem_id: int | None = None) -> HttpResponse:
    item: dict[str, Any] | None = None
    initial: dict[str, Any] = {}

    if operatingsystem_id is not None:
        try:
            operatingsystems = api_request(*_creds(request), "GET", "/v1/operatingsystems")
            item = next((entry for entry in operatingsystems if entry["id"] == operatingsystem_id), None)
            if item is None:
                messages.error(request, "Operating system not found")
                return redirect("operatingsystems")
            initial = {
                "name": item["name"],
                "description": item.get("description") or "",
                "aliases": ", ".join(item.get("aliases") or []),
            }
        except ServiceError as exc:
            messages.error(request, str(exc))
            return redirect("operatingsystems")

    form = OperatingSystemForm(request.POST or None, initial=initial)
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("operatingsystems")
        if form.is_valid():
            payload = {
                "name": form.cleaned_data["name"],
                "description": form.cleaned_data["description"] or None,
                "aliases": _parse_aliases_text(form.cleaned_data["aliases"]),
            }
            try:
                if operatingsystem_id is None:
                    api_request(*_creds(request), "POST", "/v1/operatingsystems", payload=payload)
                    messages.success(request, "Operating system created")
                    notify_ui_update("operatingsystems", "created", payload["name"])
                else:
                    api_request(*_creds(request), "PATCH", f"/v1/operatingsystems/{operatingsystem_id}", payload=payload)
                    messages.success(request, "Operating system updated")
                    notify_ui_update("operatingsystems", "updated", payload["name"])
                return redirect("operatingsystems")
            except ServiceError as exc:
                messages.error(request, str(exc))

    return render(request, "webui/operatingsystem_form.html", {"form": form, "operatingsystem": item})


@login_required
def operatingsystem_delete_view(request: HttpRequest, operatingsystem_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("operatingsystems")
    if request.user.readonly:
        messages.error(request, "This user has readonly access.")
        return redirect("operatingsystems")
    try:
        api_request(*_creds(request), "DELETE", f"/v1/operatingsystems/{operatingsystem_id}")
        messages.success(request, "Operating system deleted")
        notify_ui_update("operatingsystems", "deleted", str(operatingsystem_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("operatingsystems")


@login_required
def apiusers_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "username") or _param(request, "name")
    sort_by = _param(request, "sort_by") or "username"
    sort_dir = _sort_direction(request)

    apiusers = []
    for item in list_apiusers(*_creds(request)):
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
def ldap_sources_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "name") or _param(request, "hostname")
    sort_by = _param(request, "sort_by") or "name"
    sort_dir = _sort_direction(request)

    sources = []
    for item in list_ldap_sources(*_creds(request)):
        if not _ldap_source_matches_logic_query(item, search):
            continue
        sources.append(item)

    sources = _sort_items(
        sources,
        sort_by,
        sort_dir,
        {
            "name": lambda item: str(item.name or "").lower(),
            "hostname": lambda item: str(item.hostname or "").lower(),
            "protocol": lambda item: str(item.protocol or "").lower(),
            "is_active": lambda item: 1 if item.is_active else 0,
            "changed_at": lambda item: str(item.changed_at or ""),
        },
    )

    return render(
        request,
        "webui/ldap_sources_list.html",
        {
            "sources": sources,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["name", "hostname", "protocol", "is_active", "changed_at"]),
        },
    )


@login_required
def ldap_source_form_view(request: HttpRequest, source_id: int | None = None) -> HttpResponse:
    source_record = get_ldap_source(*_creds(request), source_id) if source_id is not None else None
    if source_id is not None and source_record is None:
        messages.error(request, "LDAP source not found")
        return redirect("ldap-sources")

    initial: dict[str, Any] = {}
    if source_record is not None:
        initial = {
            "name": source_record.name,
            "hostname": source_record.hostname,
            "port": source_record.port,
            "protocol": source_record.protocol,
            "verify_certs": source_record.verify_certs,
            "server_type": source_record.server_type,
            "bind_dn": source_record.bind_dn or "",
            "base_dn": source_record.base_dn,
            "group_base_dn": source_record.group_base_dn or "",
            "group_membership": source_record.group_membership,
            "ldap_filter": source_record.ldap_filter,
            "attr_username": source_record.attr_username,
            "attr_first_name": source_record.attr_first_name,
            "attr_last_name": source_record.attr_last_name,
            "attr_email": source_record.attr_email,
            "is_active": source_record.is_active,
        }

    form = LDAPSourceForm(request.POST or None, initial=initial)
    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("ldap-sources")
        if form.is_valid():
            payload = {
                "name": form.cleaned_data["name"],
                "hostname": form.cleaned_data["hostname"],
                "port": form.cleaned_data["port"],
                "protocol": form.cleaned_data["protocol"],
                "verify_certs": form.cleaned_data["verify_certs"],
                "server_type": form.cleaned_data["server_type"],
                "bind_dn": form.cleaned_data["bind_dn"] or None,
                "base_dn": form.cleaned_data["base_dn"],
                "group_base_dn": form.cleaned_data["group_base_dn"] or None,
                "group_membership": form.cleaned_data["group_membership"],
                "ldap_filter": form.cleaned_data["ldap_filter"],
                "attr_username": form.cleaned_data["attr_username"],
                "attr_first_name": form.cleaned_data["attr_first_name"],
                "attr_last_name": form.cleaned_data["attr_last_name"],
                "attr_email": form.cleaned_data["attr_email"],
                "is_active": form.cleaned_data["is_active"],
            }
            if form.cleaned_data["bind_password"]:
                payload["bind_password"] = form.cleaned_data["bind_password"]

            try:
                if source_id is None:
                    create_ldap_source(*_creds(request), payload)
                    messages.success(request, "LDAP source created")
                    notify_ui_update("ldap-sources", "created", form.cleaned_data["name"])
                else:
                    update_ldap_source(*_creds(request), source_id, payload)
                    messages.success(request, "LDAP source updated")
                    notify_ui_update("ldap-sources", "updated", form.cleaned_data["name"])
                return redirect("ldap-sources")
            except ServiceError as exc:
                messages.error(request, str(exc))

    return render(request, "webui/ldap_source_form.html", {"form": form, "source_record": source_record})


@login_required
def ldap_source_delete_view(request: HttpRequest, source_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("ldap-sources")
    if request.user.readonly:
        messages.error(request, "This user has readonly access.")
        return redirect("ldap-sources")
    try:
        delete_ldap_source(*_creds(request), source_id)
        messages.success(request, "LDAP source deleted")
        notify_ui_update("ldap-sources", "deleted", str(source_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("ldap-sources")


@login_required
def ldap_users_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "username") or _param(request, "source")
    sort_by = _param(request, "sort_by") or "username"
    sort_dir = _sort_direction(request)

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("ldap-users")
        username = (request.POST.get("username") or "").strip()
        action = (request.POST.get("action") or "").strip()
        if not username:
            messages.error(request, "Missing username")
            return redirect("ldap-users")
        try:
            if action == "delete":
                delete_ldap_user_access(*_creds(request), username)
                messages.success(request, f"Deleted policy for {username}")
                notify_ui_update("ldap-users", "deleted", username)
            else:
                form = LDAPUserAccessForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Invalid LDAP user policy form")
                    return redirect("ldap-users")
                update_ldap_user_access(
                    *_creds(request),
                    username,
                    {
                        "readonly": form.cleaned_data["readonly"],
                        "is_active": form.cleaned_data["is_active"],
                    },
                )
                messages.success(request, f"Updated policy for {username}")
                notify_ui_update("ldap-users", "updated", username)
        except ServiceError as exc:
            messages.error(request, str(exc))
        return redirect("ldap-users")

    users = []
    for item in list_ldap_user_access(*_creds(request)):
        if not _ldap_user_access_matches_logic_query(item, search):
            continue
        users.append(item)

    users = _sort_items(
        users,
        sort_by,
        sort_dir,
        {
            "username": lambda item: str(item.username or "").lower(),
            "source_name": lambda item: str(item.source_name or "").lower(),
            "readonly": lambda item: 1 if item.readonly else 0,
            "is_active": lambda item: 1 if item.is_active else 0,
            "last_login_at": lambda item: str(item.last_login_at or ""),
            "changed_at": lambda item: str(item.changed_at or ""),
        },
    )

    return render(
        request,
        "webui/ldap_users_list.html",
        {
            "ldap_users": users,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["username", "source_name", "readonly", "is_active", "last_login_at", "changed_at"]),
        },
    )


@login_required
def ldap_group_mappings_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "group_name") or _param(request, "source")
    sort_by = _param(request, "sort_by") or "source_name"
    sort_dir = _sort_direction(request)

    sources = list_ldap_sources(*_creds(request))
    source_choices = [(str(item.id), item.name) for item in sources]
    create_form = LDAPGroupRoleMappingForm(
        request.POST if request.method == "POST" and (request.POST.get("action") or "").strip() == "create" else None,
        source_choices=source_choices,
    )

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("ldap-group-mappings")

        action = (request.POST.get("action") or "").strip()
        try:
            if action == "delete":
                mapping_id = int((request.POST.get("mapping_id") or "0").strip())
                if mapping_id <= 0:
                    messages.error(request, "Invalid mapping id")
                    return redirect("ldap-group-mappings")
                delete_ldap_group_role_mapping(*_creds(request), mapping_id)
                messages.success(request, "Deleted LDAP group mapping")
                notify_ui_update("ldap-group-mappings", "deleted", str(mapping_id))
                return redirect("ldap-group-mappings")

            if action == "update":
                mapping_id = int((request.POST.get("mapping_id") or "0").strip())
                if mapping_id <= 0:
                    messages.error(request, "Invalid mapping id")
                    return redirect("ldap-group-mappings")

                readonly_value = _parse_bool_text(request.POST.get("readonly") or "")
                is_active_value = _parse_bool_text(request.POST.get("is_active") or "")
                if readonly_value is None or is_active_value is None:
                    messages.error(request, "Invalid role or status")
                    return redirect("ldap-group-mappings")

                update_ldap_group_role_mapping(
                    *_creds(request),
                    mapping_id,
                    {
                        "readonly": readonly_value,
                        "is_active": is_active_value,
                    },
                )
                messages.success(request, "Updated LDAP group mapping")
                notify_ui_update("ldap-group-mappings", "updated", str(mapping_id))
                return redirect("ldap-group-mappings")

            if action == "create":
                if not create_form.is_valid():
                    messages.error(request, "Invalid mapping form")
                    return redirect("ldap-group-mappings")

                create_ldap_group_role_mapping(
                    *_creds(request),
                    {
                        "source_id": int(create_form.cleaned_data["source_id"]),
                        "group_name": create_form.cleaned_data["group_name"],
                        "readonly": create_form.cleaned_data["readonly"],
                        "is_active": create_form.cleaned_data["is_active"],
                    },
                )
                messages.success(request, "Created LDAP group mapping")
                notify_ui_update("ldap-group-mappings", "created", create_form.cleaned_data["group_name"])
                return redirect("ldap-group-mappings")

            messages.error(request, "Unknown action")
            return redirect("ldap-group-mappings")
        except (ServiceError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("ldap-group-mappings")

    mappings = []
    for item in list_ldap_group_role_mappings(*_creds(request)):
        if not _ldap_group_mapping_matches_logic_query(item, search):
            continue
        mappings.append(item)

    mappings = _sort_items(
        mappings,
        sort_by,
        sort_dir,
        {
            "source_name": lambda item: str(item.source_name or "").lower(),
            "group_name": lambda item: str(item.group_name or "").lower(),
            "readonly": lambda item: 1 if item.readonly else 0,
            "is_active": lambda item: 1 if item.is_active else 0,
            "changed_at": lambda item: str(item.changed_at or ""),
        },
    )

    return render(
        request,
        "webui/ldap_group_mappings_list.html",
        {
            "mappings": mappings,
            "source_choices": source_choices,
            "create_form": create_form,
            "filters": {
                "q": search,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            "sort_links": _sort_link_data({"q": search}, sort_by, sort_dir, ["source_name", "group_name", "readonly", "is_active", "changed_at"]),
        },
    )


@login_required
def audit_view(request: HttpRequest) -> HttpResponse:
    search = _raw_param(request, "q") or _param(request, "actor_username") or _param(request, "entity_type")
    sort_by = _param(request, "sort_by") or "created_at"
    sort_dir = _sort_direction(request)

    audit_logs = []
    for item in list_audit_logs(*_creds(request)):
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
def docs_view(request: HttpRequest) -> HttpResponse:
    api_url = request.build_absolute_uri("/api")
    return render(request, "webui/docs.html", {"api_url": api_url})


@login_required
def apiuser_form_view(request: HttpRequest, user_id: int | None = None) -> HttpResponse:
    user_record = get_apiuser(*_creds(request), user_id) if user_id is not None else None
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
                        *_creds(request),
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
                        *_creds(request),
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
        delete_apiuser(*_creds(request), user_id)
        messages.success(request, "API user deleted")
        notify_ui_update("apiusers", "deleted", str(user_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("apiusers")
