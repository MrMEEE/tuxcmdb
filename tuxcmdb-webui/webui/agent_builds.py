"""Server-side connection to the optional OpenShift agent updater."""

from typing import Any

from django.conf import settings
import requests


class AgentUpdaterError(Exception):
    pass


def enabled() -> bool:
    return (
        settings.INSTALLATION_MODE == "openshift"
        and bool(settings.AGENT_UPDATER_URL)
        and bool(settings.AGENT_UPDATER_TOKEN)
    )


def request(method: str, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if not enabled():
        raise AgentUpdaterError("Agent builds are not configured for this installation.")
    try:
        response = requests.request(
            method, settings.AGENT_UPDATER_URL.rstrip("/") + endpoint,
            headers={"Authorization": f"Bearer {settings.AGENT_UPDATER_TOKEN}"},
            json=data, timeout=3,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise AgentUpdaterError("The agent updater is unavailable. Check its pod and logs.") from exc
    if not isinstance(result, dict):
        raise AgentUpdaterError("The agent updater returned an invalid response.")
    return result
