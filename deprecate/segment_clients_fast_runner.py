#!/usr/bin/env python3
"""Efficient segment client fetch: single window, dedupe, identities, profile, combined CSV."""

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from agni_client import AgniClient, AgniConfig
from agni_client.exceptions import APIRequestError, AuthenticationError
from agni_client.sessions_list import SessionRecord

TARGET_SEGMENT_ID = 100000
LOOKBACK_HOURS = 6


def _to_iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def deduplicate_by_mac(records: List[SessionRecord]) -> List[SessionRecord]:
    seen = set()
    unique: List[SessionRecord] = []
    for rec in records:
        mac = getattr(rec, "mac", None)
        if mac and mac in seen:
            continue
        if mac:
            seen.add(mac)
        unique.append(rec)
    return unique


def fetch_active_segment_clients(client: AgniClient, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Single call to session.active.list over the lookback window."""
    payload = {
        "orgID": org_id,
        "fromTimestamp": _to_iso_z(start),
        "toTimestamp": _to_iso_z(end),
    }
    body = client.request("POST", "session.active.list", json=payload)
    data = body.get("data", {}) if isinstance(body, dict) else {}
    raw_records: List[Dict[str, Any]] = data.get("records", []) if isinstance(data, dict) else []
    return [SessionRecord.from_dict(rec) for rec in raw_records if isinstance(rec, dict)]


def filter_by_segment(records: List[SessionRecord], segment_id: int) -> List[SessionRecord]:
    return [rec for rec in records if getattr(rec, "segmentID", None) == segment_id]


def fetch_identity_and_profile(client: AgniClient, org_id: str, macs: List[str]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for mac in macs:
        payload = {"orgID": org_id, "mac": mac}
        entry: Dict[str, Any] = {}
        try:
            get_body = client.request("POST", "identity.client.get", json=payload)
            entry["identity"] = get_body.get("data", {}) if isinstance(get_body, dict) else {}
        except APIRequestError as exc:
            print(f"identity.client.get failed for {mac}: {exc}", file=sys.stderr)

        try:
            profile_body = client.request("POST", "identity.client.profile.get", json=payload)
            entry["profile"] = profile_body.get("data", {}) if isinstance(profile_body, dict) else {}
        except APIRequestError as exc:
            print(f"identity.client.profile.get failed for {mac}: {exc}", file=sys.stderr)

        results[mac] = entry
    return results


def build_combined_rows(records: List[SessionRecord], details: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        mac = rec.mac
        detail = details.get(mac, {}) if mac else {}
        identity_data = detail.get("identity", {}) if isinstance(detail, dict) else {}
        profile_data = detail.get("profile", {}) if isinstance(detail, dict) else {}
        fingerprints = profile_data.get("fingerprints", {}) if isinstance(profile_data, dict) else {}
        dhcp = fingerprints.get("dhcp", {}) if isinstance(fingerprints, dict) else {}

        row = {
            "authReqID": getattr(rec, "authReqID", None),
            "identity": identity_data.get("identity") if isinstance(identity_data, dict) else None,
            "mac": mac,
            "macVendor": identity_data.get("macVendor") if isinstance(identity_data, dict) else None,
            "ip": getattr(rec, "ip", None) or (identity_data.get("ip") if isinstance(identity_data, dict) else None),
            "deviceType": profile_data.get("deviceType") if isinstance(profile_data, dict) else None,
            "fingerprints.dhcp.option12": dhcp.get("option12") if isinstance(dhcp, dict) else None,
            "location": getattr(rec, "location", None),
            "nasIP": getattr(rec, "nasIP", None),
        }
        rows.append(row)
    return rows


def main() -> None:
    try:
        config = AgniConfig.from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    client = AgniClient(config)
    start = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    end = datetime.now(timezone.utc)

    try:
        sessions = fetch_active_segment_clients(client, config.org_id, start, end)
        sessions = filter_by_segment(sessions, TARGET_SEGMENT_ID)
        sessions = deduplicate_by_mac(sessions)
        macs = [rec.mac for rec in sessions if rec.mac]
        details = fetch_identity_and_profile(client, config.org_id, macs)
    except AuthenticationError as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if not sessions:
        print("No matching sessions found.")
        return

    combined_rows = build_combined_rows(sessions, details)
    columns = [
        "authReqID",
        "identity",
        "mac",
        "macVendor",
        "ip",
        "deviceType",
        "fingerprints.dhcp.option12",
        "location",
        "nasIP",
    ]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"agni_segment_clients_{timestamp}.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(combined_rows)

    print(f"Wrote {len(combined_rows)} combined records to {csv_path}")


if __name__ == "__main__":
    main()
