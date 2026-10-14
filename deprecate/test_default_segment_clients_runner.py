#!/usr/bin/env python3
"""Runner for the session.active.list API with time-windowed fetch and CSV export."""

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from agni_client import AgniClient, AgniConfig
from agni_client.exceptions import APIRequestError, AuthenticationError
from agni_client.sessions_list import SessionRecord

WINDOW_MINUTES = 30
LOOKBACK_HOURS = 6
TARGET_SEGMENT_ID = 100000


def _to_iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def fetch_active_sessions_windowed(client: AgniClient, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Iterate over small windows to collect active sessions."""
    all_records: List[SessionRecord] = []

    current_to = end
    print(f"\n Starting fetch using {WINDOW_MINUTES}-minute windows over {LOOKBACK_HOURS} hours")

    while current_to > start:
        current_from = max(start, current_to - timedelta(minutes=WINDOW_MINUTES))
        print(f"  Fetching active sessions from {current_from.isoformat()} → {current_to.isoformat()}")
        payload = {
            "orgID": org_id,
            "fromTimestamp": _to_iso_z(current_from),
            "toTimestamp": _to_iso_z(current_to),
        }

        try:
            body = client.request("POST", "session.active.list", json=payload)
        except APIRequestError as exc:
            print(f"API request failed for window ending {current_to}: {exc}", file=sys.stderr)
            break

        data = body.get("data", {}) if isinstance(body, dict) else {}
        raw_records: List[Dict[str, Any]] = data.get("records", []) if isinstance(data, dict) else []
        window_records = [SessionRecord.from_dict(rec) for rec in raw_records if isinstance(rec, dict)]

        if window_records:
            print(f"    Retrieved {len(window_records)} records")
            all_records.extend(window_records)
        else:
            print("    No records in this window")

        current_to = current_from
        current_from = current_to - timedelta(minutes=WINDOW_MINUTES)

    return all_records


def filter_by_segment(records: List[SessionRecord], segment_id: int) -> List[SessionRecord]:
    return [rec for rec in records if getattr(rec, "segmentID", None) == segment_id]


def deduplicate_by_mac(records: List[SessionRecord]) -> List[SessionRecord]:
    """Deduplicate records by MAC address, keeping the first occurrence."""
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


def fetch_identities_for_macs(client: AgniClient, org_id: str, macs: List[str]) -> List[Dict[str, Any]]:
    """Fetch identity info for each MAC via identity.client.get."""
    identities: List[Dict[str, Any]] = []
    for mac in macs:
        payload = {"orgID": org_id, "mac": mac}
        try:
            body = client.request("POST", "identity.client.get", json=payload)
        except APIRequestError as exc:
            print(f"Identity lookup failed for MAC {mac}: {exc}", file=sys.stderr)
            continue

        data = body.get("data", {}) if isinstance(body, dict) else {}
        identities.append({"mac": mac, **data} if isinstance(data, dict) else {"mac": mac, "data": data})
    return identities


def fetch_profiles_for_macs(client: AgniClient, org_id: str, macs: List[str]) -> List[Dict[str, Any]]:
    """Fetch identity profile info for each MAC via identity.client.profile.get."""
    profiles: List[Dict[str, Any]] = []
    for mac in macs:
        payload = {"orgID": org_id, "mac": mac}
        try:
            body = client.request("POST", "identity.client.profile.get", json=payload)
        except APIRequestError as exc:
            print(f"Profile lookup failed for MAC {mac}: {exc}", file=sys.stderr)
            continue

        data = body.get("data", {}) if isinstance(body, dict) else {}
        profiles.append({"mac": mac, **data} if isinstance(data, dict) else {"mac": mac, "data": data})
    return profiles


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
        all_records = fetch_active_sessions_windowed(client, org_id=config.org_id, start=start, end=end)
        records = filter_by_segment(all_records, TARGET_SEGMENT_ID)
        records = deduplicate_by_mac(records)
        macs = [rec.mac for rec in records if rec.mac]
        identities = fetch_identities_for_macs(client, config.org_id, macs)
        profiles = fetch_profiles_for_macs(client, config.org_id, macs)
    except AuthenticationError as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    print(f"Active sessions returned: {len(records)}")
    if not records:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"agni_segment_clients_{timestamp}.csv")

    # Compile combined rows (previous per-source CSV outputs removed)
    identity_by_mac = {row.get("mac"): row for row in identities if isinstance(row, dict) and "mac" in row}
    profile_by_mac = {row.get("mac"): row for row in profiles if isinstance(row, dict) and "mac" in row}

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

    combined_rows = []
    for rec in records:
        mac = rec.mac
        identity_data = identity_by_mac.get(mac, {}) if mac else {}
        profile_data = profile_by_mac.get(mac, {}) if mac else {}
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
        combined_rows.append(row)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(combined_rows)

    print(f"Wrote {len(combined_rows)} combined records to {csv_path}")


if __name__ == "__main__":
    main()
