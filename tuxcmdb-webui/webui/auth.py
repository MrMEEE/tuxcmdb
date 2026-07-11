from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse


@dataclass
class SessionUser:
    username: str
    readonly: bool
    is_authenticated: bool = True


class AnonymousSessionUser:
    username = ""
    readonly = True
    is_authenticated = False


class SessionUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        username = request.session.get("api_username")
        readonly = bool(request.session.get("api_readonly", True))
        if username:
            request.user = SessionUser(username=username, readonly=readonly)
        else:
            request.user = AnonymousSessionUser()
        return self.get_response(request)


def login_required(view_func):
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return HttpResponseRedirect(reverse("login"))
        return view_func(request, *args, **kwargs)

    return wrapped


def write_required(view_func):
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return HttpResponseRedirect(reverse("login"))
        if getattr(request.user, "readonly", True):
            from django.contrib import messages
            from django.shortcuts import redirect

            messages.error(request, "This user has readonly access.")
            return redirect(request.META.get("HTTP_REFERER") or reverse("assets"))
        return view_func(request, *args, **kwargs)

    return wrapped
