from __future__ import annotations

import requests

from ..base import HypervisorBackend, HypervisorProbeError, extract_stats, register_backend


def _coerce_stats_from_payload(payload: object) -> dict[str, object]:
    stats = extract_stats(payload)
    if stats:
        return {
            "vm_count": stats.get("vm_count", 0),
            "cluster_cpus": stats.get("cluster_cpus", 0),
            "cluster_memory_gb": stats.get("cluster_memory_gb", 0),
            "vms": stats.get("vms", []),
        }
    return {}


@register_backend
class VMwareBackend(HypervisorBackend):
    key = "vmware"
    label = "VMware"

    def probe_connection(self, *, hostname: str, username: str, password: str, verify_ssl: bool) -> dict[str, object]:
        if not hostname:
            raise HypervisorProbeError("Management hostname is required")
        session = requests.Session()
        session.auth = (username, password)
        session.verify = verify_ssl
        try:
            response = session.get(f"https://{hostname}/sdk", timeout=10)
        except requests.RequestException as exc:
            raise HypervisorProbeError(f"Unable to reach VMware endpoint: {exc}") from exc

        if response.status_code >= 400:
            raise HypervisorProbeError(f"VMware endpoint returned HTTP {response.status_code}")

        payload = response.text
        stats = _coerce_stats_from_payload(payload)
        if not stats:
            try:
                stats = _coerce_stats_from_payload(response.json())
            except ValueError:
                stats = {}

        return {
            "vm_count": stats.get("vm_count", 0),
            "cluster_cpus": stats.get("cluster_cpus", 0),
            "cluster_memory_gb": stats.get("cluster_memory_gb", 0),
            "vms": stats.get("vms", []),
            "status": "reachable",
        }
