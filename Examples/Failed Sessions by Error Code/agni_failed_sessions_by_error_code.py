#!/usr/bin/env python3
"""Fetch failed sessions for a specific error code and deduplicate by MAC."""

import argparse
import csv
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set

from agni_client import AgniClient, AgniConfig, SessionAPI, SessionFilter, SessionRecord
from agni_client.exceptions import APIRequestError, AuthenticationError

WINDOW_MINUTES = 30
DEFAULT_LOOKBACK_HOURS = 8
STATUS = "failed"
# Set this to the error string you want to filter on (e.g., "eap_invalid_desired_auth_type").
TARGET_ERROR_CODE = "peer_certificate_expired"


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch failed sessions for a specific error code.")
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_LOOKBACK_HOURS,
        help=f"Lookback period in hours (default: {DEFAULT_LOOKBACK_HOURS})",
    )
    parser.add_argument(
        "--output-prefix",
        default="agni_failed_sessions",
        help="Filename prefix for the CSV output",
    )
    return parser.parse_args(argv)


def fetch_failed_sessions(api: SessionAPI, org_id: str, start: datetime, end: datetime) -> List[SessionRecord]:
    """Iterate over 30-minute windows and collect failed sessions with the target error."""
    all_records: List[SessionRecord] = []
    current_to = end

    print(f"\nStarting fetch using {WINDOW_MINUTES}-minute windows over {end - start}")

    while current_to > start:
        current_from = max(start, current_to - timedelta(minutes=WINDOW_MINUTES))
        print(f"  Fetching sessions from {current_from.isoformat()} → {current_to.isoformat()}")

        result = api.list_sessions(
            org_id=org_id,
            status=STATUS,
            from_ts=current_from,
            to_ts=current_to,
            filters=[SessionFilter(field="error", value=TARGET_ERROR_CODE)],
        )

        if not result.records:
            print("    No records in this window.")
        else:
            print(f"    Retrieved {len(result.records)} records")
            all_records.extend(result.records)

        current_to = current_from
        time.sleep(0.2)  # small delay to avoid hitting API rate limits

    return all_records


def deduplicate_by_mac(records: List[SessionRecord]) -> List[SessionRecord]:
    """Keep the first occurrence of each MAC (records are gathered newest to oldest)."""
    seen: Set[str] = set()
    deduped: List[SessionRecord] = []

    for rec in records:
        mac = getattr(rec, "mac", None)
        if mac is None:
            deduped.append(rec)
            continue
        if mac in seen:
            continue
        seen.add(mac)
        deduped.append(rec)

    return deduped


def write_csv(records: List[SessionRecord], *, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"{prefix}_{timestamp}.csv")
    record_dicts = [asdict(rec) for rec in records]
    fieldnames = sorted({key for rec in record_dicts for key in rec.keys()})

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(record_dicts)

    return csv_path


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv or [])

    if args.hours <= 0:
        print("Lookback hours must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    try:
        config = AgniConfig.from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    client = AgniClient(config, timeout=60)
    api = SessionAPI(client)

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)

    try:
        records = fetch_failed_sessions(api, org_id=config.org_id, start=start, end=end)
    except (AuthenticationError, APIRequestError) as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if not records:
        print("No records returned.")
        sys.exit(0)

    deduped_records = deduplicate_by_mac(records)
    print(f"Deduplicated from {len(records)} down to {len(deduped_records)} records based on MAC address.")

    try:
        csv_path = write_csv(deduped_records, prefix=args.output_prefix)
    except OSError as exc:
        print(f"Failed to write CSV: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(deduped_records)} records to {csv_path}")


if __name__ == "__main__":
    main()
