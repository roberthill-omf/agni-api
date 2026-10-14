#!/usr/bin/env python3
"""Fetch active sessions and save to CSV."""

import csv
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agni_client import AgniClient, AgniConfig
from agni_client.exceptions import APIRequestError, AuthenticationError
from agni_client.sessions_list import SessionRecord

LOOKBACK_HOURS = 6
WINDOW_MINUTES = 30
TARGET_SEGMENT_ID = 100000


def _to_iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def fetch_active_sessions(client: AgniClient, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Iterate over time windows to gather active sessions."""
    records: List[SessionRecord] = []
    current_to = end
    print(f"\nStarting fetch using {WINDOW_MINUTES}-minute windows over {LOOKBACK_HOURS} hours")

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
            records.extend(window_records)
        else:
            print("    No records in this window")

        current_to = current_from

    return records


def filter_by_segment(records: List[SessionRecord], segment_id: int) -> List[SessionRecord]:
    return [rec for rec in records if getattr(rec, "segmentID", None) == segment_id]


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
        profile_entry: Dict[str, Any] = {"mac": mac, **data} if isinstance(data, dict) else {"mac": mac, "data": data}

        fingerprints = data.get("fingerprints", {}) if isinstance(data, dict) else {}
        if isinstance(fingerprints, dict):
            dhcp_opt = fingerprints.get("dhcp:option12")
            mac_vendor = fingerprints.get("host:macVendor")

            if isinstance(dhcp_opt, list) and dhcp_opt:
                profile_entry["dhcp_option12"] = dhcp_opt[0]
            elif isinstance(dhcp_opt, str):
                profile_entry["dhcp_option12"] = dhcp_opt

            if isinstance(mac_vendor, list) and mac_vendor:
                profile_entry["mac_vendor"] = mac_vendor[0]
            elif isinstance(mac_vendor, str):
                profile_entry["mac_vendor"] = mac_vendor

        profiles.append(profile_entry)
    return profiles


def write_csv(records: List[SessionRecord], *, prefix: str = "agni_session_active", timestamp: Optional[str] = None) -> Path:
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"{prefix}_{timestamp}.csv")
    rows = [asdict(rec) for rec in records]
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


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
        records = fetch_active_sessions(client, config.org_id, start, end)
        records = filter_by_segment(records, TARGET_SEGMENT_ID)
        records = deduplicate_by_mac(records)
        macs = [rec.mac for rec in records if rec.mac]
        identities = fetch_identities_for_macs(client, config.org_id, macs)
        profiles = fetch_profiles_for_macs(client, config.org_id, macs)
    except AuthenticationError as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if not records:
        print("No records returned.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = write_csv(records, timestamp=timestamp)
    print(f"Wrote {len(records)} records to {csv_path}")

    if identities:
        id_fieldnames = sorted({key for row in identities for key in row.keys()})
        id_csv_path = Path(f"agni_identity_clients_{timestamp}.csv")
        with id_csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=id_fieldnames)
            writer.writeheader()
            writer.writerows(identities)
        print(f"Wrote {len(identities)} identity records to {id_csv_path}")

    if profiles:
        profile_fieldnames = sorted({key for row in profiles for key in row.keys()})
        profile_csv_path = Path(f"agni_identity_profiles_{timestamp}.csv")
        with profile_csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=profile_fieldnames)
            writer.writeheader()
            writer.writerows(profiles)
        print(f"Wrote {len(profiles)} profile records to {profile_csv_path}")


if __name__ == "__main__":
    main()
