from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
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
        assets = api_request(*_creds(request), "GET", "/v1/assets")
    except ServiceError as exc:
        messages.error(request, str(exc))

    return render(request, "webui/assets_list.html", {"assets": assets, "create_form": create_form})


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
                assignment_form = AssignmentForm(request.POST)
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
def attributes_view(request: HttpRequest) -> HttpResponse:
    create_form = AttributeForm(request.POST if request.method == "POST" else None)
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
    datatypes: list[dict[str, Any]] = []
    try:
        items = api_request(*_creds(request), "GET", "/v1/attributes")
    except ServiceError as exc:
        messages.error(request, str(exc))
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass
    return render(request, "webui/attributes_list.html", {"attributes": items, "create_form": create_form, "datatypes": datatypes})


@login_required
def attribute_form_view(request: HttpRequest, attribute_id: int | None = None) -> HttpResponse:
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

    form = AttributeForm(request.POST or None, initial=initial)
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

    datatypes: list[dict[str, Any]] = []
    try:
        datatypes = api_request(*_creds(request), "GET", "/v1/datatypes")
    except ServiceError:
        pass
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
    return render(request, "webui/datatypes_list.html", {"datatypes": datatypes, "create_form": create_form})


@login_required
def apiusers_view(request: HttpRequest) -> HttpResponse:
    return render(request, "webui/apiusers_list.html", {"apiusers": list_apiusers()})


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
            "is_active": user_record.is_active,
            "readonly": user_record.readonly,
        }
    form = APIUserForm(request.POST or None, initial=initial)

    if request.method == "POST":
        if request.user.readonly:
            messages.error(request, "This user has readonly access.")
            return redirect("apiusers")
        if form.is_valid():
            try:
                if user_id is None:
                    if not form.cleaned_data["password"]:
                        raise ServiceError("Password is required for new API users")
                    create_apiuser(
                        form.cleaned_data["username"],
                        form.cleaned_data["password"],
                        form.cleaned_data["is_active"],
                        form.cleaned_data["readonly"],
                    )
                    messages.success(request, "API user created")
                    notify_ui_update("apiusers", "created", form.cleaned_data["username"])
                else:
                    update_apiuser(
                        user_id,
                        form.cleaned_data["username"],
                        form.cleaned_data["password"] or None,
                        form.cleaned_data["is_active"],
                        form.cleaned_data["readonly"],
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
        delete_apiuser(user_id)
        messages.success(request, "API user deleted")
        notify_ui_update("apiusers", "deleted", str(user_id))
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("apiusers")
