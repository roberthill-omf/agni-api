"""Helpers for AGNI config.nad.* endpoints."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .client import AgniClient


def _wrap_nullable_int64(value: Optional[int]) -> Optional[Dict[str, Any]]:
    """Return API-style nullable int64 wrapper."""
    if value is None:
        return None
    return {"Int64": value, "Valid": True}


def _wrap_nullable_string(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return API-style nullable string wrapper."""
    if value is None:
        return None
    return {"String": value, "Valid": True}


@dataclass
class NADAddResponse:
    id: Optional[int] = None
    mac: Optional[str] = None
    orgID: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NADGetResponse:
    id: Optional[int] = None
    name: Optional[str] = None
    mac: Optional[str] = None
    ipAddress: Optional[str] = None
    orgID: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class ConfigNADAPI:
    """Client for AGNI config.nad.* endpoints."""

    def __init__(self, client: AgniClient) -> None:
        self.client = client

    def add_nad(
        self,
        *,
        org_id: str,
        name: str,
        vendor: str,
        zone_id: int,
        ip_address: Optional[str] = None,
        mac: Optional[List[str]] = None,
        serial_number: Optional[str] = None,
        nad_group_id: Optional[int] = None,
        external_location_id: Optional[str] = None,
        location: Optional[str] = None,
        location_ids: Optional[List[int]] = None,
        nad_type: Optional[str] = None,
        coa_port: Optional[int] = None,
        radius_shared_secret: Optional[str] = None,
        tacacs_shared_secret: Optional[str] = None,
        concourse_app_install_id: Optional[int] = None,
        cert_id: Optional[str] = None,
    ) -> NADAddResponse:
        """
        Create a network access device via config.nad.add.

        Required fields: org_id, name, vendor, zone_id.
        """
        payload: Dict[str, Any] = {
            "orgID": org_id,
            "name": name,
            "vendor": vendor,
            "zoneID": zone_id,
        }

        if ip_address:
            payload["ipAddress"] = ip_address
        if mac:
            payload["mac"] = mac
        if serial_number:
            payload["serialNumber"] = serial_number
        if nad_group_id is not None:
            wrapped = _wrap_nullable_int64(nad_group_id)
            if wrapped:
                payload["nadGroupID"] = wrapped
        if external_location_id:
            payload["externalLocationID"] = external_location_id
        if location:
            wrapped_loc = _wrap_nullable_string(location)
            if wrapped_loc:
                payload["location"] = wrapped_loc
        if location_ids:
            payload["locationID"] = location_ids
        if nad_type:
            payload["type"] = nad_type
        if concourse_app_install_id is not None:
            wrapped_concourse = _wrap_nullable_int64(concourse_app_install_id)
            if wrapped_concourse:
                payload["concourseAppInstallID"] = wrapped_concourse
        if cert_id:
            payload["certID"] = cert_id

        config_block: Dict[str, Any] = {}
        if coa_port is not None:
            config_block["coaPort"] = coa_port
        if radius_shared_secret:
            config_block["radiusSharedSecret"] = radius_shared_secret
        if tacacs_shared_secret:
            config_block["tacacsSharedSecret"] = tacacs_shared_secret
        if config_block:
            payload["config"] = config_block

        body = self.client.request("POST", "config.nad.add", json=payload)
        data = body.get("data", {}) if isinstance(body, dict) else {}

        return NADAddResponse(
            id=data.get("id") if isinstance(data, dict) else None,
            mac=data.get("mac") if isinstance(data, dict) else None,
            orgID=data.get("orgID") if isinstance(data, dict) else None,
            raw=data if isinstance(data, dict) else {},
        )

    def get_nad(self, *, org_id: str, mac: str, nad_id: int = 0, name: str = "", zone_id: int = 0) -> NADGetResponse:
        """Look up a network access device via config.nad.get (here, by MAC)."""
        payload: Dict[str, Any] = {"id": nad_id, "mac": mac, "name": name, "orgID": org_id, "zoneID": zone_id}

        body = self.client.request("POST", "config.nad.get", json=payload)
        data = body.get("data") if isinstance(body, dict) else None
        data = data if isinstance(data, dict) else {}

        return NADGetResponse(
            id=data.get("id"),
            name=data.get("name"),
            mac=data.get("mac"),
            ipAddress=data.get("ipAddress"),
            orgID=data.get("orgID"),
            raw=data,
        )
