"""Source-level rules from the one-time focus correction."""

from __future__ import annotations

from datetime import UTC, datetime
import re
import time

import football_daily as daily
import football_store as store


REMOTE_TIMEOUT = 35

# The template returns only the last-write time of a unique metadata entry.
METADATA_COMMAND = r"""
$ErrorActionPreference = 'Stop'
try {
    if ($env:COMPUTERNAME -ine '__PINNED_HOST__') {
        [Console]::Write('{"state":"route_unavailable"}'); exit 0
    }
    $activeRoot = $env:HERMES_HOME
    if ([string]::IsNullOrWhiteSpace($activeRoot) -or ![IO.Path]::IsPathRooted($activeRoot)) {
        [Console]::Write('{"state":"active_home_unavailable"}'); exit 0
    }
    $accounts = Join-Path $activeRoot 'weixin\accounts'
    if (!(Test-Path -LiteralPath $accounts -PathType Container)) {
        [Console]::Write('{"state":"context_missing"}'); exit 0
    }
    $directory = Get-Item -LiteralPath $accounts -Force
    if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        [Console]::Write('{"state":"context_ambiguous"}'); exit 0
    }
    $items = @(Get-ChildItem -LiteralPath $accounts -Filter '*.context-tokens.json' -File -Force |
               Select-Object -First 2 -Property LastWriteTimeUtc,Attributes)
    if ($items.Count -eq 0) { [Console]::Write('{"state":"context_missing"}'); exit 0 }
    if ($items.Count -ne 1 -or (($items[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        [Console]::Write('{"state":"context_ambiguous"}'); exit 0
    }
    $when = $items[0].LastWriteTimeUtc
    if ($when -gt [DateTime]::UtcNow) { [Console]::Write('{"state":"remote_clock_uncertain"}'); exit 0 }
    $stamp = $when.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
    [Console]::Write((@{state='observed';modified_utc=$stamp} | ConvertTo-Json -Compress))
} catch { [Console]::Write('{"state":"metadata_unavailable"}') }
"""


def metadata(deadline, *, clock, hostname, lookup_route, query_status):
    remaining = (deadline - clock()).total_seconds()
    if remaining <= 0:
        return "deadline_reached", None
    if not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", hostname):
        return "route_unavailable", None
    until = time.monotonic() + min(remaining, REMOTE_TIMEOUT)
    try:
        hub, windows = lookup_route(until)
        command = METADATA_COMMAND.replace("__PINNED_HOST__", hostname)
        result = query_status(hub, windows, command, until, seconds=8)
    except (OSError, ValueError):
        return "route_unavailable", None
    if not isinstance(result, dict):
        return "route_unavailable", None
    state = result.get("state")
    if state in {"route_unavailable", "context_missing", "context_ambiguous", "metadata_unavailable",
                 "remote_clock_uncertain", "active_home_unavailable"}:
        return state, None
    if state != "observed" or not isinstance(result.get("modified_utc"), str):
        return "metadata_unavailable", None
    try:
        at = datetime.fromisoformat(result["modified_utc"])
    except ValueError:
        return "metadata_unavailable", None
    if at.utcoffset() is None:
        return "remote_clock_uncertain", None
    return state, at.astimezone(UTC)


def correction_job_key(correction_key):
    return store.digest(f"owner-focus\n{correction_key}")


