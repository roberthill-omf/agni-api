# agni-api

Python scripts and a small client library for calling Arista's **AGNI** (Cloud NAC) REST API, built for reporting and troubleshooting that the built-in dashboards and search don't cover well.

Everything here is read-only: the scripts query AGNI and write results to CSV files. Nothing is changed in AGNI.

## Contents

| Path | What it is |
| --- | --- |
| [failed_sessions_by_error_code.py](failed_sessions_by_error_code.py) | Interactive script: find failed sessions for an error code, then map each one to its switch, port and host name. |
| [clients_hitting_default_segment.py](clients_hitting_default_segment.py) | Find clients whose sessions landed in a specific segment (default segment ID `100000`) over a lookback period. |
| [fetch_active_sessions.py](fetch_active_sessions.py) | Pull active sessions for a segment, then look up identity and profile data for each client MAC. |
| [agni_client/](agni_client/) | Reusable library: authentication, HTTP client and wrappers for the endpoints used above. |
| [requirements.txt](requirements.txt) | Python dependencies (`requests`, `python-dotenv`). |
| [Examples/](Examples/) | Older standalone example with its own README (see the note under [Known issues](#known-issues)). |
| [deprecate/](deprecate/) | Retired scripts and old output kept for reference. Not maintained. |

## Requirements

- Python 3.8 or newer
- An AGNI API key (a **Key ID** and **Key Value**) and your AGNI **Org ID**
- Network access to your AGNI cluster (default `ag01c01.agni.arista.io`)

## Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd agni-api

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Credentials

The scripts read three environment variables. The easiest way is a `.env` file in the repo root (already git-ignored, so it won't be committed):

```
AGNI_ORG_ID=your-org-id
KEY_ID=your-api-key-id
KEY_VALUE=your-api-key-value
```

You can also export them in your shell instead. If any are missing, the script prints which ones and exits.

> **Keep the key value secret.** Never commit it or paste it into tickets or chat.

### Using a different AGNI cluster

The base URLs default to `https://ag01c01.agni.arista.io` and are set in [agni_client/config.py](agni_client/config.py) (`api_base` and `auth_url`). If your tenant lives on another cluster, change those two values. They are not currently read from the environment.

Run every script from the repo root so that `import agni_client` resolves.

---

## Script 1: Failed sessions by error code

[failed_sessions_by_error_code.py](failed_sessions_by_error_code.py)

Answers the question: *"Which devices are failing authentication with error X, and which switch port are they connected to?"*

```bash
python failed_sessions_by_error_code.py
```

### Prompts

The script asks three questions when it starts. Press **Enter** to accept the default shown in brackets.

| Prompt | Default | Meaning |
| --- | --- | --- |
| Lookback period in hours | `4` | How far back from now to search. |
| Time window size in minutes | `10` | Size of each slice sent to the API (see below). |
| Choose an error code | `1` | Pick from a numbered list of error codes to filter on. Currently only `peer_certificate_expired`. |

Example session:

```
Lookback period in hours [4]: 8
Time window size in minutes [10]: 5

Error codes available to filter on:
  1. peer_certificate_expired
Choose an error code [1]:
```

### Why the window size matters

`session.list` returns **at most 1000 records per call**. The script therefore walks backward from now in small time windows rather than asking for the whole period at once. If a window returns 1000 records, results for that window are probably truncated and the script prints:

```
WARNING: window returned the 1000-record API cap; results may be truncated. Re-run with a smaller window size.
```

If you see this, re-run with a smaller window (for example `2` or `1` minutes). Smaller windows mean more API calls, so a long lookback with a tiny window will take longer.

### What it does

1. Calls `session.list` for each window with `status = failed` and a filter on the chosen error code.
2. **Deduplicates by client MAC**, keeping the most recent session for each device.
3. Writes the deduplicated sessions to `agni_failed_sessions_<YYYYMMDD_HHMMSS>.csv` (all session fields).
4. For each remaining session:
   - Calls `session.details.get` with the session's `authReqID` and your org ID, and reads:
     - `Radius:IETF:NAS-IP-Address` (switch IP)
     - `Radius:IETF:NAS-Port-Id` (switch interface)
     - `Certificate:SAN` (host name on the certificate)
   - Calls `config.nad.get` with the switch MAC (the session's `apMAC`) and your org ID to get the switch **name**. Names are cached per switch MAC, so each switch is looked up only once.
5. Writes `agni_failed_session_switches_<YYYYMMDD_HHMMSS>.csv`.

### Output files

Both files are written to the current directory.

**`agni_failed_sessions_<timestamp>.csv`**: one row per unique client MAC, with every field returned by `session.list` (`authReqID`, `mac`, `apMAC`, `error`, `createdAt`, `nadName`, and so on).

**`agni_failed_session_switches_<timestamp>.csv`**:

| Column | Source |
| --- | --- |
| `sessionID` | `authReqID` from `session.list` |
| `switchName` | `name` from `config.nad.get` |
| `switchIP` | `Radius:IETF:NAS-IP-Address` from `session.details.get` |
| `switchIntf` | `Radius:IETF:NAS-Port-Id` from `session.details.get` |
| `hostname` | `Certificate:SAN` from `session.details.get` (multiple SANs are joined with `;`) |

### Behavior to know about

- If `session.details.get` fails for a session, that session is skipped (and the error is printed). If only `config.nad.get` fails, the row is still written with an empty `switchName`.
- If no sessions match, the script prints `No records returned.` and exits without writing files.
- Exit codes: `0` on success or no results, `1` on configuration, API, or file-write errors.

### Adding more error codes

Add the AGNI error string to the `ERROR_CODES` list near the top of the script:

```python
ERROR_CODES = ["peer_certificate_expired", "eap_invalid_desired_auth_type"]
```

It will appear in the numbered menu automatically.

### Changing the defaults

At the top of the script:

```python
DEFAULT_LOOKBACK_HOURS = 4    # offered at the lookback prompt
DEFAULT_WINDOW_MINUTES = 10   # offered at the window-size prompt
```

---

## Script 2: Clients hitting the default segment

[clients_hitting_default_segment.py](clients_hitting_default_segment.py)

Lists clients whose sessions were placed in a particular segment (by default segment ID `100000`, used here as the default/catch-all segment).

```bash
python clients_hitting_default_segment.py [--lookback-hours N]
```

- `--lookback-hours` (default `8`): how far back to search, in 10-minute windows.
- Collects all sessions, keeps those whose `segmentID` matches `TARGET_SEGMENT_ID`, deduplicates by MAC, and writes `default_profile_sessions/agni_default_session_clients_<timestamp>.csv`.
- **Create the `default_profile_sessions/` directory first** if it does not exist, or the write will fail.
- To target another segment, edit `TARGET_SEGMENT_ID` in the script. To restrict by session status, set `STATUS` (for example `"failed"`).
- This script does not warn about the 1000-record cap; reduce `WINDOW_MINUTES` in the script if your org is busy.

---

## Script 3: Active sessions with identity and profile data

[fetch_active_sessions.py](fetch_active_sessions.py)

```bash
python fetch_active_sessions.py
```

This script takes no arguments. Settings are constants at the top of the file:

| Constant | Default | Meaning |
| --- | --- | --- |
| `LOOKBACK_HOURS` | `6` | Total time to search. |
| `WINDOW_MINUTES` | `30` | Size of each `session.active.list` window. |
| `TARGET_SEGMENT_ID` | `100000` | Only sessions in this segment are kept. |

It filters to the target segment, deduplicates by MAC, then calls `identity.client.get` and `identity.client.profile.get` for each MAC. It writes up to three files in the current directory, sharing one timestamp:

- `agni_session_active_<timestamp>.csv`: the active sessions
- `agni_identity_clients_<timestamp>.csv`: identity data per MAC
- `agni_identity_profiles_<timestamp>.csv`: profile data per MAC, including the DHCP option 12 hostname (`dhcp_option12`) and `mac_vendor` when available

---

## The `agni_client` library

You can reuse the library in your own scripts.

```python
from agni_client import AgniClient, AgniConfig, SessionAPI, SessionDetailsAPI, ConfigNADAPI

config = AgniConfig.from_env()          # reads .env / environment
client = AgniClient(config, timeout=60) # logs in lazily on first request

try:
    details = SessionDetailsAPI(client).get_session_details(
        org_id=config.org_id, auth_req_id="Rdb7o2ig4eroc72uv7lag"
    )
    print(details.nas_ip, details.nas_port_id, details.certificate_san)

    nad = ConfigNADAPI(client).get_nad(org_id=config.org_id, mac="c06911cde8b5")
    print(nad.name)
finally:
    client.close()
```

| Module | Provides |
| --- | --- |
| [config.py](agni_client/config.py) | `AgniConfig`: credentials, org ID and base URLs, loaded with `from_env()`. |
| [client.py](agni_client/client.py) | `AgniClient`: authenticates with the key pair, then `request(method, path, json=...)`. Raises on HTTP errors and on a non-empty `error` field in the response body. |
| [sessions_list.py](agni_client/sessions_list.py) | `SessionAPI.list_sessions(...)` for `session.list`, plus `SessionFilter` and `SessionRecord`. |
| [session_details.py](agni_client/session_details.py) | `SessionDetailsAPI.get_session_details(...)` for `session.details.get`. |
| [config_nad.py](agni_client/config_nad.py) | `ConfigNADAPI.get_nad(...)` for `config.nad.get` and `add_nad(...)` for `config.nad.add`. |
| [exceptions.py](agni_client/exceptions.py) | `AuthenticationError` and `APIRequestError` (both subclass `AgniError`). |

For any endpoint without a wrapper, call `client.request("POST", "<endpoint>", json={...})` directly.

> `ConfigNADAPI.add_nad` **creates** devices in AGNI. None of the scripts in this repo call it.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `Missing required environment variables: ...` | Set `AGNI_ORG_ID`, `KEY_ID` and `KEY_VALUE` in `.env` or your shell. |
| `Login failed: ...` | Wrong key ID or value, expired key, or the cluster URL in `config.py` doesn't match your tenant. |
| `API call failed: ... API returned an error: ...` | AGNI rejected the request; the message after `error:` comes from the API response. |
| `ModuleNotFoundError: agni_client` | Run from the repo root, with the virtual environment active. |
| Truncation warning (1000-record cap) | Re-run with a smaller window size. |
| A script is slow | Each window and each per-session lookup is an API call with a short pause (0.2s) to avoid rate limits. Reduce the lookback or increase the window size, mindful of the 1000 cap. |

## Known issues

- [Examples/Failed Sessions by Error Code/](Examples/Failed%20Sessions%20by%20Error%20Code/) holds an earlier version of the failed-sessions script and its README, which describe `--hours` and 30-minute windows. They don't match the current [failed_sessions_by_error_code.py](failed_sessions_by_error_code.py).
- Output files are written to the current directory, and `*.csv` is git-ignored so reports (which contain MAC addresses and identities) aren't committed by accident.

## License

See [LICENSE](LICENSE).
