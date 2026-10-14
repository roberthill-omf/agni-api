# Failed Sessions by Error Code

Fetch failed AGNI sessions for a specific error code using 30-minute time windows, deduplicate by MAC address, and export to CSV.

## Prerequisites
- Python environment with the project dependencies installed.
- Environment variables set: `AGNI_ORG_ID`, `KEY_ID`, `KEY_VALUE`.

## Usage
```
python agni_failed_sessions_by_error_code.py [--hours N] [--output-prefix PREFIX]
```
- `--hours`: Lookback window in hours (default: 8). The script walks backward in 30-minute slices.
- `--output-prefix`: Prefix for the output CSV file (default: `agni_failed_sessions`).

## Changing the error code
Open `agni_failed_sessions_by_error_code.py` and set:
```python
TARGET_ERROR_CODE = "peer_certificate_expired"
```
Replace the string with any error value you want to filter on (for example, `"eap_invalid_desired_auth_type"`).

## How it works
- Builds 30-minute windows from `now` back to `now - hours`.
- Calls `session.list` with `status="failed"` and a filter on `TARGET_ERROR_CODE`.
- Deduplicates the combined results by MAC address (first occurrence wins).
- Writes the deduped records to `PREFIX_YYYYMMDD_HHMMSS.csv`.
