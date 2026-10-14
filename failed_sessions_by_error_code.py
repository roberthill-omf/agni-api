#!/usr/bin/env python3
"""Fetch failed sessions for a chosen error code, deduplicate by MAC, and map each to its switch.

HIGH-LEVEL FLOW
---------------
1. Prompt for the lookback period (hours), the time-window size (minutes) and
   the error code to filter on.
2. Load AGNI credentials/org settings from environment variables.
3. Walk backwards in time from "now" to "now - lookback", querying
   session.list in small windows (newest first). Windows are kept small because
   the API returns at most 1000 records per call; a warning is printed if a
   window hits that cap.
4. Keep one session per client MAC (most recent) and write them to a CSV.
5. For each session: call session.details.get (by authReqID) to read the
   Certificate:SAN, NAS-IP-Address and NAS-Port-Id attributes, then call
   config.nad.get (by the apMAC switch MAC) to get the switch name.
6. Write a second CSV: sessionID, switchName, switchIP, switchIntf, hostname.

Required environment: whatever AgniConfig.from_env() expects (API credentials,
org ID, etc. - see agni_client/config.py).
"""

# --- Standard library imports -------------------------------------------------
import csv  # writing the CSV report
import sys  # stderr output and exit codes
import time  # sleep between API calls (simple rate-limit courtesy)
from dataclasses import asdict  # converts a SessionRecord dataclass -> dict
from datetime import datetime, timedelta, timezone  # time-window math
from pathlib import Path  # filesystem path handling for the CSV
from typing import Dict, List, Optional, Set  # type hints only

# --- Local AGNI client library ------------------------------------------------
# AgniClient     : low-level HTTP client (handles auth token + requests)
# AgniConfig     : holds settings (org_id, credentials, base URL) loaded from env
# SessionAPI     : wrapper around the /session.list endpoint
# SessionFilter  : one server-side filter (field + value) for session.list
# SessionRecord  : dataclass representing one session row returned by the API
from agni_client import (
    AgniClient,
    AgniConfig,
    ConfigNADAPI,
    SessionAPI,
    SessionDetailsAPI,
    SessionFilter,
    SessionRecord,
)
from agni_client.exceptions import APIRequestError, AuthenticationError

# --- Tunable constants --------------------------------------------------------
# Defaults offered at the prompts (press Enter to accept).
# default_lookback_hours: total time to go back when looking for sessions.
DEFAULT_LOOKBACK_HOURS = 4
# default_window_minutes: size of each time slice sent to the API. session.list
# returns at most 1000 records per call, so keep this small enough that a
# single window never reaches that cap.
DEFAULT_WINDOW_MINUTES = 10
# Maximum records session.list returns per call; used to warn about truncation.
API_RECORD_LIMIT = 1000
# Session status to request from the API; only failed (rejected) sessions.
STATUS = "failed"
# Error codes the user can pick from at the prompt. Add more here later.
ERROR_CODES = ["peer_certificate_expired"]
# Prefix for the output CSV file names.
SESSIONS_CSV_PREFIX = "agni_failed_sessions"
SWITCH_MAP_CSV_PREFIX = "agni_failed_session_switches"
# Header of the switch/host mapping CSV.
SWITCH_MAP_FIELDS = ["sessionID", "switchName", "switchIP", "switchIntf", "hostname"]


