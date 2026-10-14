"""Helpers for the AGNI session.details.get endpoint."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .client import AgniClient


@dataclass
class SessionDetails:
    authReqID: Optional[str] = None
    orgID: Optional[str] = None
    inputAttrs: Dict[str, Any] = field(default_factory=dict)
    outputAttrs: Dict[str, Any] = field(default_factory=dict)
    createdAt: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def certificate_san(self) -> List[str]:
        """Certificate:SAN is a list in the API; always return a list."""
        san = self.inputAttrs.get("Certificate:SAN")
        if san is None:
            return []
        return san if isinstance(san, list) else [san]

    @property
    def nas_ip(self) -> Optional[str]:
        return self.inputAttrs.get("Radius:IETF:NAS-IP-Address")

    @property
    def nas_port_id(self) -> Optional[str]:
        return self.inputAttrs.get("Radius:IETF:NAS-Port-Id")


class SessionDetailsAPI:
    def __init__(self, client: AgniClient) -> None:
        self.client = client

    def get_session_details(self, *, org_id: str, auth_req_id: str, on_prem_node_id: Optional[str] = None) -> SessionDetails:
        """Call session.details.get for a single authReqID."""
        payload: Dict[str, Any] = {"authReqID": auth_req_id, "orgID": org_id}
        if on_prem_node_id:
            payload["onPremNodeID"] = on_prem_node_id

        body = self.client.request("POST", "session.details.get", json=payload)
        data = body.get("data") if isinstance(body, dict) else None
        data = data if isinstance(data, dict) else {}

        return SessionDetails(
            authReqID=data.get("authReqID"),
            orgID=data.get("orgID"),
            inputAttrs=data.get("inputAttrs") or {},
            outputAttrs=data.get("outputAttrs") or {},
            createdAt=data.get("createdAt"),
            raw=data,
        )
