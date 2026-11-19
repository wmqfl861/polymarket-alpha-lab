"""FX-01 read-only paper-ledger gross/net performance report.

Single responsibility: load settled paper-ledger rows from a checked-in
export (JSON snapshot or ``\\copy`` CSV pair), or from the live
paper_sim cluster with a READ-ONLY ``SELECT``, and print gross and net
(after-fee) returns by queue. This is the minimal predecessor of the
FX-11 evaluation module (it will be absorbed there); the aggregation
itself lives in the library
(:func:`polymarket_alpha_lab.papersim.ledger.queue_performance`) and
this script only does I/O and formatting.

Fee semantics (V-6 evidence run 2026-11-12,
``~/paper-sim/review/audit2026_v6_v7/`` on the ops host): official
formula ``fee = shares x rate x p x (1 - p)`` with ``shares =
stake / price`` (== ``stake * rate * (1 - p)``), rates from the FX-06
category table via the scan-lineage classification (``crypto_%`` teams
7%, every other team 5%; ``market_type`` does not override — the gold
and equity-index rows recorded under ``crypto_touch`` are sports-rate);
``EXITED`` rows additionally charge the sell fee at the exit price
reconstructed from the recorded PnL; ``VOIDED`` rows are stake-refunded
and excluded; unresolved rows are excluded.

Read-only and paper-only: no writes anywhere, no orders, no
credentials. The PG path resolves its DSN exclusively through
``ledger.papersim_dsn()`` (the single audited
``validate_local_postgres_dsn`` door — never a raw ``psql`` bypass) and
opens the session with ``default_transaction_read_only=on``; the SCRAM
password is only read at connect time from the operator-supplied
``PAPERSIM_PG_PASSWORD_FILE`` when that variable is set.

CAVEAT printed with every report: net figures are ESTIMATES
back-computed with the fee schedule as of
``fees.FEE_SCHEDULE_AS_OF``; whether the historical markets were
actually charged, and at which tier, cannot be verified from the repo.

Usage::

    # 1) the checked-in audit snapshot (reproduces the published
    #    gross 23.30% and nets fees; JSON rows carry recorded pnl,
    #    so the default pnl source there is 'recorded'):
    python tools/papersim_ledger_report.py \
        --ledger-json docs/paper-sim/review-2026-10-30/settled_ledger.json

    # 2) a \\copy export pair from the live cluster (V-6 shape);
    #    default pnl source 'recomputed' (per-entry, V-6 primary):
    python tools/papersim_ledger_report.py \
        --decisions-csv decisions.csv --resolutions-csv resolutions.csv

    # 3) read-only SELECT against PAPERSIM_PG_DSN:
    python tools/papersim_ledger_report.py --from-pg
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

# Repo/package bootstrap: prefer the installed package; fall back to the
# worktree's src/ so the script also runs from a plain checkout.
try:
    from polymarket_alpha_lab.papersim import fees, ledger, legacy_settlement
except ImportError:  # pragma: no cover - exercised only outside the venv
    _SRC = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(_SRC))
    from polymarket_alpha_lab.papersim import fees, ledger, legacy_settlement

# Read-only session flag: every connection this tool opens refuses writes
# at the server, on top of the script itself issuing SELECTs only.
_PG_READONLY_OPTIONS = "-c default_transaction_read_only=on"

# The single read-only join the PG mode runs (decisions LEFT JOIN
# resolutions; unresolved decisions keep outcome/pnl NULL).
_PG_SELECT_SQL = (
    "select d.id, d.market_id, d.team, d.market_type, d.side, "
    "d.stake, d.price, d.ts, d.shares, r.ts as resolution_ts, "
    "r.outcome, r.pnl, r.payoff, r.evidence "
    "from public.decisions d "
    "left join public.resolutions r on r.market_id = d.market_id "
    "order by d.id"
)


def _optional_float(value: Any) -> float | None:
    """Parse a possibly-null numeric field (CSV strings, PG numerics)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def load_rows_json(path: Path) -> list[dict[str, Any]]:
    """Load a settled-ledger JSON export (list of decision-shaped rows)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(
        isinstance(row, dict) for row in data
    ):
        raise ValueError(
            f"{path}: expected a JSON list of decision-shaped row objects"
        )
    return data


def _bind_resolutions(
    decisions: list[dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    duplicate_markets: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = {}
    for row in decisions:
        by_market.setdefault(row["market_id"], []).append(row)
    blocked = {}
    allocations = {}
    for market_id, market_rows in by_market.items():
        if market_id not in resolutions:
            continue
        try:
            if market_id in duplicate_markets:
                raise legacy_settlement.LegacyResolutionError(
                    "papersim_legacy_resolution_duplicate", market_id, market_rows
                )
            allocations[market_id] = legacy_settlement.attribute_resolution(
                market_id, market_rows, resolutions[market_id]
            )
        except legacy_settlement.LegacyResolutionError as error:
            blocked[market_id] = str(error)

    rows = []
    for row in decisions:
        market_id = row["market_id"]
        resolution = resolutions.get(market_id, {})
        output = {
            key: row.get(key) for key in
            ("id", "market_id", "team", "market_type", "side", "stake", "price")
        }
        if market_id in blocked:
            output.update(outcome=None, pnl=None, settlement_issue=blocked[market_id])
        else:
            pooled_pnl = allocations.get(market_id)
            pnl = resolution.get("pnl") if pooled_pnl is None else pooled_pnl[str(row["id"])]
            output.update(outcome=resolution.get("outcome"), pnl=pnl)
        rows.append(output)
    return rows


def load_rows_csv(decisions_csv: Path, resolutions_csv: Path) -> list[dict[str, Any]]:
    """Attribute resolutions in a ``\\copy`` CSV pair to the decisions they cover."""
    resolutions: dict[str, dict[str, Any]] = {}
    duplicate_markets: set[str] = set()
    with resolutions_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            market_id = (row.get("market_id") or "").strip()
            if market_id:
                if market_id in resolutions:
                    duplicate_markets.add(market_id)
                resolutions[market_id] = row
    with decisions_csv.open(newline="", encoding="utf-8") as handle:
        decisions = list(csv.DictReader(handle))
    for row in decisions:
        row["market_id"] = (row.get("market_id") or "").strip()
    rows = _bind_resolutions(decisions, resolutions, frozenset(duplicate_markets))
    for row in rows:
        row.pop("market_id")  # Preserve the CSV loader's decision-shaped result.
        for field in ("stake", "price", "pnl"):
            row[field] = _optional_float(row[field])
    return rows


def load_rows_pg(connect: Callable[..., Any]) -> list[dict[str, Any]]:
    """Read the ledger join through a read-only PG connection.

    ``connect`` is injected so tests can pass a fake; production wiring
    builds it from ``ledger.papersim_dsn()`` (the validated DSN) plus
    the operator's out-of-band password file. Raises ``ImportError``
    guidance when psycopg is not installed.
    """
    connection = connect(
        ledger.papersim_dsn(),
        connect_timeout=5,
        options=_PG_READONLY_OPTIONS,
    )
    try:
        with connection:
            cursor = connection.execute(_PG_SELECT_SQL)
            columns = [desc.name for desc in cursor.description]
            decisions = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()
    resolutions = {
        row["market_id"]: {
            "ts": row.get("resolution_ts"), "outcome": row["outcome"],
            "pnl": row["pnl"], "payoff": row.get("payoff"),
            "evidence": row.get("evidence"),
        }
        for row in decisions if row["outcome"] is not None
    }
    return _bind_resolutions(decisions, resolutions)


def _build_connect() -> Callable[..., Any]:
    """Build the production connect through the single papersim adapter.

    FX-09L convergence (design 2026-11-15 v2, section 6.2 L3): the
    production ``--from-pg`` path no longer hand-rolls a ``psycopg``
    connect or a password-file read. It delegates to
    ``papersim.store_psycopg.connect_legacy_readonly`` — the one
    allowlisted connector — which validates the DSN through the single
    audited validator before connecting, forces the read-only session flag
    itself and applies the operator password-file convention with its
    no-symlink guard. The injected-callable contract of
    :func:`load_rows_pg` (``connect(dsn, connect_timeout=5, options=...)``)
    is unchanged; test fakes never pass through this builder, and nothing
    but this builder can bypass the adapter on the production path.
    """
    try:
        from polymarket_alpha_lab.papersim import store_psycopg
    except ImportError as error:  # pragma: no cover - env-dependent
        raise SystemExit(
            "the --from-pg mode requires psycopg (not installed)"
        ) from error
    import os

    password_file = os.environ.get(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR) or None

    def _connect(dsn: str, **kwargs: Any):
        if kwargs != {"connect_timeout": 5, "options": _PG_READONLY_OPTIONS}:
            raise ValueError(
                "unexpected load_rows_pg connect kwargs: "
                f"{sorted(kwargs)!r}"
            )
        return store_psycopg.connect_legacy_readonly(
            dsn, password_file=password_file
        )

    return _connect


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def _money(value: float) -> str:
    return f"{value:.2f}"


def render_report(
    performance: ledger.LedgerPerformance,
    *,
    source_label: str,
    mode: str,
    pnl_source: str,
    settlement_issues: tuple[str, ...] = (),
) -> str:
    """Format one LedgerPerformance as the deterministic text report."""
    lines = [
        "papersim ledger report - gross and net performance by queue (FX-01)",
        f"source: {source_label} (mode: {mode}, pnl source: {pnl_source})",
        "fee schedule: TAKER_FEE_RATES as of "
        f"{performance.fee_schedule_as_of} ({fees.FEE_SCHEDULE_SOURCE})",
        "classification: scan mirror (team rule) - team 'crypto_*' -> crypto "
        "(0.07); every other team -> sports (0.05); market_type does not "
        "override",
        "caveat: net figures are ESTIMATES back-computed with the "
        f"{performance.fee_schedule_as_of} fee schedule; actual historical "
        "charging is not verifiable from the repo.",
    ]
    if pnl_source == ledger.PNL_SOURCE_RECORDED:
        if mode == "ledger-json":
            lines.append("gross basis: recorded per-row pnl as exported")
        else:
            lines.append("gross basis: recorded pnl with verified shared resolutions allocated once")
    else:
        lines.append(
            "gross basis: per-entry recomputed (V-6 primary); EXITED rows "
            "use the recorded market pnl (exit price reconstructed)"
        )
    lines += [
        "",
        f"entries counted: {performance.entries} "
        f"(VOIDED refunded/excluded: {performance.voided_excluded}; "
        f"unresolved/open excluded: {performance.unresolved_excluded})",
        f"stake: {_money(performance.stake)}",
        f"gross pnl: {_money(performance.gross_pnl)}   "
        f"gross roi: {_percent(performance.gross_roi)}",
        f"entry fees: {_money(performance.entry_fees)}",
        f"exit fees: {_money(performance.exit_fees)}",
        f"net pnl (entry fees): "
        f"{_money(performance.net_pnl_entry_fees_only)}   "
        f"net roi: {_percent(performance.net_roi_entry_fees_only)}",
        f"net pnl (entry+exit fees): {_money(performance.net_pnl)}   "
        f"net roi: {_percent(performance.net_roi)}",
        "",
        "queue (market_type) | categories | n | stake | gross | entry fees"
        " | exit fees | net | net roi",
    ]
    for queue in performance.queues:
        lines.append(
            f"{queue.market_type} | {'+'.join(queue.categories)} | "
            f"{queue.entries} | {_money(queue.stake)} | "
            f"{_money(queue.gross_pnl)} | {_money(queue.entry_fees)} | "
            f"{_money(queue.exit_fees)} | {_money(queue.net_pnl)} | "
            f"{_percent(queue.net_roi)}"
        )
    lines += [
        "",
        "classification (team -> category, rate):",
    ]
    lines += [
        f"{team} -> {category} ({rate:.2f})"
        for team, category, rate in performance.team_classification
    ]
    if settlement_issues:
        lines += [
            "",
            f"ambiguous settlements excluded: entries={len(settlement_issues)} "
            f"markets={len(set(settlement_issues))}",
            *sorted(set(settlement_issues)),
        ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="papersim_ledger_report",
        description=(
            "Read-only paper-ledger gross/net performance report (FX-01)."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--ledger-json",
        type=Path,
        help=(
            "settled-ledger JSON export (list of decision-shaped rows with "
            "recorded pnl, e.g. docs/paper-sim/review-2026-10-30/"
            "settled_ledger.json)"
        ),
    )
    source.add_argument(
        "--decisions-csv",
        type=Path,
        help="decisions \\copy CSV (requires --resolutions-csv)",
    )
    source.add_argument(
        "--from-pg",
        action="store_true",
        help=(
            "read-only SELECT against PAPERSIM_PG_DSN (validated through "
            "ledger.papersim_dsn; no psql bypass)"
        ),
    )
    parser.add_argument(
        "--resolutions-csv",
        type=Path,
        help="resolutions \\copy CSV joined by market_id",
    )
    parser.add_argument(
        "--pnl-source",
        choices=(
            "auto",
            ledger.PNL_SOURCE_RECORDED,
            ledger.PNL_SOURCE_RECOMPUTED,
        ),
        default="auto",
        help=(
            "gross basis: 'recorded' reproduces exported pnl as published; "
            "'recomputed' is the V-6 per-entry primary. Default 'auto': "
            "recorded for --ledger-json, recomputed for CSV/PG."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.decisions_csv is not None and args.resolutions_csv is None:
        raise SystemExit("--decisions-csv requires --resolutions-csv")
    if args.decisions_csv is None and args.resolutions_csv is not None:
        raise SystemExit("--resolutions-csv requires --decisions-csv")
    if args.ledger_json is not None:
        rows = load_rows_json(args.ledger_json)
        mode = "ledger-json"
        source_label = args.ledger_json.as_posix()
        default_source = ledger.PNL_SOURCE_RECORDED
    elif args.decisions_csv is not None:
        rows = load_rows_csv(args.decisions_csv, args.resolutions_csv)
        mode = "decisions+resolutions csv"
        source_label = (
            f"{args.decisions_csv.as_posix()} + "
            f"{args.resolutions_csv.as_posix()}"
        )
        default_source = ledger.PNL_SOURCE_RECOMPUTED
    else:
        rows = load_rows_pg(_build_connect())
        mode = "pg read-only select"
        source_label = "PAPERSIM_PG_DSN (validated)"
        default_source = ledger.PNL_SOURCE_RECOMPUTED
    pnl_source = (
        default_source if args.pnl_source == "auto" else args.pnl_source
    )
    eligible = [row for row in rows if "settlement_issue" not in row]
    settlement_issues = tuple(row["settlement_issue"] for row in rows
                              if "settlement_issue" in row)
    performance = ledger.queue_performance(eligible, pnl_source=pnl_source)
    print(render_report(
        performance,
        source_label=source_label,
        mode=mode,
        pnl_source=pnl_source,
        settlement_issues=settlement_issues,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
