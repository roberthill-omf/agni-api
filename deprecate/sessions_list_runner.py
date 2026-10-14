#!/usr/bin/env python3
"""Simple script to invoke session.list with configurable options and save to CSV."""

import csv
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agni_client import AgniClient, AgniConfig, SessionAPI
from agni_client.exceptions import AgniError
from agni_client.sessions_list import SessionFilter, SessionRecord

WINDOW_MINUTES_DEFAULT = 30
LOOKBACK_HOURS_DEFAULT = 6
TARGET_SEGMENT_ID = 100000

OPTIONS_FILE = Path("session_list_options.txt")


def ensure_options_file() -> None:
    """Create a commented options file if it does not exist."""
    if OPTIONS_FILE.exists():
        return

    template = """# Uncomment and set any of these options to use them in the request.
# cursor=string
# filters=[{"field":"identity","value":"alice"}]
# fromTimestamp=2025-11-28T16:23:45.415Z
# toTimestamp=2025-11-28T17:23:45.415Z
# limit=100
# location=HQ
# onPremNodeID=node-1
# orgID=override-org
# query=search text
# sessionType=wireless
# status=failed
# zoneID=1
# windowMinutes=30
# lookbackHours=6
"""
    OPTIONS_FILE.write_text(template, encoding="utf-8")


def parse_value(raw: str) -> Any:
    """Attempt to parse a value from string to int/list/dict via JSON, else return raw string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    if raw.isdigit():
        return int(raw)
    return raw


def parse_datetime(value: Any) -> datetime:
    """Convert datetime or ISO8601 string (with optional Z) to datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        iso_value = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(iso_value)
    raise ValueError(f"Unsupported datetime value: {value}")


def load_options() -> Dict[str, Any]:
    """Load options from the text file, ignoring comments and blanks."""
    ensure_options_file()
    options: Dict[str, Any] = {}

    for line in OPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        options[key.strip()] = parse_value(raw_value.strip())

    return options


def fetch_sessions_windowed(
    api: SessionAPI,
    *,
    org_id: str,
    start: datetime,
    end: datetime,
    window_minutes: int,
    status: Optional[str],
    session_type: Optional[str],
    location: Optional[str],
    on_prem_node_id: Optional[str],
    zone_id: Optional[int],
    query: Optional[str],
    filters: Optional[List[SessionFilter]],
    limit: Optional[int],
) -> List[SessionRecord]:
    all_records: List[SessionRecord] = []

    current_to = end
    current_from = current_to - timedelta(minutes=window_minutes)
    print(f"\n Starting fetch using {window_minutes}-minute windows")

    while current_from >= start:
        print(f"\n Fetching sessions from {current_from.isoformat()} → {current_to.isoformat()}")

        result = api.list_sessions(
            org_id=org_id,
            from_ts=current_from,
            to_ts=current_to,
            status=status,
            session_type=session_type,
            location=location,
            on_prem_node_id=on_prem_node_id,
            zone_id=zone_id,
            query=query,
            filters=filters,
            limit=limit,
        )

        if not result.records:
            print("No records returned for this time window.")
        else:
            print(f"  > Retrieved {len(result.records)} records")
            all_records.extend(result.records)

        current_to = current_from
        current_from = current_to - timedelta(minutes=window_minutes)
        time.sleep(0.2)  # small delay to avoid API rate limits

    return all_records


def filter_by_segment(records: List[SessionRecord], segment_id: int) -> List[SessionRecord]:
    """Return only records matching the desired segment ID."""
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


def main() -> None:
    config = AgniConfig.from_env()
    client = AgniClient(config)
    api = SessionAPI(client)

    try:
        opts = load_options()
        filters: Optional[List[SessionFilter]] = None
        if "filters" in opts and isinstance(opts["filters"], list):
            filters = []
            for item in opts["filters"]:
                if isinstance(item, dict) and "field" in item and "value" in item:
                    filters.append(SessionFilter(field=item["field"], value=item["value"]))

        # Determine time window
        end_ts_raw = opts.get("toTimestamp") or datetime.now(timezone.utc)
        start_ts_raw = opts.get("fromTimestamp")
        if not start_ts_raw:
            lookback_hours = opts.get("lookbackHours", LOOKBACK_HOURS_DEFAULT)
            start_ts_raw = datetime.now(timezone.utc) - timedelta(hours=int(lookback_hours))

        window_minutes = int(opts.get("windowMinutes", WINDOW_MINUTES_DEFAULT))

        all_records = fetch_sessions_windowed(
            api,
            org_id=opts.get("orgID", config.org_id),
            start=parse_datetime(start_ts_raw),
            end=parse_datetime(end_ts_raw),
            window_minutes=window_minutes,
            status=opts.get("status"),
            session_type=opts.get("sessionType"),
            location=opts.get("location"),
            on_prem_node_id=opts.get("onPremNodeID"),
            zone_id=opts.get("zoneID"),
            query=opts.get("query"),
            filters=filters,
            limit=opts.get("limit"),
        )
        records = filter_by_segment(all_records, TARGET_SEGMENT_ID)
        records = deduplicate_by_mac(records)
    except AgniError as exc:
        print(f"API call failed: {exc}")
        return
    finally:
        client.close()

    if not records:
        print("No records returned for the requested segment.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"agni_session_list_{timestamp}.csv")

    rows = [asdict(rec) for rec in records]
    fieldnames = sorted({key for row in rows for key in row.keys()})

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} records to {csv_path}")


if __name__ == "__main__":
    main()
