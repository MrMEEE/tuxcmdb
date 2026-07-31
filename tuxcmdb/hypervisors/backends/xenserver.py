from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import requests

from ..base import HypervisorBackend, HypervisorProbeError, extract_stats, register_backend, _parse_string_payload


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


def _build_xmlrpc_body(method: str, params: list[Any]) -> str:
    root = ET.Element("methodCall")
    ET.SubElement(root, "methodName").text = method
    params_el = ET.SubElement(root, "params")

    for param in params:
        param_el = ET.SubElement(params_el, "param")
        value_el = ET.SubElement(param_el, "value")
        if isinstance(param, bool):
            child = ET.SubElement(value_el, "boolean")
            child.text = "1" if param else "0"
        elif isinstance(param, int) and not isinstance(param, bool):
            child = ET.SubElement(value_el, "int")
            child.text = str(param)
        elif isinstance(param, float):
            child = ET.SubElement(value_el, "double")
            child.text = str(param)
        else:
            child = ET.SubElement(value_el, "string")
            child.text = str(param)

    return ET.tostring(root, encoding="unicode")


def _extract_xmlrpc_result(payload: Any) -> Any:
    if isinstance(payload, dict):
        if "Status" in payload and "Value" in payload:
            return payload.get("Value")
        if "value" in payload:
            nested = _extract_xmlrpc_result(payload["value"])
            if nested is not None:
                return nested
        for value in payload.values():
            nested = _extract_xmlrpc_result(value)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for value in payload:
            nested = _extract_xmlrpc_result(value)
            if nested is not None:
                return nested
    return None


def _xmlrpc_request(session: requests.Session, *, hostname: str, method: str, params: list[Any], verify_ssl: bool) -> Any:
    body = _build_xmlrpc_body(method, params)
    response = session.post(
        f"https://{hostname}/",
        data=body,
        headers={"Content-Type": "text/xml"},
        timeout=15,
        verify=verify_ssl,
    )
    if response.status_code >= 400:
        raise HypervisorProbeError(f"XenServer/XCP-ng endpoint returned HTTP {response.status_code}")

    parsed = _parse_string_payload(response.text)
    if not isinstance(parsed, dict):
        raise HypervisorProbeError("Unable to parse XenServer/XCP-ng XML-RPC response")

    result = _extract_xmlrpc_result(parsed)
    if result is None:
        raise HypervisorProbeError("Unable to parse XenServer/XCP-ng XML-RPC response")
    return result


@register_backend
class XenServerBackend(HypervisorBackend):
    key = "xenserver"
    label = "XenServer/XCP-ng"

    def probe_connection(self, *, hostname: str, username: str, password: str, verify_ssl: bool) -> dict[str, object]:
        if not hostname:
            raise HypervisorProbeError("Management hostname is required")
        session = requests.Session()
        session.verify = verify_ssl
        try:
            session_ref = _xmlrpc_request(
                session,
                hostname=hostname,
                method="session.login_with_password",
                params=[username, password],
                verify_ssl=verify_ssl,
            )
            if not isinstance(session_ref, str) or not session_ref:
                raise HypervisorProbeError("XenServer/XCP-ng authentication failed")

            host_refs = _xmlrpc_request(session, hostname=hostname, method="host.get_all", params=[session_ref], verify_ssl=verify_ssl)
            if not isinstance(host_refs, list) or not host_refs:
                raise HypervisorProbeError("No XenServer/XCP-ng hosts were returned")

            host_ref = host_refs[0]
            host_record = _xmlrpc_request(session, hostname=hostname, method="host.get_record", params=[session_ref, host_ref], verify_ssl=verify_ssl)
            if not isinstance(host_record, dict):
                raise HypervisorProbeError("Unable to read XenServer/XCP-ng host record")

            metrics_ref = host_record.get("metrics")
            metrics_record: dict[str, Any] | None = None
            if isinstance(metrics_ref, str) and metrics_ref:
                metrics_record = _xmlrpc_request(session, hostname=hostname, method="host_metrics.get_record", params=[session_ref, metrics_ref], verify_ssl=verify_ssl)
            if not isinstance(metrics_record, dict):
                metrics_record = {}

            cpu_info = host_record.get("cpu_info") if isinstance(host_record.get("cpu_info"), dict) else {}
            cpu_count = cpu_info.get("cpu_count")
            if cpu_count is None:
                cpu_count = host_record.get("cpu_count")

            resident_vms = host_record.get("resident_VMs") if isinstance(host_record.get("resident_VMs"), list) else []
            if not isinstance(resident_vms, list):
                resident_vms = []

            memory_total = metrics_record.get("memory_total")
            memory_gb = None
            if isinstance(memory_total, (int, float)):
                memory_gb = int(memory_total / (1024 * 1024 * 1024)) if memory_total else 0
            elif isinstance(memory_total, str):
                numeric = memory_total.strip()
                if numeric.isdigit():
                    memory_gb = int(int(numeric) / (1024 * 1024 * 1024)) if int(numeric) else 0

            vm_details = []
            for vm_ref in resident_vms:
                if not isinstance(vm_ref, str):
                    continue
                try:
                    vm_record = _xmlrpc_request(session, hostname=hostname, method="VM.get_record", params=[session_ref, vm_ref], verify_ssl=verify_ssl)
                    if not isinstance(vm_record, dict):
                        continue
                    
                    vm_name = vm_record.get("name_label", "")
                    vm_uuid = vm_record.get("uuid", "")
                    vm_vcpus = vm_record.get("VCPUs_at_startup", 0)
                    if vm_vcpus is None or vm_vcpus == 0:
                        vm_vcpus = vm_record.get("VCPUs_max", 0)
                    try:
                        vm_vcpus = int(vm_vcpus) if vm_vcpus else 0
                    except (ValueError, TypeError):
                        vm_vcpus = 0
                    
                    vm_memory = vm_record.get("memory_dynamic_max", 0)
                    try:
                        vm_memory_int = int(vm_memory) if vm_memory else 0
                        vm_memory_gb = vm_memory_int / (1024 * 1024 * 1024) if vm_memory_int else 0
                    except (ValueError, TypeError):
                        vm_memory_gb = 0
                    
                    vm_details.append({
                        "uuid": vm_uuid,
                        "name": vm_name,
                        "vcpus": vm_vcpus,
                        "memory_gb": round(vm_memory_gb, 2),
                    })
                except Exception:
                    continue

            stats = {
                "vm_count": len(resident_vms),
                "cluster_cpus": int(cpu_count) if isinstance(cpu_count, (int, float)) else int(str(cpu_count)) if str(cpu_count).isdigit() else 0,
                "cluster_memory_gb": memory_gb or 0,
                "vms": vm_details,
            }
            return {**stats, "status": "reachable"}
        except requests.RequestException as exc:
            raise HypervisorProbeError(f"Unable to reach XenServer/XCP-ng endpoint: {exc}") from exc
        except HypervisorProbeError:
            raise
        except Exception as exc:
            raise HypervisorProbeError(f"Unable to parse XenServer/XCP-ng response: {exc}") from exc
