from __future__ import annotations

from django import template
from django.templatetags.static import static
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def asset_url(context: dict, path: str) -> str:
    """Resolve a static asset URL.

    Pages that must work even when the reverse proxy in front of the WebUI
    doesn't forward STATIC_URL (e.g. the public /agents/ page) can set
    ``use_agents_static`` in their template context to serve assets from
    under /agents/assets/ instead of STATIC_URL.
    """
    if context.get("use_agents_static"):
        return reverse("agents-asset", args=[path])
    return static(path)
