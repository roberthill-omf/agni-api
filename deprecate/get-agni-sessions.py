#!/usr/bin/env python3
"""Example script demonstrating the modular AGNI client."""

import csv
from dataclasses import asdict
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List

from agni_client import AgniClient, AgniConfig, SessionAPI
from agni_client.sessions_list import SessionRecord
from agni_client.exceptions import APIRequestError, AuthenticationError

WINDOW_MINUTES = 30  # tweak if you routinely hit the 1000-record limit
LOOKBACK_HOURS = 6
TIMEOUT = 60
STATUS = "failed"


def fetch_failed_sessions(api: SessionAPI, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Iterate over time windows and collect session records."""
    all_records: List[SessionRecord] = []

    current_to = end
    current_from = current_to - timedelta(minutes=WINDOW_MINUTES)
    print(f"\n Starting fetch using {WINDOW_MINUTES}-minute windows")

    while current_from >= start:
        print(f"\n Fetching sessions from {current_from.isoformat()} → {current_to.isoformat()}")

        result = api.list_sessions(
            org_id=org_id,
            status=STATUS,
            from_ts=current_from,
            to_ts=current_to,
        )

        if not result.records:
            print("No records returned for this time window.")
        else:
            print(f"  > Retrieved {len(result.records)} records")
            all_records.extend(result.records)

        current_to = current_from
        current_from = current_to - timedelta(minutes=WINDOW_MINUTES)
        time.sleep(0.2)  # small delay to avoid API rate limits

    return all_records


def write_csv(records: List[SessionRecord]) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"agni_session_list_{timestamp}.csv"
    record_dicts = [asdict(rec) for rec in records]
    all_keys = sorted({k for rec in record_dicts for k in rec.keys()})

    with open(csv_file, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_keys)
        writer.writeheader()
        writer.writerows(record_dicts)

    return csv_file


def main() -> None:
    try:
        config = AgniConfig.from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    client = AgniClient(config, timeout=TIMEOUT)
    api = SessionAPI(client)

    start = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    end = datetime.now(timezone.utc)

    try:
        records = fetch_failed_sessions(api, org_id=config.org_id, start=start, end=end)
    except (AuthenticationError, APIRequestError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if not records:
        print("No records to export.")
        sys.exit(0)

    try:
        csv_file = write_csv(records)
    except OSError as exc:
        print(f"Failed to write CSV: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n Total records collected: {len(records)}")
    print(f"Wrote {len(records)} records to {csv_file}")


if __name__ == "__main__":
    main()
