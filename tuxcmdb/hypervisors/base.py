from __future__ import annotations

from abc import ABC, abstractmethod
import json
from typing import Any
import xml.etree.ElementTree as ET


class HypervisorProbeError(Exception):
    """Raised when a hypervisor connection probe fails."""


class HypervisorBackend(ABC):
    key: str
    label: str

    @abstractmethod
    def probe_connection(self, *, hostname: str, username: str, password: str, verify_ssl: bool) -> dict[str, Any]:
        raise NotImplementedError


_BACKENDS: dict[str, type[HypervisorBackend]] = {}


def register_backend(backend_cls: type[HypervisorBackend]) -> type[HypervisorBackend]:
    _BACKENDS[backend_cls.key] = backend_cls
    return backend_cls


def list_supported_hypervisor_types() -> list[dict[str, str]]:
    return [{"key": key, "label": backend_cls.label} for key, backend_cls in sorted(_BACKENDS.items())]


def normalize_cluster_type(value: str) -> str:
    return (value or "").strip().lower()


def get_hypervisor_backend(cluster_type: str) -> HypervisorBackend:
    normalized = normalize_cluster_type(cluster_type)
    backend_cls = _BACKENDS.get(normalized)
    if backend_cls is None:
        raise HypervisorProbeError(f"Unsupported hypervisor type: {cluster_type}")
    return backend_cls()


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _coerce_memory_gb(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lower().endswith("gb"):
            text = text[:-2].strip()
        elif text.lower().endswith("mb"):
            text = text[:-2].strip()
            return int(float(text) / 1024) if text else None
        elif text.lower().endswith("b"):
            text = text[:-1].strip()
            return int(float(text) / (1024 * 1024 * 1024)) if text else None
        try:
            numeric = float(text)
        except ValueError:
            return None
        if numeric > 1024 * 1024 * 1024:
            return int(numeric / (1024 * 1024 * 1024))
        return int(numeric)
    if isinstance(value, (int, float)):
        if value > 1024 * 1024 * 1024:
            return int(value / (1024 * 1024 * 1024))
        return int(value)
    return None


def _parse_xml_element(element: ET.Element) -> Any:
    if element.tag == "struct":
        parsed: dict[str, Any] = {}
        for member in element.findall("member"):
            name = member.findtext("name")
            value = member.find("value")
            if name is None or value is None:
                continue
            parsed[name.strip()] = _parse_xml_element(value)
        return parsed

    if element.tag == "array":
        data = element.find("data")
        if data is None:
            return []
        return [_parse_xml_element(value) for value in data.findall("value")]

    if element.tag == "value":
        children = list(element)
        if not children:
            text = (element.text or "").strip()
            return text if text else None
        if len(children) == 1:
            child = children[0]
            if child.tag == "boolean":
                text = (child.text or "").strip().lower()
                return text in {"1", "true", "yes", "on"}
            if child.tag in {"int", "i4", "double"}:
                text = (child.text or "").strip()
                try:
                    return int(float(text))
                except ValueError:
                    return text
            if child.tag == "string":
                return (child.text or "").strip()
            return _parse_xml_element(child)
        return [_parse_xml_element(child) for child in children]

    if element.tag == "boolean":
        text = (element.text or "").strip().lower()
        return text in {"1", "true", "yes", "on"}

    if element.tag in {"int", "i4", "double"}:
        text = (element.text or "").strip()
        try:
            return int(float(text))
        except ValueError:
            return text

    if element.tag == "string":
        return (element.text or "").strip()

    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return text if text else None

    parsed_children = [_parse_xml_element(child) for child in children]
    if len(children) > 1 and all(child.tag == children[0].tag for child in children):
        return parsed_children

    parsed: dict[str, Any] = {}
    for child, child_value in zip(children, parsed_children):
        parsed[child.tag] = child_value
    return parsed


def _parse_string_payload(payload: str) -> Any:
    text = payload.strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None
        return _parse_xml_element(root)
    return None


def extract_stats(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}

    if isinstance(payload, str):
        parsed = _parse_string_payload(payload)
        if parsed is not None:
            return extract_stats(parsed)

    stats: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key in ("vm_count", "vmcount", "num_vms", "vms", "resident_VMs", "vm"):
            if key in payload:
                value = payload[key]
                if key in {"vms", "resident_VMs", "vm"}:
                    if isinstance(value, list):
                        stats["vms"] = [str(item) for item in value]
                    elif isinstance(value, dict):
                        stats["vms"] = [str(item) for item in value.values()]
                    else:
                        stats["vms"] = [str(value)]
                else:
                    int_value = _coerce_int(value)
                    if int_value is not None:
                        stats["vm_count"] = int_value

        for key in ("cluster_cpus", "cpu_count", "num_cpus", "cpus", "cpu_info"):
            if key in payload:
                if key == "cpu_info" and isinstance(payload[key], dict):
                    cpu_info = payload[key]
                    cpu_count = cpu_info.get("cpu_count")
                    if cpu_count is None:
                        cpu_count = cpu_info.get("cpus")
                    int_value = _coerce_int(cpu_count)
                    if int_value is not None:
                        stats["cluster_cpus"] = int_value
                else:
                    int_value = _coerce_int(payload[key])
                    if int_value is not None:
                        stats["cluster_cpus"] = int_value

        for key in ("cluster_memory_gb", "memory_gb", "memory", "memory_mb", "memory_bytes", "memory_total"):
            if key in payload:
                value = payload[key]
                int_value = _coerce_memory_gb(value)
                if int_value is not None:
                    stats["cluster_memory_gb"] = int_value

        if "vms" not in stats and isinstance(payload.get("vms"), list):
            stats["vms"] = [str(item) for item in payload["vms"]]

        if "vm_count" not in stats and isinstance(payload.get("vms"), list):
            stats["vm_count"] = len(payload["vms"])

        if "vm_count" not in stats and isinstance(payload.get("resident_VMs"), list):
            stats["vm_count"] = len(payload["resident_VMs"])

        if "vm_count" not in stats and isinstance(payload.get("vm"), list):
            stats["vm_count"] = len(payload["vm"])

        if "cluster_cpus" not in stats:
            for nested in payload.values():
                if isinstance(nested, dict):
                    nested_stats = extract_stats(nested)
                    if nested_stats:
                        stats.update(nested_stats)
                        break

        if "cluster_cpus" not in stats and "vms" not in stats:
            for nested in payload.values():
                if isinstance(nested, (list, dict)):
                    nested_stats = extract_stats(nested)
                    if nested_stats:
                        stats.update(nested_stats)
                        break

    elif isinstance(payload, list):
        stats["vm_count"] = len(payload)
        stats["vms"] = [str(item) for item in payload]

    return stats


def probe_hypervisor_connection(*, cluster_type: str, hostname: str, username: str, password: str, verify_ssl: bool) -> dict[str, Any]:
    backend = get_hypervisor_backend(cluster_type)
    return backend.probe_connection(hostname=hostname, username=username, password=password, verify_ssl=verify_ssl)
