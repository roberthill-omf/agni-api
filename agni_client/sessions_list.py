from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from .client import AgniClient

Timestamp = Union[datetime, str]


def _to_iso_z(ts: Timestamp) -> str:
    """Normalize datetime or string timestamps to API-expected ISO with trailing Z."""
    if isinstance(ts, datetime):
        return ts.isoformat().replace("+00:00", "Z")
    return ts


@dataclass
class SessionFilter:
    field: str
    value: str

    def as_dict(self) -> Dict[str, str]:
        return {"field": self.field, "value": self.value}


@dataclass
class SessionRecord:
    acctMultiSessionID: Optional[str] = None
    acctSessionID: Optional[str] = None
    acgDeviceName: Optional[str] = None
    apMAC: Optional[str] = None
    authReqID: Optional[str] = None
    authStatus: Optional[bool] = None
    authType: Optional[str] = None
    closedAt: Optional[str] = None
    createdAt: Optional[str] = None
    dacl: Optional[str] = None
    deviceAdminPolicyName: Optional[str] = None
    error: Optional[str] = None
    identity: Optional[str] = None
    ip: Optional[str] = None
    location: Optional[str] = None
    mac: Optional[str] = None
    machineIdentity: Optional[str] = None
    nadConnID: Optional[str] = None
    nadGroupID: Optional[int] = None
    nadID: Optional[int] = None
    nadName: Optional[str] = None
    nasIP: Optional[str] = None
    nasIdentifier: Optional[str] = None
    networkID: Optional[int] = None
    networkName: Optional[str] = None
    onPremNodeID: Optional[str] = None
    orgID: Optional[str] = None
    segmentID: Optional[int] = None
    segmentName: Optional[str] = None
    tacacsProfileName: Optional[str] = None
    updatedAt: Optional[str] = None
    userID: Optional[str] = None
    zoneID: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionRecord":
        known_field_names = {f.name for f in fields(cls)}
        kwargs = {k: raw.get(k) for k in known_field_names if k != "extra"}
        extra = {k: v for k, v in raw.items() if k not in known_field_names}
        return cls(**kwargs, extra=extra)


@dataclass
class SessionListResponse:
    cursor: Optional[str]
    records: List[SessionRecord]
    raw: Dict[str, Any] = field(default_factory=dict)


class SessionAPI:
    def __init__(self, client: AgniClient) -> None:
        self.client = client

    def list_sessions(
        self,
        *,
        org_id: str,
        from_ts: Optional[Timestamp] = None,
        to_ts: Optional[Timestamp] = None,
        status: Optional[str] = None,
        session_type: Optional[str] = None,
        location: Optional[str] = None,
        on_prem_node_id: Optional[str] = None,
        zone_id: Optional[int] = None,
        query: Optional[str] = None,
        filters: Optional[List[SessionFilter]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> SessionListResponse:
        """Call session.list with the full request schema supported."""
        payload: Dict[str, Any] = {"orgID": org_id}

        if to_ts is None:
            to_ts = datetime.now(timezone.utc)
        if from_ts is None:
            from_ts = to_ts - timedelta(hours=1)

        payload["fromTimestamp"] = _to_iso_z(from_ts)
        payload["toTimestamp"] = _to_iso_z(to_ts)
        if status:
            payload["status"] = status
        if session_type:
            payload["sessionType"] = session_type
        if location:
            payload["location"] = location
        if on_prem_node_id:
            payload["onPremNodeID"] = on_prem_node_id
        if zone_id is not None:
            payload["zoneID"] = zone_id
        if query:
            payload["query"] = query
        if filters:
            payload["filters"] = [flt.as_dict() for flt in filters]
        if cursor:
            payload["cursor"] = cursor
        if limit is not None:
            payload["limit"] = limit

        body = self.client.request("POST", "session.list", json=payload)
        data = body.get("data", {}) if isinstance(body, dict) else {}

        raw_records = data.get("records", []) if isinstance(data, dict) else []
        records = [SessionRecord.from_dict(item) for item in raw_records if isinstance(item, dict)]

        return SessionListResponse(cursor=data.get("cursor") if isinstance(data, dict) else None, records=records, raw=data if isinstance(data, dict) else {})