def prompt_positive_int(label: str, default: int) -> int:
    """Ask for a positive integer; Enter accepts the default, bad input re-prompts."""
    while True:
        answer = input(f"{label} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("  Please enter a positive whole number.")


def prompt_error_code() -> str:
    """Show a numbered list of error codes and return the one the user picks."""
    print("\nError codes available to filter on:")
    for idx, code in enumerate(ERROR_CODES, start=1):
        print(f"  {idx}. {code}")
    while True:
        answer = input("Choose an error code [1]: ").strip()
        if not answer:
            return ERROR_CODES[0]
        if answer.isdigit() and 1 <= int(answer) <= len(ERROR_CODES):
            return ERROR_CODES[int(answer) - 1]
        print(f"  Please enter a number between 1 and {len(ERROR_CODES)}.")


def fetch_failed_sessions(
    api: SessionAPI, org_id: str, start: datetime, end: datetime, window_minutes: int, error_code: str
) -> List[SessionRecord]:
    """Iterate over window_minutes-sized windows and collect failed sessions with the target error.

    Works from `end` backwards to `start`, so the returned list is ordered
    roughly newest -> oldest (deduplicate_by_mac relies on this ordering).
    """
    # Accumulates the session records from every window.
    all_records: List[SessionRecord] = []
    # Upper bound of the window currently being queried; starts at the newest
    # point in time and moves backwards on each loop pass.
    current_to = end

    print(f"\nStarting fetch using {window_minutes}-minute windows over {end - start}")

    # Keep stepping back until the window's upper bound reaches the start time.
    while current_to > start:
        # Lower bound = upper bound minus the window size, but never earlier
        # than `start` (clamps the final, possibly shorter, window).
        current_from = max(start, current_to - timedelta(minutes=window_minutes))
        print(f"  Fetching sessions from {current_from.isoformat()} → {current_to.isoformat()}")

        # One API call for this window. Filtering is done server-side:
        #   - status=STATUS            -> only failed sessions
        #   - SessionFilter(error=...) -> only sessions whose error matches
        #                                 error_code
        result = api.list_sessions(
            org_id=org_id,
            status=STATUS,
            from_ts=current_from,
            to_ts=current_to,
            filters=[SessionFilter(field="error", value=error_code)],
        )

        # Report and collect whatever this window returned.
        if not result.records:
            print("    No records in this window.")
        else:
            print(f"    Retrieved {len(result.records)} records")
            if len(result.records) >= API_RECORD_LIMIT:
                print(
                    f"    WARNING: window returned the {API_RECORD_LIMIT}-record API cap; results may be "
                    "truncated. Re-run with a smaller window size."
                )
            all_records.extend(result.records)

        # Slide the window back: this window's lower bound becomes the next
        # window's upper bound, so windows tile the range with no gaps/overlap.
        current_to = current_from
        time.sleep(0.2)  # small delay to avoid hitting API rate limits

    return all_records


def deduplicate_by_mac(records: List[SessionRecord]) -> List[SessionRecord]:
    """Keep the first occurrence of each MAC (records are gathered newest to oldest).

    Because the input is newest-first, "first occurrence" means the most
    recent failed session for each device.
    """
    # MAC addresses already kept; a set gives O(1) membership checks.
    seen: Set[str] = set()
    # Output list, preserving the original (newest-first) order.
    deduped: List[SessionRecord] = []

    for rec in records:
        # getattr with a default so records lacking a `mac` attribute don't crash.
        mac = getattr(rec, "mac", None)

        # No MAC to compare on -> can't tell if it's a duplicate, so keep it.
        if mac is None:
            deduped.append(rec)
            continue

        # MAC already kept from a newer session -> drop this older one.
        if mac in seen:
            continue

        # First time seeing this MAC: remember it and keep the record.
        seen.add(mac)
        deduped.append(rec)

    return deduped


def write_csv(records: List[SessionRecord], *, prefix: str) -> Path:
    """Write the records to a timestamped CSV and return its path.

    `prefix` is keyword-only (note the `*`) to keep call sites explicit.
    """
    # Local-time timestamp makes each run's file name unique, so earlier
    # output is never overwritten.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Relative path -> file lands in the directory the script is run from.
    csv_path = Path(f"{prefix}_{timestamp}.csv")

    # Convert each dataclass instance into a plain dict (column -> value).
    record_dicts = [asdict(rec) for rec in records]
    # Build the header from the UNION of keys across all records (in case some
    # records have fields others lack), sorted alphabetically for stable columns.
    # DictWriter fills missing values with empty strings.
    fieldnames = sorted({key for rec in record_dicts for key in rec.keys()})

    # newline="" is required by the csv module to avoid blank lines on Windows;
    # utf-8 keeps non-ASCII characters intact.
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()  # first row: column names
        writer.writerows(record_dicts)  # one row per session

    return csv_path


def build_switch_rows(
    records: List[SessionRecord],
    details_api: SessionDetailsAPI,
    nad_api: ConfigNADAPI,
    org_id: str,
) -> List[Dict[str, str]]:
    """Build one switch/host row per session by calling session.details.get and config.nad.get."""
    rows: List[Dict[str, str]] = []
    # Many sessions share a switch, so cache name lookups by switch MAC to
    # avoid repeating identical config.nad.get calls.
    switch_names: Dict[str, str] = {}

    for idx, rec in enumerate(records, start=1):
        print(f"  [{idx}/{len(records)}] session {rec.authReqID}")
        if not rec.authReqID:
            print("    Skipping: record has no authReqID.")
            continue

        # session.details.get -> NAS IP, NAS port (switch interface), cert SAN (hostname).
        try:
            details = details_api.get_session_details(org_id=org_id, auth_req_id=rec.authReqID)
        except (AuthenticationError, APIRequestError) as exc:
            print(f"    session.details.get failed: {exc}")
            continue
        time.sleep(0.2)  # small delay to avoid hitting API rate limits

        # config.nad.get -> switch name, using the switch MAC from the apMAC column.
        switch_mac = rec.apMAC
        switch_name = ""
        if switch_mac:
            if switch_mac not in switch_names:
                try:
                    switch_names[switch_mac] = nad_api.get_nad(org_id=org_id, mac=switch_mac).name or ""
                except (AuthenticationError, APIRequestError) as exc:
                    print(f"    config.nad.get failed for {switch_mac}: {exc}")
                    switch_names[switch_mac] = ""
                time.sleep(0.2)
            switch_name = switch_names[switch_mac]

        rows.append(
            {
                "sessionID": rec.authReqID,
                "switchName": switch_name,
                "switchIP": details.nas_ip or "",
                # A certificate can carry several SANs; join them with ";".
                "switchIntf": details.nas_port_id or "",
                "hostname": ";".join(details.certificate_san),
            }
        )

    return rows


def write_switch_csv(rows: List[Dict[str, str]], *, prefix: str) -> Path:
    """Write the switch/host rows to a timestamped CSV and return its path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"{prefix}_{timestamp}.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SWITCH_MAP_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def main() -> None:
    """Script entry point: prompt, call the APIs, dedupe, write both CSVs."""
    # Ask the user for the run parameters; Enter accepts the defaults.
    hours = prompt_positive_int("Lookback period in hours", DEFAULT_LOOKBACK_HOURS)
    window_minutes = prompt_positive_int("Time window size in minutes", DEFAULT_WINDOW_MINUTES)
    error_code = prompt_error_code()

    # Load settings (org ID, credentials, etc.) from environment variables.
    # A ValueError means something required is missing or malformed.
    try:
        config = AgniConfig.from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Build the HTTP client (60-second per-request timeout) and the API wrappers.
    client = AgniClient(config, timeout=60)
    api = SessionAPI(client)
    details_api = SessionDetailsAPI(client)
    nad_api = ConfigNADAPI(client)

    # Define the overall search range in UTC: from `hours` ago up to now.
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    try:
        # Step 1: list failed sessions with the chosen error.
        records = fetch_failed_sessions(
            api, org_id=config.org_id, start=start, end=end, window_minutes=window_minutes, error_code=error_code
        )

        # Nothing matched: not an error, so exit with success and skip the CSVs.
        if not records:
            print("No records returned.")
            sys.exit(0)

        # Reduce to one record per MAC (most recent session wins).
        deduped_records = deduplicate_by_mac(records)
        print(f"Deduplicated from {len(records)} down to {len(deduped_records)} records based on MAC address.")

        # Save the de-duplicated session list.
        try:
            csv_path = write_csv(deduped_records, prefix=SESSIONS_CSV_PREFIX)
        except OSError as exc:
            print(f"Failed to write CSV: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Wrote {len(deduped_records)} records to {csv_path}")

        # Step 2: enrich each session with switch/host details.
        print(f"\nLooking up session details and switch names for {len(deduped_records)} sessions")
        rows = build_switch_rows(deduped_records, details_api, nad_api, config.org_id)
    except (AuthenticationError, APIRequestError) as exc:
        print(f"API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    try:
        switch_csv_path = write_switch_csv(rows, prefix=SWITCH_MAP_CSV_PREFIX)
    except OSError as exc:
        print(f"Failed to write CSV: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(rows)} switch/host rows to {switch_csv_path}")


# Run main() only when executed as a script (not when imported as a module).
if __name__ == "__main__":
    main()
