from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

from ..base import HypervisorBackend, HypervisorProbeError, _coerce_int, register_backend

# vCenter/ESXi only exposes /sdk as a SOAP (vim25) endpoint: it accepts POSTed
# SOAP envelopes and returns 404/405 for plain GET requests (the latter is
# normal and does not indicate a misconfigured vCenter).
SOAP_ACTION = '"urn:vim25/8.0"'


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_all_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<soapenv:Body>{body}</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def _soap_call(session: requests.Session, *, hostname: str, verify_ssl: bool, body: str, timeout: int = 15) -> ET.Element:
    try:
        response = session.post(
            f"https://{hostname}/sdk",
            data=_envelope(body).encode("utf-8"),
            headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": SOAP_ACTION},
            timeout=timeout,
            verify=verify_ssl,
        )
    except requests.RequestException as exc:
        raise HypervisorProbeError(f"Unable to reach VMware endpoint: {exc}") from exc

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        raise HypervisorProbeError(f"VMware endpoint returned HTTP {response.status_code} (expected a SOAP response from /sdk)")

    body_el = _find_child(root, "Body")
    if body_el is None:
        raise HypervisorProbeError("Unexpected VMware response: missing SOAP body")

    fault_el = _find_child(body_el, "Fault")
    if fault_el is not None:
        faultstring_el = _find_child(fault_el, "faultstring")
        message = (faultstring_el.text or "").strip() if faultstring_el is not None and faultstring_el.text else "Unknown VMware SOAP fault"
        raise HypervisorProbeError(f"VMware API error: {message}")

    if response.status_code >= 400:
        raise HypervisorProbeError(f"VMware endpoint returned HTTP {response.status_code}")

    return body_el


def _get_returnval(body_el: ET.Element) -> ET.Element | None:
    response_el = next(iter(body_el), None)
    if response_el is None:
        return None
    return _find_child(response_el, "returnval")


def _retrieve_service_content(session: requests.Session, *, hostname: str, verify_ssl: bool) -> dict[str, str]:
    body = '<RetrieveServiceContent xmlns="urn:vim25"><_this type="ServiceInstance">ServiceInstance</_this></RetrieveServiceContent>'
    body_el = _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=body)
    content_el = _get_returnval(body_el)
    if content_el is None:
        raise HypervisorProbeError("VMware endpoint did not return service content")
    return {_local_name(child.tag): (child.text or "").strip() for child in content_el}


def _login(session: requests.Session, *, hostname: str, verify_ssl: bool, session_manager_ref: str, username: str, password: str) -> None:
    body = (
        '<Login xmlns="urn:vim25">'
        f'<_this type="SessionManager">{escape(session_manager_ref)}</_this>'
        f"<userName>{escape(username)}</userName>"
        f"<password>{escape(password)}</password>"
        "</Login>"
    )
    _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=body)


def _logout(session: requests.Session, *, hostname: str, verify_ssl: bool, session_manager_ref: str) -> None:
    body = f'<Logout xmlns="urn:vim25"><_this type="SessionManager">{escape(session_manager_ref)}</_this></Logout>'
    _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=body)


def _create_container_view(session: requests.Session, *, hostname: str, verify_ssl: bool, view_manager_ref: str, root_folder_ref: str, obj_type: str) -> str | None:
    body = (
        '<CreateContainerView xmlns="urn:vim25">'
        f'<_this type="ViewManager">{escape(view_manager_ref)}</_this>'
        f'<container type="Folder">{escape(root_folder_ref)}</container>'
        f"<type>{escape(obj_type)}</type>"
        "<recursive>true</recursive>"
        "</CreateContainerView>"
    )
    body_el = _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=body)
    returnval = _get_returnval(body_el)
    if returnval is None or not returnval.text:
        return None
    return returnval.text.strip()


def _parse_retrieve_result(body_el: ET.Element) -> tuple[list[dict[str, Any]], str | None]:
    result_el = _get_returnval(body_el)
    if result_el is None:
        return [], None

    items: list[dict[str, Any]] = []
    for obj_el in _find_all_children(result_el, "objects"):
        obj_ref_el = _find_child(obj_el, "obj")
        moref = (obj_ref_el.text or "").strip() if obj_ref_el is not None and obj_ref_el.text else ""
        props: dict[str, str | None] = {}
        for prop_el in _find_all_children(obj_el, "propSet"):
            name_el = _find_child(prop_el, "name")
            val_el = _find_child(prop_el, "val")
            if name_el is None or not name_el.text:
                continue
            props[name_el.text.strip()] = (val_el.text or "").strip() if val_el is not None and val_el.text else None
        items.append({"moref": moref, "props": props})

    token_el = _find_child(result_el, "token")
    token = (token_el.text or "").strip() if token_el is not None and token_el.text else None
    return items, token