def correction_record(db, correction_key):
    # Keep all attempts in the ledger. An expired, audited turn that never
    # invoked the sender can release only its own job and exact attempt.
    job_key = correction_job_key(correction_key)
    stage_key = daily.focus_stage_key(job_key)
    row = db.execute(f"""
        WITH related_jobs AS (
            SELECT key,stage,state,starts_at,attempt_key,detail->>'kind' AS kind,
                   detail->>'correction_key' AS business_key
              FROM job WHERE key=%(base)s OR detail->>'correction_key'=%(business)s
        ), verified_non_submissions AS (
            SELECT j.key AS job_key,a.key AS attempt_key
              FROM related_jobs r JOIN job j ON j.key=r.key JOIN attempt a ON a.key=j.attempt_key
             WHERE {store.VERIFIED_NATIVE_NOT_INVOKED_SQL}
        ), attempts AS (
            SELECT a.state,a.submitted_at FROM attempt a WHERE (a.stage_key=%(stage)s
                OR EXISTS (SELECT FROM related_jobs j WHERE j.attempt_key=a.key
                           OR a.stage_key='wf-focus-' || left(j.key,48)))
              AND NOT EXISTS (SELECT FROM verified_non_submissions v WHERE v.attempt_key=a.key)
        )
        SELECT EXISTS (SELECT FROM related_jobs
                        WHERE stage<>'note' OR kind IS DISTINCT FROM %(kind)s
                           OR business_key IS DISTINCT FROM %(business)s) AS ambiguous,
               EXISTS (SELECT FROM related_jobs j WHERE j.attempt_key IS NOT NULL
                         AND NOT EXISTS (SELECT FROM verified_non_submissions v
                                          WHERE v.job_key=j.key AND v.attempt_key=j.attempt_key)) AS linked,
               (SELECT state FROM attempts ORDER BY (state='accepted') DESC,submitted_at DESC LIMIT 1) AS attempt_state,
               (SELECT state FROM related_jobs ORDER BY (state NOT IN ('expired','suppressed')) DESC,
                      starts_at DESC LIMIT 1) AS job_state
        """, {"base": job_key, "business": correction_key, "stage": stage_key, "kind": store.FOCUS_KIND}).fetchone()
    if row["ambiguous"]:
        return "ambiguous_correction"
    if row["attempt_state"] in {"accepted", "unknown", "submitted"}:
        return row["attempt_state"]
    if row["linked"]:
        return "submitted"
    if row["job_state"] in {"expired", "suppressed"}:
        return "retry_before_submission"
    return row["job_state"] or "absent"


def request_correction(db, *, correction_key, deadline, get_time, request):
    existing = correction_record(db, correction_key)
    if existing not in {"absent", "retry_before_submission"}:
        return existing
    if get_time(db) >= deadline:
        return "deadline_reached"
    result = request(db, correction_key=correction_key, correction_until=deadline)
    recorded = correction_record(db, correction_key)
    if recorded not in {"absent", "retry_before_submission"}:
        return recorded
    state = result.get("state") if isinstance(result, dict) else None
    return state if state in {
        "no_focus", "focus_scan_running", "focus_queue_busy", "correction_window_closed", "waiting_for_fresh_quote",
    } else "verify_correction_record"


def record_status(state):
    if state == "accepted":
        return "platform_accepted", True
    if state in {"unknown", "submitted"}:
        return "awaiting_existing_receipt", False
    if state in {"queued", "pending", "leased"}:
        return "existing_handoff", False
    if state == "ambiguous_correction":
        return state, True
    return "verify_correction_record", False


def step(clock, get_record, get_metadata, publish, *, denied_at, deadline):
    if clock() >= deadline:
        return "deadline_reached", True
    try:
        state = get_record()
    except Exception:
        return "ledger_unavailable", False
    if state not in {"absent", "retry_before_submission"}:
        return record_status(state)
    try:
        gate, observed = get_metadata(deadline)
    except Exception:
        return "route_unavailable", False
    now = clock()
    if now >= deadline:
        return "deadline_reached", True
    if gate != "observed":
        return gate, False
    if observed is None or observed > now:
        return "remote_clock_uncertain", False
    if observed <= denied_at:
        return "awaiting_owner_message", False
    try:
        state = publish()
    except Exception:
        # Never infer that a failed client call failed to commit. Re-read the
        # durable job and every attempt carrying its stage key on the next poll.
        return "verify_correction_record", False
    if state in {"no_focus", "waiting_for_fresh_quote"}:
        return "awaiting_fresh_candidates", False
    if state in {"focus_scan_running", "focus_queue_busy"}:
        return "focus_scan_in_progress", False
    if state in {"deadline_reached", "correction_window_closed"}:
        return "deadline_reached", True
    return record_status(state)
