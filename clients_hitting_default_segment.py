#!/usr/bin/env python3
"""Fetch session list data in windowed chunks and save to CSV."""

import argparse
import csv
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from agni_client import AgniClient, AgniConfig, SessionAPI
from agni_client.exceptions import APIRequestError, AuthenticationError
from agni_client.sessions_list import SessionRecord

WINDOW_MINUTES = 10
DEFAULT_LOOKBACK_HOURS = 8
STATUS = None  # set to a value like "failed" if you want to filter
TARGET_SEGMENT_ID = 100000


def fetch_sessions_windowed(api: SessionAPI, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Iterate over WINDOW_MINUTES slices from end back to start, collecting records."""
    all_records: List[SessionRecord] = []

    current_to = end
    print(f"\nStarting fetch using {WINDOW_MINUTES}-minute windows over {DEFAULT_LOOKBACK_HOURS} hours")

    while current_to > start:
        current_from = max(start, current_to - timedelta(minutes=WINDOW_MINUTES))
        print(f"  Fetching {current_from.isoformat()} → {current_to.isoformat()}")

        result = api.list_sessions(
            org_id=org_id,
            from_ts=current_from,
            to_ts=current_to,
            status=STATUS,
        )

        if result.records:
            print(f"    Retrieved {len(result.records)} records")
            all_records.extend(result.records)
        else:
            print("    No records in this window")

        current_to = current_from
        time.sleep(0.2)  # small delay to avoid rate limits

    return all_records


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


def write_csv(records: List[SessionRecord], prefix: str = "default_profile_sessions/agni_default_session_clients") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"{prefix}_{timestamp}.csv")
    rows = [asdict(rec) for rec in records]
    fieldnames = sorted({key for row in rows for key in row.keys()})

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch AGNI sessions and save to CSV.")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=DEFAULT_LOOKBACK_HOURS,
        help="How many hours back to search (default: 8).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        config = AgniConfig.from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    client = AgniClient(config)
    api = SessionAPI(client)

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.lookback_hours)

    try:
        records = fetch_sessions_windowed(api, org_id=config.org_id, start=start, end=end)
        records = filter_by_segment(records, TARGET_SEGMENT_ID)
        records = deduplicate_by_mac(records)
    except (AuthenticationError, APIRequestError) as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if not records:
        print("No records returned.")
        return

    csv_path = write_csv(records)
    print(f"Wrote {len(records)} records to {csv_path}")


if __name__ == "__main__":
    main()