def _retrieve_properties(
    session: requests.Session,
    *,
    hostname: str,
    verify_ssl: bool,
    property_collector_ref: str,
    container_view_ref: str,
    obj_type: str,
    path_set: list[str],
) -> list[dict[str, Any]]:
    path_set_xml = "".join(f"<pathSet>{escape(path)}</pathSet>" for path in path_set)
    body = (
        '<RetrievePropertiesEx xmlns="urn:vim25">'
        f'<_this type="PropertyCollector">{escape(property_collector_ref)}</_this>'
        "<specSet>"
        f"<propSet><type>{escape(obj_type)}</type>{path_set_xml}</propSet>"
        "<objectSet>"
        f'<obj type="ContainerView">{escape(container_view_ref)}</obj>'
        "<skip>true</skip>"
        '<selectSet xsi:type="TraversalSpec">'
        "<name>traverseEntities</name>"
        "<type>ContainerView</type>"
        "<path>view</path>"
        "<skip>false</skip>"
        "</selectSet>"
        "</objectSet>"
        "</specSet>"
        "<options></options>"
        "</RetrievePropertiesEx>"
    )
    body_el = _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=body)
    items, token = _parse_retrieve_result(body_el)

    while token:
        continue_body = (
            '<ContinueRetrievePropertiesEx xmlns="urn:vim25">'
            f'<_this type="PropertyCollector">{escape(property_collector_ref)}</_this>'
            f"<token>{escape(token)}</token>"
            "</ContinueRetrievePropertiesEx>"
        )
        body_el = _soap_call(session, hostname=hostname, verify_ssl=verify_ssl, body=continue_body)
        more_items, token = _parse_retrieve_result(body_el)
        items.extend(more_items)

    return items


@register_backend
class VMwareBackend(HypervisorBackend):
    key = "vmware"
    label = "VMware"

    def probe_connection(self, *, hostname: str, username: str, password: str, verify_ssl: bool) -> dict[str, object]:
        if not hostname:
            raise HypervisorProbeError("Management hostname is required")
        session = requests.Session()
        session.verify = verify_ssl
        try:
            service_content = _retrieve_service_content(session, hostname=hostname, verify_ssl=verify_ssl)
            session_manager_ref = service_content.get("sessionManager")
            property_collector_ref = service_content.get("propertyCollector")
            view_manager_ref = service_content.get("viewManager")
            root_folder_ref = service_content.get("rootFolder")
            if not (session_manager_ref and property_collector_ref and view_manager_ref and root_folder_ref):
                raise HypervisorProbeError("Unexpected VMware response: missing service content references")

            _login(session, hostname=hostname, verify_ssl=verify_ssl, session_manager_ref=session_manager_ref, username=username, password=password)

            cluster_cpus = 0
            cluster_memory_gb = 0
            host_view_ref = _create_container_view(session, hostname=hostname, verify_ssl=verify_ssl, view_manager_ref=view_manager_ref, root_folder_ref=root_folder_ref, obj_type="HostSystem")
            if host_view_ref:
                for item in _retrieve_properties(
                    session,
                    hostname=hostname,
                    verify_ssl=verify_ssl,
                    property_collector_ref=property_collector_ref,
                    container_view_ref=host_view_ref,
                    obj_type="HostSystem",
                    path_set=["hardware.cpuInfo.numCpuCores", "hardware.memorySize"],
                ):
                    props = item["props"]
                    cluster_cpus += _coerce_int(props.get("hardware.cpuInfo.numCpuCores")) or 0
                    memory_bytes = _coerce_int(props.get("hardware.memorySize")) or 0
                    cluster_memory_gb += int(memory_bytes / (1024**3))

            vms: list[dict[str, object]] = []
            vm_view_ref = _create_container_view(session, hostname=hostname, verify_ssl=verify_ssl, view_manager_ref=view_manager_ref, root_folder_ref=root_folder_ref, obj_type="VirtualMachine")
            if vm_view_ref:
                for item in _retrieve_properties(
                    session,
                    hostname=hostname,
                    verify_ssl=verify_ssl,
                    property_collector_ref=property_collector_ref,
                    container_view_ref=vm_view_ref,
                    obj_type="VirtualMachine",
                    path_set=["name", "config.uuid", "config.hardware.numCPU", "config.hardware.memoryMB"],
                ):
                    props = item["props"]
                    memory_mb = _coerce_int(props.get("config.hardware.memoryMB")) or 0
                    vms.append(
                        {
                            "uuid": props.get("config.uuid") or item["moref"],
                            "name": props.get("name") or item["moref"],
                            "vcpus": _coerce_int(props.get("config.hardware.numCPU")) or 0,
                            "memory_gb": round(memory_mb / 1024, 2) if memory_mb else 0,
                        }
                    )

            try:
                _logout(session, hostname=hostname, verify_ssl=verify_ssl, session_manager_ref=session_manager_ref)
            except HypervisorProbeError:
                pass

            return {
                "vm_count": len(vms),
                "cluster_cpus": cluster_cpus,
                "cluster_memory_gb": cluster_memory_gb,
                "vms": vms,
                "status": "reachable",
            }
        except requests.RequestException as exc:
            raise HypervisorProbeError(f"Unable to reach VMware endpoint: {exc}") from exc
        except HypervisorProbeError:
            raise
        except Exception as exc:
            raise HypervisorProbeError(f"Unable to parse VMware response: {exc}") from exc
