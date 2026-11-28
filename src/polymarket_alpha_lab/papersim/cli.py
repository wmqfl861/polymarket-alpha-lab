"""Command-line entry points for the paper-sim library.

Single responsibility: expose the library's read-only research flows
(load snapshots, price fixtures, evaluate gates, render reports, dump
regression comparisons against the frozen fixtures) as CLI subcommands.
Every subcommand is paper-only / report-only / readonly: none may place,
sign or submit real orders, authenticate accounts or handle credentials.

Canonical lineage: this CLI replaces the per-round bespoke scripts of the
soccer lineage (``r5c -> r15 -> r23``: discover/model/finalize per round)
and the crypto lineage (``r19``: fetch19/model19/finalize19) with one
consolidated, testable surface. Bespoke scripts may not relax model
guards (``review/FIXES.md`` scan-dispatch rule).

Subcommands (WP-P1 final commit):

- ``selfcheck``: the invariant suite. Imports the package surface, then
  recomputes the frozen regression anchors through the REAL
  implementations — the three r19 crypto touch anchors (ETH-2000 /
  ETH-2250 / BTC-65k), the four r23 soccer anchors (BRE-FOR / HUL-IPS /
  CHE-MUN / BOU-LEE over the ten sweep variants), the no-bridge
  cross-league path (HOF-LYO yields ``None`` without bridge; policy is ELIGIBLE_DEPRIORITIZED with bridge) and the frozen #48
  gate-arithmetic record (book walk, edge identities, gate pass).
  Prints one ``[PASS]``/``[FAIL]`` line per check plus a summary; exit
  code 0 only when every check passes. No network, no database
  connection, no writes.
- ``schema``: emit the paper-sim ledger migration text
  (:func:`polymarket_alpha_lab.papersim.ledger.ddl`) verbatim to stdout.
  Descriptive evidence only: applying migrations stays the exclusive
  job of the transactional native-Postgres migration ledger, never this
  command.
- ``validate-dsn``: report-only validation of the ledger DSN
  environment variable through the single audited
  ``validate_local_postgres_dsn`` (via
  :func:`polymarket_alpha_lab.papersim.ledger.papersim_dsn`). This CLI
  never reads the process environment itself: the env-vs-default source
  metadata comes from the sanctioned
  ``polymarket_alpha_lab.supabase_papersim_config`` boundary
  (``from_papersim_db_env``), the only module allowed to read the DSN
  environment. Never opens a connection, never reads
  ``PAPERSIM_PG_PASSWORD_FILE`` and never prints the DSN value (repo
  redaction discipline).
- ``evaluate`` (final CLI wiring, M1): FX-11 queue evaluation over
  recorded paper evidence. The candidate queue comes from the managed
  papersim store (read-only SELECTs through the reviewed connection
  adapter), an explicit isolated-cluster DSN validated by the single
  audited local validator, or an explicit JSON candidate file. Store
  reads join the per-trade settlement ledger (M1 final review P2-01; receipt in repo:
  docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log,
  DELIVERY_PLAN.md 78.5):
  settled trades evaluate on their recorded realization (win/loss
  labels, EXITED exits with the ledger gross and two-sided fees,
  VOIDED refunds), the market natural result stays an independent
  whole-market scoring input, and book-walk provenance requires the
  candidate's recorded walked order book. Prints
  one JSON :class:`~polymarket_alpha_lab.papersim.evaluation.EvaluationReport`;
  paper-only / report-only / readonly.
- ``replay`` (final CLI wiring, M1): FX-12 forensic replay of one
  recorded candidate by id. Requires the operator's
  ``--trusted-commit`` (the recorded code commit is compared against
  it, never against the ambient HEAD); read-only SELECTs through the
  reviewed connection adapter; prints the replay match summary as
  JSON. No network, no code execution from the record.
- ``report`` (B4, M2 paper-loop closeout): one idempotent, reproducible,
  timestamped pipeline report. Reads only project-private PostgreSQL
  evidence inside ONE repeatable-read read-only transaction (pipeline
  receipts, scan runs, queue-control latch rows, bankroll checks and
  honest coverage scalars) and reuses the evaluate command's store
  loader as the single metric face; the capture->settle stages are
  presented in causal order, stages whose command is not wired stay
  ``STAGE_NOT_WIRED``, and missing evidence is never zero-filled or
  synthesized. The canonical report bytes are registered as one
  ``papersim-report`` snapshot plus ``report`` run receipts (the only
  writes: paper evidence). Paper-only / report-only / readonly.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from polymarket_alpha_lab.papersim import (
    crypto_model,
    gates,
    ledger,
    risk_policy,
    soccer_model,
    xg,
)

__all__ = ["main"]

# ---------------------------------------------------------------------------
# Frozen reconstruction constants (shared with the wave test files).
#
# These byte-frozen values duplicate constants documented with full
# provenance in ``tests/test_papersim_soccer_model.py`` (EPL_2025
# aggregates, frozen from /home/ubuntu/paper-sim/round23/raw/
# xg_epl_23.json key "EPL_2025": 380 isResult dates, 38-row team
# histories) and ``tests/test_papersim_gates.py`` (the representative
# #48 ladders reproducing the frozen r24 book-ladder summary; round24/
# raw/books24.json stores the walk summary, not the raw levels). The
# selfcheck intentionally re-embeds them so the CLI regression suite
# runs without importing test modules; if a value ever needs to change,
# it is a fixture/regression change, not a tune-up.
# ---------------------------------------------------------------------------

EPL_2025_HOME_RATE = 1.6946258894736843
EPL_2025_AWAY_RATE = 1.3643311084210525
EPL_2025_TEAM_RATES: dict[str, tuple[float, float]] = {
    "Bournemouth": (1.758804, 1.4939418157894737),
    "Brentford": (1.7435966842105264, 1.5555292368421054),
    "Chelsea": (1.9003278947368418, 1.5294069210526315),
    "Leeds": (1.5729118421052632, 1.4822607894736841),
    "Manchester United": (1.8996939473684211, 1.3231953684210527),
    "Nottingham Forest": (1.287795947368421, 1.7036741052631579),
}

# r23 regression cases: (case id, excerpt home title, excerpt away title,
# O/U line). Hull and Ipswich are deliberately absent from
# EPL_2025_TEAM_RATES (promoted sides), exercising the league-average
# fallback exactly as the r23 model did.
R23_CASES: tuple[tuple[str, str, str, float], ...] = (
    ("BRE-FOR_O2.5", "Brentford", "Nottingham Forest", 2.5),
    ("HUL-IPS_O2.5", "Hull", "Ipswich", 2.5),
    ("CHE-MUN_O3.5", "Chelsea", "Manchester United", 3.5),
    ("BOU-LEE_O2.5", "Bournemouth", "Leeds", 2.5),
)

# Reconstructed representative ladders for market 5856164 (#48
# Brentford-Forest O2.5) reproducing the frozen r24 book-ladder summary:
# ask best/vwap 0.56 filled depth_near2c $16,076; bid best/vwap 0.54
# filled depth_near2c $13,759.
LADDER_5856164_ASK: tuple[tuple[float, float], ...] = (
    (0.56, 1000.0),
    (0.57, 14000.0),
    (0.58, 12993.0),
    (0.60, 30000.0),
)
LADDER_5856164_BID: tuple[tuple[float, float], ...] = (
    (0.54, 800.0),
    (0.53, 15000.0),
    (0.52, 10340.0),
    (0.51, 20000.0),
)

_CHECK_TOLERANCE = 1e-4
_LAMBDA_TOLERANCE = 5e-4


def _fixtures_dir() -> Path:
    """Repository location of the frozen papersim regression fixtures."""
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "papersim"


def _load_fixture(name: str) -> dict:
    path = _fixtures_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"frozen fixture missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# selfcheck checks: each returns (name, ok, detail)
# ---------------------------------------------------------------------------


def _check_module_imports() -> tuple[str, bool, str]:
    from polymarket_alpha_lab import papersim as package

    modules = (crypto_model, gates, ledger, risk_policy, soccer_model, xg)
    unresolved = [
        name for name in package.__all__ if getattr(package, name, None) is None
    ]
    if unresolved:
        return (
            "module-imports",
            False,
            f"unresolved package exports: {', '.join(sorted(unresolved))}",
        )
    return (
        "module-imports",
        True,
        f"6 modules imported ({', '.join(m.__name__.rpartition('.')[2] for m in modules)}); "
        f"{len(package.__all__)} package exports resolve "
        "(ledger.record_entry/mark_to_market completed by FX-09L; pure event "
        "construction and per-share mark delta)",
    )


def _r19_horizon_hours(fixture: dict) -> float:
    """Recompute the r19 ``T`` exactly as model19.py did (see W2 tests)."""
    anchor = datetime.datetime.fromisoformat(fixture["anchor"]["raw_scan_generated_at"])
    dec31 = datetime.datetime(2026, 12, 31, 23, 59, tzinfo=datetime.timezone.utc)
    return (dec31 - anchor).total_seconds() / 86400.0 * 24.0


def _check_crypto_r19(case_key: str) -> tuple[str, bool, str]:
    fixture = _load_fixture("regression_r19_crypto.json")
    case = fixture["cases"][case_key]
    inputs = fixture["inputs"]
    spot = float(inputs["spot"][case["asset"]])
    sigma = float(inputs["realized_vol_daily"][case["asset"]][inputs["sig_table"]])
    horizon = _r19_horizon_hours(fixture)
    probs = crypto_model.touch_probability(
        spot=spot,
        sigma_daily=sigma,
        barrier=float(case["barrier"]),
        horizon_hours=horizon,
        asset=str(case["asset"]),
    )
    delta_a = abs(probs.convention_a - float(case["A"]))
    delta_b = abs(probs.convention_b - float(case["B"]))
    horizon_ok = round(horizon, 2) == float(case["horizon_hours"])
    # All three frozen barriers sit below spot: mean-flat B (negative log
    # drift pushes mass down) must touch more often than median-flat A.
    ordering_ok = probs.convention_b > probs.convention_a
    ok = (
        delta_a <= _CHECK_TOLERANCE
        and delta_b <= _CHECK_TOLERANCE
        and horizon_ok
        and ordering_ok
    )
    return (
        f"crypto-r19:{case_key}",
        ok,
        f"A={probs.convention_a:.4f} (want {case['A']}) B={probs.convention_b:.4f} "
        f"(want {case['B']}) |dA|={delta_a:.2e} |dB|={delta_b:.2e} "
        f"horizon={round(horizon, 2)}h",
    )


def _r23_snapshot(fixture: dict) -> dict[str, object]:
    """Rebuild the regression snapshot from the fixture excerpt (W1 tests)."""
    excerpt = fixture["xg_snapshot_excerpt"]
    prior_teams = {
        team_id: {
            "id": team_id,
            "title": row["title"],
            "history": [
                {
                    "xG": EPL_2025_TEAM_RATES[row["title"]][0],
                    "xGA": EPL_2025_TEAM_RATES[row["title"]][1],
                }
            ],
        }
        for team_id, row in excerpt["teams"].items()
        if row["title"] in EPL_2025_TEAM_RATES
    }
    return {
        "EPL_2025": {
            "dates": [
                {
                    "isResult": True,
                    "xG": {"h": EPL_2025_HOME_RATE, "a": EPL_2025_AWAY_RATE},
                }
            ],
            "teams": prior_teams,
        },
        "EPL_2026": {"dates": [], "teams": excerpt["teams"]},
    }


def _check_soccer_r23(case_id: str) -> tuple[str, bool, str]:
    fixture = _load_fixture("regression_r23_soccer.json")
    case = fixture["cases"][case_id]
    home, away, line = next(
        (h, a, ln) for cid, h, a, ln in R23_CASES if cid == case_id
    )
    snapshot = xg.load_xg_payload(_r23_snapshot(fixture))
    variants = soccer_model.fixture_variants(snapshot, home, "EPL", away, "EPL")
    if variants is None:
        return (f"soccer-r23:{case_id}", False, "fixture_variants returned None")
    quotes = [soccer_model.over_probability(v.outputs, line) for v in variants]
    variant_ok = all(
        abs(got - want) <= _CHECK_TOLERANCE
        for got, want in zip(quotes, case["qs_variants"])
    )
    median_ok = (
        abs(soccer_model.median(quotes) - float(case["model_median"]))
        <= _CHECK_TOLERANCE
    )
    range_ok = (
        abs(min(quotes) - float(case["model_range"][0])) <= _CHECK_TOLERANCE
        and abs(max(quotes) - float(case["model_range"][1])) <= _CHECK_TOLERANCE
    )
    lambdas = soccer_model.match_lambdas(snapshot, home, "EPL", away, "EPL")
    lambda_ok = (
        lambdas is not None
        and abs(lambdas[0] - float(case["lambda_home"])) <= _LAMBDA_TOLERANCE
        and abs(lambdas[1] - float(case["lambda_away"])) <= _LAMBDA_TOLERANCE
    )
    ok = variant_ok and median_ok and range_ok and lambda_ok
    return (
        f"soccer-r23:{case_id}",
        ok,
        f"median={soccer_model.median(quotes):.4f} (want {case['model_median']}) "
        f"range={min(quotes):.4f}..{max(quotes):.4f} "
        f"(want {case['model_range'][0]}..{case['model_range'][1]}) "
        f"variants_1e-4={variant_ok} "
        f"lambdas=({lambdas[0]:.3f}, {lambdas[1]:.3f}) "
        f"(want {case['lambda_home']}, {case['lambda_away']})",
    )


def _check_banned_path() -> tuple[str, bool, str]:
    fixture = _load_fixture("regression_banned_path.json")
    case = fixture["case"]
    home = case["home"]
    away = case["away"]
    snapshot = {
        "bundesliga_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.7, "a": 1.4}}],
            "teams": {
                "3": {
                    "title": "TSG 1899 Hoffenheim",
                    "history": [{"xG": 1.5, "xGA": 1.5}],
                }
            },
        },
        "bundesliga_2026": {
            "dates": [],
            "teams": {
                "3": {
                    "title": "TSG 1899 Hoffenheim",
                    "history": [{"xG": 1.5, "xGA": 1.5}],
                }
            },
        },
        "ligue_1_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.6, "a": 1.3}}],
            "teams": {
                "4": {
                    "title": "Olympique Lyonnais",
                    "history": [{"xG": 1.4, "xGA": 1.4}],
                }
            },
        },
        "ligue_1_2026": {
            "dates": [],
            "teams": {
                "4": {
                    "title": "Olympique Lyonnais",
                    "history": [{"xG": 1.4, "xGA": 1.4}],
                }
            },
        },
    }
    lambdas = soccer_model.match_lambdas(
        snapshot, home["team"], home["league"], away["team"], away["league"]
    )
    variants = soccer_model.fixture_variants(
        snapshot, home["team"], home["league"], away["team"], away["league"]
    )
    policy = gates.suspension_policy("match_winner", cross_league=True)
    ok = (
        lambdas is None
        and variants is None
        and case["expected_match_lambdas"] is None
        and policy.policy == gates.POLICY_ELIGIBLE_DEPRIORITIZED
        and policy.eligible is True
    )
    return (
        "no-bridge-path:HOF-LYO",
        ok,
        f"match_lambdas={lambdas} fixture_variants={'None' if variants is None else 'not-None'} "
        f"cross-league match_winner policy={policy.policy}",
    )


def _check_gate_arithmetic_48() -> tuple[str, bool, str]:
    fixture = _load_fixture("regression_gate_arithmetic.json")
    case = fixture["case"]
    entry = case["entry_r23"]
    recheck = case["r24_recheck"]
    ladder = case["r24_book_ladder"]

    ask = gates.walk_book(LADDER_5856164_ASK, "ask")
    bid = gates.walk_book(LADDER_5856164_BID, "bid")
    walk_ok = (
        (ask.best, ask.vwap, ask.filled, ask.depth_near2c)
        == (
            ladder["tok0_ask"]["best"],
            ladder["tok0_ask"]["vwap"],
            ladder["tok0_ask"]["filled"],
            ladder["tok0_ask"]["depth_near2c"],
        )
        and (bid.best, bid.vwap, bid.filled, bid.depth_near2c)
        == (
            ladder["tok0_bid"]["best"],
            ladder["tok0_bid"]["vwap"],
            ladder["tok0_bid"]["filled"],
            ladder["tok0_bid"]["depth_near2c"],
        )
    )
    variants = entry["qs_variants"]
    edge_exec = round(entry["model_median"] - ask.vwap, 4)
    edge_worst = round(min(variants) - ask.vwap, 4)
    spread = round(max(variants) - min(variants), 4)
    identity_ok = (
        edge_exec == recheck["edge_exec_now"]
        and edge_worst == recheck["edge_worst_now"]
        and spread == entry["var_spread"]
        and round(edge_exec * 100, 1) == recheck["expected_edge_exec_cents"]
        and round(edge_worst * 100, 1) == recheck["expected_edge_worst_cents"]
        and round(bid.best - entry["entry_ask_vwap250"], 4)
        == recheck["mark_bid_vs_entry"]
    )
    decision = gates.evaluate_gates(
        variant_spread=spread,
        worst_variant_edge=edge_worst,
        median_exec_edge=edge_exec,
        gamma_liquidity=float(ladder["liq"]),
    )
    gates_ok = decision.passed is True and decision.failed == ()
    # Fee accounting (2026-11-05 fix): the same record through the NET
    # exec bar. Per-share fee at entry 0.56, sports rate:
    # 0.05 * 0.56 * 0.44 = 1.23c, so exec +7.42c gross = +6.19c net,
    # still clearing the frozen +5c bar; both doors (the fee-aware
    # evaluate_gates and evaluate_candidate with its default sports
    # fee_rate) must agree on the net edge and the pass.
    fee = round(gates.fee_cost(ask.vwap, gates.FEE_RATE_SPORTS), 4)
    edge_exec_net = round(edge_exec - fee, 4)
    net_decision = gates.evaluate_gates(
        variant_spread=spread,
        worst_variant_edge=edge_worst,
        median_exec_edge=edge_exec,
        gamma_liquidity=float(ladder["liq"]),
        fee_rate=gates.FEE_RATE_SPORTS,
        entry_price=ask.vwap,
    )
    report = gates.evaluate_candidate(
        dict(model_median=entry["model_median"], qs_variants=variants),
        LADDER_5856164_ASK,
        gamma_liquidity=float(ladder["liq"]),
        news=gates.check_news(
            case["position"], "r24 recheck", news_delta=gates.NEWS_UNCHANGED
        ),
    )
    net_ok = (
        net_decision.passed is True
        and net_decision.failed == ()
        and report.exec_edge_gross == edge_exec
        and report.exec_edge == edge_exec_net
        and report.checks["exec>=+5c"] is True
        and report.decision.passed is True
    )
    ok = walk_ok and identity_ok and gates_ok and net_ok
    return (
        "gate-arithmetic:#48",
        ok,
        f"ask walk best/vwap={ask.best}/{ask.vwap} depth_near2c={ask.depth_near2c:.0f} "
        f"exec=+{edge_exec * 100:.1f}c gross / +{edge_exec_net * 100:.1f}c net "
        f"(fee {fee * 100:.2f}c @ {ask.vwap:.2f}) worst=+{edge_worst * 100:.1f}c "
        f"spread={spread * 100:.2f}c gates={'pass' if gates_ok else 'fail'} "
        f"net_gates={'pass' if net_ok else 'fail'}",
    )


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def _cmd_selfcheck() -> int:
    # Each entry is (failure label, zero-argument check callable): a check
    # that raises is REPORTED as a failure, never a traceback (a missing
    # or unreadable fixture must fail the suite cleanly).
    check_calls: list[tuple[str, Callable[[], tuple[str, bool, str]]]] = [
        ("module-imports", _check_module_imports),
        ("crypto-r19:ETH-2000", lambda: _check_crypto_r19("ETH-2000")),
        ("crypto-r19:ETH-2250", lambda: _check_crypto_r19("ETH-2250")),
        ("crypto-r19:BTC-65k", lambda: _check_crypto_r19("BTC-65k")),
        ("soccer-r23:BRE-FOR_O2.5", lambda: _check_soccer_r23("BRE-FOR_O2.5")),
        ("soccer-r23:HUL-IPS_O2.5", lambda: _check_soccer_r23("HUL-IPS_O2.5")),
        ("soccer-r23:CHE-MUN_O3.5", lambda: _check_soccer_r23("CHE-MUN_O3.5")),
        ("soccer-r23:BOU-LEE_O2.5", lambda: _check_soccer_r23("BOU-LEE_O2.5")),
        ("no-bridge-path:HOF-LYO", _check_banned_path),
        ("gate-arithmetic:#48", _check_gate_arithmetic_48),
    ]
    check_results: list[tuple[str, bool, str]] = []
    for label, check in check_calls:
        try:
            check_results.append(check())
        except Exception as exc:  # report, do not crash
            check_results.append((label, False, f"error: {exc}"))
    lines = ["papersim selfcheck (read-only: no network, no database, no writes)"]
    passed = 0
    for name, ok, detail in check_results:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name} - {detail}")
        passed += 1 if ok else 0
    total = len(check_results)
    overall = "PASS" if passed == total else "FAIL"
    lines.append(f"selfcheck: {overall} ({passed}/{total} checks)")
    print("\n".join(lines))
    return 0 if passed == total else 1


def _cmd_schema() -> int:
    print(ledger.ddl(), end="")
    return 0


def _cmd_validate_dsn() -> int:
    env_var = ledger.PAPERSIM_PG_DSN_ENV_VAR
    # This module never reads the process environment (F9 boundary: DSN
    # environment reads live only inside supabase_papersim_config). The
    # env-vs-default source metadata comes from that sanctioned module:
    # ``dsn is None`` means the variable is unset, empty or whitespace
    # only, so the documented example default is what would be used.
    try:
        source = (
            f"environment {env_var}"
            if ledger.from_papersim_db_env().dsn is not None
            else "documented default (env unset or empty)"
        )
    except ValueError:
        # The env-provided DSN already failed validation inside the
        # sanctioned config boundary; report it via the shared failure
        # path below, where papersim_dsn re-raises the exact reason.
        source = f"environment {env_var}"
    lines = [
        "validate-dsn (report-only: no connection opened, no password read)",
        f"source: {source}",
        "validator: validate_local_postgres_dsn (single audited implementation)",
        "value: <redacted>",
    ]
    try:
        ledger.papersim_dsn()
    except ValueError as exc:
        lines.insert(1, "validate-dsn: FAIL")
        lines.append(f"reason: {exc}")
        print("\n".join(lines))
        return 1
    lines.insert(1, "validate-dsn: PASS")
    lines.append(
        f"password file: {ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR} "
        "is out-of-band and never read by this command"
    )
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# evaluate / replay (final CLI wiring: FX-11 evaluation, FX-12 replay)
# ---------------------------------------------------------------------------

#: Read-only candidate/evidence/outcome SELECTs for ``evaluate``. The
#: decision-time observation is the DECISION row with the smallest
#: evidence_id (09D review item P3-8 derived rule; extract in repo:
#: docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log); the close mark is the latest
#: CLOSE row; the effective market outcome reuses the pure FX-09S
#: selection (latest non-UNKNOWN observation, tie-break outcome_id DESC).
#: M1 final review P2-01: the candidates statement also joins the per-trade
#: settlement ledger (``papersim.settlements`` through ``entries.entry_id``)
#: and the recorded-book marker, so a settled trade evaluates on its actual
#: realization (recorded gross PnL, win/loss label, EXITED/VOIDED
#: classification) instead of being recomputed as held-to-natural-settlement,
#: and legacy fills no longer obtain book-walk provenance for free.
#: FX-26 (N-1): the candidates statement also selects c.event_key so the
#: loader can cluster interval estimates by match; NULL keys keep the
#: frozen market-key fallback unchanged.
_EVAL_CANDIDATES_SQL = (
    "SELECT c.candidate_id, c.candidate_key, c.conclusion, c.side, c.market_type, "
    "c.market_kind, c.event_key, c.model_name, c.model_version, c.evaluated_at, "
    "c.condition_id, "
    "c.market_id, c.market_slug, s.gate_version, e.stake, e.price, e.fee_category, "
    "e.entry_id, e.entered_at, "
    "(c.order_book is not null) AS has_order_book, "
    "st.classification, st.win_loss, st.exit_price, st.proceeds, st.pnl_gross, "
    "st.fee_total, st.pnl_net, st.confirmed_at, e.entered_at "
    "FROM papersim.candidates c JOIN papersim.scan_runs s ON s.scan_id = c.scan_id "
    "LEFT JOIN papersim.entries e ON e.candidate_id = c.candidate_id "
    "LEFT JOIN papersim.settlements st ON st.entry_id = e.entry_id "
    "ORDER BY c.candidate_id"
)
_EVAL_EVIDENCE_SQL = (
    "SELECT candidate_id, evidence_role, model_probability, mid, bid, ask, last, observed_at "
    "FROM papersim.candidate_price_evidence ORDER BY evidence_id"
)
_EVAL_OUTCOMES_SQL = (
    "SELECT outcome_id, condition_id, market_id, market_slug, outcome, observed_at, "
    "confirmed_at, source_kind "
    "FROM papersim.market_outcomes ORDER BY outcome_id"
)


def _jsonify(value):
    """Recursive JSON-ready conversion of the frozen report dataclasses."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonify(getattr(value, field.name))
                for field in dataclasses.fields(value)}
    raise ValueError(
        f"papersim_cli_output_unserializable: {type(value).__qualname__}")


def _open_readonly_connection(args, *, label: str, clock=None):
    """Source connection through the reviewed papersim adapter.

    ``--managed`` opens the project-private managed application face
    (its DSN already passed the single audited local validator inside
    the adapter, before any connection is constructed); ``--dsn`` is an
    explicit DSN for an isolated/throwaway cluster validated by the
    same single audited validator. This module opens no connection
    itself and never reads the process environment.

    Autocommit is enabled on the returned connection so bare SELECTs
    (registry reads, snapshot lookups, identity probes) never open an
    implicit transaction; every ``with conn.transaction():`` block in
    the store writers is then the OUTERMOST real transaction and its
    commit persists (MO-6 code review finding 3: without this, any bare
    SELECT between connection open and the first write block degrades
    all subsequent write blocks to savepoints inside that implicit
    transaction, and ``finally: connection.close()`` rolls back
    everything while the CLI still prints success).
    """
    from polymarket_alpha_lab.papersim import store_psycopg
    options = {} if clock is None else {"clock": clock}
    if args.managed:
        connection = store_psycopg.connect_managed(Path(args.repo_root), **options)
    else:
        connection = store_psycopg.connect_explicit(args.dsn, label=label, **options)
    connection.autocommit = True
    return connection


def _fetch_rows(conn, sql: str) -> list[dict]:
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [description.name for description in cursor.description]
        rows = cursor.fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _snapshot_rows(cursor, sql: str) -> list[dict]:
    """Execute one SELECT on an open snapshot cursor and dictify its rows."""
    cursor.execute(sql)
    columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _number(value):
    return None if value is None else float(value)


def _decision_mid(evidence) -> float | None:
    if evidence is None:
        return None
    if evidence["mid"] is not None:
        return _number(evidence["mid"])
    if evidence["bid"] is not None and evidence["ask"] is not None:
        return (_number(evidence["bid"]) + _number(evidence["ask"])) / 2.0
    return None


def _evaluation_candidates_from_store(conn) -> tuple:
    """Build the FX-11 evaluation queue from the managed store (read-only).

    Every statement is a SELECT. Rejected candidates keep their decision
    probabilities and never carry entry fields; construction-failed
    candidates fall back to their candidate key as the market key (no
    market identity was ever bound) and are flagged
    ``construction_failed`` so criterion A never counts them (FX-30 /
    OD-K: accepted plus gate-rejected only); an UNKNOWN effective outcome
    stays
    ``None`` (coverage gap), never a filled value.

    Settlement wiring (M1 final review P2-01): a trade with a
    ``papersim.settlements`` row evaluates on the ledger's actual
    realization — ``EXIT`` keeps the recorded gross PnL (the exit price is
    reconstructed exactly from it: proceeds / shares) and pays two-sided
    fees through the same single fee schedule, ``NATURAL`` carries the
    post-audit ``win``/``loss`` label plus the recorded gross, and
    ``VOIDED_REFUND`` voids the trade. The market's natural result stays
    available as the independent whole-market scoring input
    (``natural_outcome``) but never re-prices a settled trade. An entered
    row without a settlement record uses evidence applicable to its
    own entry. An eligible outcome can inform recomputation, while the
    recorded basis requires its stored realization.
    ``book_walk`` provenance requires the candidate's recorded walked
    book; legacy imports (no stored order book) stay ``recorded_price``.

    FX-29 (N-4): the three SELECTs run inside ONE REPEATABLE READ
    READ ONLY transaction — the first statement of the transaction sets
    the isolation level — so a capture or settlement committed by another
    connection mid-load can never split the queue: all three row lists
    come from one consistent snapshot. On a static database the results
    are bit-identical to the previous per-statement default isolation.
    """
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = _read_evaluation_rows(cursor)
    return _evaluation_candidates_from_rows(*rows)


def _evaluation_candidates_in_snapshot(conn, *, evidence_times=None, pending_settlements=None) -> tuple:
    """Read the queue inside the caller's repeatable-read, read-only transaction."""
    return _evaluation_candidates_from_rows(
        *_read_evaluation_rows(conn.cursor()), evidence_times=evidence_times,
        pending_settlements=pending_settlements)


def _read_evaluation_rows(cursor) -> tuple[list[dict], list[dict], list[dict]]:
    """Read all queue inputs on one open snapshot cursor."""
    return (
        _snapshot_rows(cursor, _EVAL_CANDIDATES_SQL),
        _snapshot_rows(cursor, _EVAL_EVIDENCE_SQL),
        _snapshot_rows(cursor, _EVAL_OUTCOMES_SQL),
    )


def _evaluation_candidates_from_rows(candidate_rows, evidence_rows, outcome_rows,
                                     *, evidence_times=None, pending_settlements=None) -> tuple:
    from polymarket_alpha_lab.papersim import evaluation, ledger, settle, store

    decision_evidence: dict[int, dict] = {}
    close_evidence: dict[int, dict] = {}
    for row in evidence_rows:
        candidate_id = int(row["candidate_id"])
        if row["evidence_role"] == "DECISION":
            decision_evidence.setdefault(candidate_id, row)
        elif row["evidence_role"] == "CLOSE":
            close_evidence[candidate_id] = row
    observations: dict[str, list[dict]] = {}
    outcomes_by_id = {}
    for row in outcome_rows:
        if evidence_times is not None:
            outcomes_by_id[row["outcome_id"]] = row
        for column in ("condition_id", "market_id", "market_slug"):
            identity = row[column]
            if identity is not None:
                observations.setdefault(identity, []).append(row)
    entry_observations = settle.index_captured_outcomes(outcome_rows)
    built = []
    for row in candidate_rows:
        candidate_id = int(row["candidate_id"])
        decision = decision_evidence.get(candidate_id)
        close = close_evidence.get(candidate_id)
        identity = row["condition_id"] or row["market_id"] or row["market_slug"]
        outcome, settled_at = None, None
        effective = None
        if identity is not None:
            effective = store.select_effective_outcome(observations.get(identity, ()))
            if effective.is_effective:
                outcome, settled_at = effective.outcome, effective.observed_at
        natural_outcome = (
            outcome if outcome in ledger.NATURAL_OUTCOMES else None
        )
        close_price = None
        if close is not None:
            close_price = _number(close["last"] if close["last"] is not None
                                  else close["mid"])
        model_probability = None if decision is None else _number(decision["model_probability"])
        market_mid = _decision_mid(decision)
        entered = row["stake"] is not None
        recorded_pnl = None
        classification = row["classification"] if entered else None
        trade_observation = None
        if entered and classification is None:
            typed_identity = settle.MarketIdentity.from_row(row)
            captured = settle.select_entry_outcome(
                row["entry_id"], row["entered_at"], typed_identity,
                settle.captured_for_identity(entry_observations, typed_identity))
            if captured is not None and captured.is_effective:
                outcome, settled_at = captured.outcome, settle.utc_instant(captured.evidence_time)
                trade_observation = captured
            else:
                outcome, settled_at = None, None
        elif classification is not None:
            if classification == "EXIT":
                # The ledger's actual exit: recorded gross PnL (the exit
                # price is reconstructed from it in evaluation), never the
                # market's natural settlement.
                outcome = ledger.EXITED_OUTCOME
                recorded_pnl = _number(row["pnl_gross"])
                settled_at = row["confirmed_at"]
            elif classification == "NATURAL":
                if row["win_loss"] not in ledger.SETTLED_LABEL_OUTCOMES:
                    raise ValueError(
                        "papersim_eval_settlement_win_loss_missing: "
                        f"candidate {candidate_id}"
                    )
                outcome = row["win_loss"]
                recorded_pnl = _number(row["pnl_gross"])
                settled_at = row["confirmed_at"]
            elif classification == "VOIDED_REFUND":
                outcome = ledger.VOIDED_OUTCOME
                recorded_pnl = _number(row["pnl_gross"])
                settled_at = row["confirmed_at"]
            else:
                raise ValueError(
                    "papersim_eval_settlement_classification_unknown: "
                    f"{classification!r} on candidate {candidate_id}"
                )
        if evidence_times is not None:
            # Market scoring and the pending trade can use different observations.
            market_observation = outcomes_by_id.get(effective.outcome_id) if effective else None
            used_decision = model_probability is not None or market_mid is not None
            moments = (
                evidence_times.get("evaluate"), row["evaluated_at"],
                row.get("entered_at"), row.get("confirmed_at"),
                decision.get("observed_at") if used_decision else None,
                close.get("observed_at") if close_price is not None else None,
                market_observation.get("observed_at") if market_observation else None,
                market_observation.get("confirmed_at") if market_observation else None,
                trade_observation.observed_at if trade_observation else None,
                trade_observation.confirmed_at if trade_observation else None,
            )
            evidence_times["evaluate"] = max(t for t in moments if t is not None)
        built.append(evaluation.EvaluationCandidate(
            candidate_id=str(candidate_id),
            model_key=f"{row['model_name']}:{row['model_version']}",
            market_type=row["market_type"] or row["market_kind"],
            gate_version=row["gate_version"],
            market_key=identity or row["candidate_key"],
            model_probability=model_probability,
            market_mid=market_mid,
            accepted=row["conclusion"] == "ACCEPTED",
            construction_failed=row["conclusion"] == "CONSTRUCTION_FAILED",
            stake=_number(row["stake"]) if entered else None,
            fill_price=_number(row["price"]) if entered else None,
            side=row["side"],
            fee_category=row["fee_category"] if entered else None,
            fill_provenance=(
                ("book_walk" if row["has_order_book"] else "recorded_price")
                if entered else None
            ),
            outcome=outcome,
            natural_outcome=natural_outcome,
            market_outcome_authoritative=True,
            recorded_pnl=recorded_pnl,
            decision_time=row["evaluated_at"],
            settled_time=settled_at,
            close_price=close_price,
            # FX-26 (N-1): cluster by the stored match key when present;
            # NULL keeps the frozen market-key fallback bit-for-bit.
            match_key=row["event_key"],
        ))
        if (pending_settlements is not None and entered and classification is None
                and row["conclusion"] == "ACCEPTED" and outcome in ledger.NATURAL_OUTCOMES):
            pending_settlements.add(str(candidate_id))
    return tuple(built)


def _evaluation_candidates_from_json(path: Path) -> tuple:
    """The explicit JSON candidate file (no connection is opened at all)."""
    from polymarket_alpha_lab.papersim import evaluation

    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("candidates") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError(
            "papersim_cli_json_input_invalid: expected a list of candidates "
            "or an object with a 'candidates' list")
    fields = {field.name for field in dataclasses.fields(evaluation.EvaluationCandidate)}
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("papersim_cli_json_input_invalid: rows must be objects")
        unknown = set(row) - fields
        if unknown:
            raise ValueError("papersim_cli_json_field_unknown: "
                             + ",".join(sorted(unknown)))
        kwargs = dict(row)
        for name in ("decision_time", "settled_time"):
            if isinstance(kwargs.get(name), str):
                kwargs[name] = datetime.datetime.fromisoformat(kwargs[name])
        candidates.append(evaluation.EvaluationCandidate(**kwargs))
    return tuple(candidates)


def _cmd_evaluate(args) -> int:
    from polymarket_alpha_lab.papersim import evaluation

    try:
        if args.json_input is not None:
            candidates = _evaluation_candidates_from_json(Path(args.json_input))
        else:
            connection = _open_readonly_connection(args, label="PAPERSIM_EVALUATION_PG_DSN")
            try:
                candidates = _evaluation_candidates_from_store(connection)
            finally:
                connection.close()
        report = evaluation.evaluate_queues(candidates, pnl_basis=args.pnl_basis)
        payload = _jsonify(report)
    except (ValueError, OSError) as error:
        print(f"evaluate: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return 0


def _cmd_replay(args) -> int:
    from polymarket_alpha_lab import replay as replay_engine

    try:
        connection = _open_readonly_connection(args, label="PAPERSIM_REPLAY_PG_DSN")
        try:
            result = replay_engine.replay(connection, args.candidate_id,
                                          trusted_code_commit=args.trusted_commit)
        finally:
            connection.close()
        payload = _jsonify(result)
    except (ValueError, OSError) as error:
        print(f"replay: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return 0


def _cmd_run_scan(args) -> int:
    # B1 (05 section 2 / 80.8): every heavy import stays inside the
    # function body so the CLI startup module count is untouched
    # (FX-20; tests/test_init_lazy_imports.py pins the limit).
    import datetime as _datetime

    from polymarket_alpha_lab.papersim import scan
    # MO-7 (coordinator-lane one-liner, plan 05 node MO-7 / review
    # P2-3): the scan consults the registry-backed provider.  An empty
    # or unreadable registry collapses to the frozen baseline INSIDE
    # the provider (04 section 11), so this swap alone changes nothing
    # until candidates are registered.
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider

    try:
        config_path = Path(args.config)
        if args.prepare_from is not None:
            if not args.managed or args.operation_key is None:
                raise scan.ScanError("papersim_prepare_requires_managed_operation_key")
            from polymarket_alpha_lab.papersim import prepared_scan

            connection = _open_readonly_connection(args, label="PAPERSIM_SCAN_PG_DSN")
            try:
                result = prepared_scan.run_prepared_scan(
                    connection, repo_root=args.repo_root, config_bytes=config_path.read_bytes(),
                    input_set_sha256=args.prepare_from, operation_key=args.operation_key, attempt=args.attempt,
                    now=lambda: _datetime.datetime.now(_datetime.UTC), evaluation_loader=_evaluation_candidates_in_snapshot,
                    shadow_budget_seconds=args.shadow_budget_seconds, pack_base_dir=config_path.parent,
                )
            finally:
                connection.close()
            print(json.dumps(result, sort_keys=True, allow_nan=False))
            return 0

        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"papersim_scan_config_unreadable: {config_path}") from error
        config = scan.config_from_mapping(document)
        records = scan.load_pack_records(config, base_dir=config_path.parent)
        connection = _open_readonly_connection(args, label="PAPERSIM_SCAN_PG_DSN")
        try:
            # Same-file reuse of the evaluate loader (B1 plan D6/F-2):
            # ONE metric implementation feeds both the evaluate command
            # and the pause admission wiring -- no second SQL face.
            evaluation_candidates = _evaluation_candidates_from_store(connection)
            # MO-8 / MO-7 review P3-2: the production entry point wires
            # the pack-citation shadow source (SnapshotShadowSource over
            # the scan connection) and the shadow budget.  With the
            # registry empty the non-active role set is empty, so this
            # changes nothing until candidates are registered; the
            # pack-side model-input snapshot citations (xG/K-line) are
            # the registered adapter-lane follow-up.
            from polymarket_alpha_lab.papersim import snapshots
            from polymarket_alpha_lab.papersim.optimize.shadow import (
                SnapshotShadowSource,
            )

            result = scan.run_scan(
                config,
                records,
                conn=connection,
                repo_root=args.repo_root,
                provider=RegistryProvider.from_connection(connection),
                now_utc=lambda: _datetime.datetime.now(_datetime.UTC),
                evaluation_candidates=evaluation_candidates,
                operation_key=args.operation_key,
                attempt=args.attempt,
                shadow_source=SnapshotShadowSource(
                    lambda digest: snapshots.read_snapshot(connection, digest)),
                shadow_budget_seconds=args.shadow_budget_seconds,
            )
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"run-scan: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


def _cmd_capture_marks(args) -> int:
    # B2 (05 section 2 / FX-34): every heavy import stays inside the
    # function body so the CLI startup module count is untouched
    # (FX-20; tests/test_init_lazy_imports.py pins the limit).
    import datetime as _datetime

    from polymarket_alpha_lab.papersim import capture

    try:
        config_path = Path(args.config)
        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"papersim_capture_config_unreadable: {config_path}") from error
        config = capture.config_from_mapping(document)
        packs = capture.load_mark_packs(config, base_dir=config_path.parent)
        window_batch = getattr(args, "inspect_close_window", None)
        if window_batch is not None and not args.managed:
            raise ValueError("d1_operation_managed_required")
        connection = _open_readonly_connection(args, label="PAPERSIM_CAPTURE_PG_DSN")
        try:
            if window_batch is not None:
                from polymarket_alpha_lab.papersim import close_window

                result = close_window.inspect(config, packs, conn=connection, repo_root=args.repo_root,
                                              batch_digest=window_batch, operation_key=args.operation_key)
            else:
                result = capture.run_capture(
                    config, packs, conn=connection, repo_root=args.repo_root,
                    now_utc=lambda: _datetime.datetime.now(_datetime.UTC),
                    operation_key=args.operation_key, attempt=args.attempt)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"capture-marks: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


def _cmd_settle(args) -> int:
    # B3 (05 section 2 / 80.8): heavy imports stay inside the handler
    # (FX-20; tests/test_init_lazy_imports.py pins the startup cap).
    import datetime as _datetime

    from polymarket_alpha_lab.papersim import settle, store_psycopg

    try:
        connection = _open_readonly_connection(args, label="PAPERSIM_SETTLE_PG_DSN")
        try:
            result = settle.run_settle(
                args.team,
                conn=connection,
                repo_root=args.repo_root,
                now_utc=lambda: _datetime.datetime.now(_datetime.UTC),
                open_entries=store_psycopg.open_entries_for_settlement,
                operator=args.operator,
                operation_key=args.operation_key,
                attempt=args.attempt,
                managed=args.managed,
            )
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"settle: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# MO-6: optimize families/init/run/status (plan 05 node MO-6).  Every
# heavy import stays inside the handler (FX-20; the startup module cap
# is pinned by tests/test_init_lazy_imports.py; measured baseline 19).
# ---------------------------------------------------------------------------




def parser_error_unknown_optimize(name: str) -> None:
    raise ValueError(f"papersim_optimize_command_unknown:{name!r}")


# MO-8: optimize score/decide/apply/rollback/freeze/unfreeze (plan 05 node
# MO-8; design 04 sections 6, 9, 10 and 11).  Every heavy import stays
# inside the handlers so the CLI startup module count is untouched (FX-20;
# tests/test_init_lazy_imports.py pins the limit).  The judgement rules
# themselves are pure and live in optimize/policy.py; these handlers only
# assemble facts from the store, call the policy and write through the
# reviewed MO-2 writers.
# ---------------------------------------------------------------------------


def _optimize_threshold(lineage: str):
    """(family_id, target, threshold) of one lineage id; unknown fails."""
    from polymarket_alpha_lab.papersim.optimize.policy import classify_threshold

    if not isinstance(lineage, str) or lineage.count("/") != 1:
        raise ValueError("papersim_optimize_lineage_invalid")
    family_id, target = lineage.split("/", 1)
    try:
        return family_id, target, classify_threshold(family_id, target)
    except ValueError:
        raise ValueError("papersim_optimize_lineage_unknown") from None


def _optimize_scan_lineage(family_id: str, target: str) -> str | None:
    """Map a registry lineage through the scan provider's family namespaces."""
    from polymarket_alpha_lab.papersim.optimize.provider import (
        _LINEAGE_FAMILIES, family_ids_for_prefix,
    )

    return next((f"{prefix}/{target}" for prefix, _ in _LINEAGE_FAMILIES
                 if family_id in family_ids_for_prefix(prefix)), None)


def _optimize_role_batches(conn, lineage: str) -> list[list[dict]]:
    """The lineage's role events grouped into writer-transaction batches."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT spec_sha256, role, effective_at FROM papersim.model_role_events "
            "WHERE lineage = %s ORDER BY effective_at, role_event_id", (lineage,))
        columns = [description.name for description in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    batches: list[list[dict]] = []
    current: list[dict] = []
    stamp = None
    for row in rows:
        if current and row["effective_at"] != stamp:
            batches.append(current)
            current = []
        current.append(row)
        stamp = row["effective_at"]
    if current:
        batches.append(current)
    return batches


def _optimize_queue_bindings(config_path, lineage: str) -> list[str]:
    """The queue ids one scan config binds to this lineage (read-only).

    P2-3: the queue->lineage association lives in the scan configs
    (QueueScanConfig carries both fields); nothing here writes or
    rewires the scan module -- the config is parsed through the SAME
    frozen loader run-scan uses.
    """
    import json as _json
    from pathlib import Path as _Path

    from polymarket_alpha_lab.papersim import scan as _scan

    path = _Path(config_path)
    try:
        document = _json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(
            f"papersim_scan_config_unreadable: {config_path}") from error
    config = _scan.config_from_mapping(document)
    return [queue.queue for queue in config.queues if queue.lineage == lineage]


def _optimize_entry_count(conn, model_version: str, market_type: str) -> dict:
    """Entries of the ACTIVE's queue (OD-L / P3-4: the full queue key is
    model_name x model_version x market_type; model_name is constant per
    family+version in practice, so the count is grouped by model_name and
    the breakdown is returned alongside the total)."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.model_name, COUNT(*) FROM papersim.entries e "
            "JOIN papersim.candidates c ON c.candidate_id = e.candidate_id "
            "WHERE c.model_version = %s AND c.market_type = %s "
            "GROUP BY c.model_name ORDER BY c.model_name",
            (model_version, market_type))
        rows = cursor.fetchall()
    breakdown = {str(model_name): int(total) for model_name, total in rows}
    return {"total": sum(breakdown.values()), "by_model_name": breakdown}


def _optimize_scan_failure_since(conn, since) -> bool:
    """Any FAILED run-scan receipt at or after `since` (the concrete,
    recorded v1 face of the data-integrity gate)."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM papersim.command_run_events "
            "WHERE command = 'run-scan' AND phase = 'FAILED' "
            "AND occurred_at >= %s LIMIT 1", (since,))
        return cursor.fetchone() is not None


def _optimize_lineage_facts(conn, lineage: str, queue_ids, *,
                            allow_unavailable_active: bool = False) -> dict:
    """Everything decide/score need about one lineage (no clock reads)."""
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize import policy

    view = store_psycopg.load_lineage_roles(conn, lineage)
    if view is None:
        raise ValueError("papersim_optimize_lineage_unknown")
    batches = _optimize_role_batches(conn, lineage)
    registered_at: dict[str, object] = {}
    last_handover_at = None
    for batch in batches:
        roles = {row["role"] for row in batch}
        if roles == {"CANDIDATE"}:
            registered_at[batch[0]["spec_sha256"]] = batch[0]["effective_at"]
        elif "GUARD" in roles and "ACTIVE" in roles:
            stamp = batch[0]["effective_at"]
            if last_handover_at is None or stamp > last_handover_at:
                last_handover_at = stamp
    freeze = store_psycopg.registry_freeze_state(conn)
    frozen = bool(freeze["global_frozen"] or lineage in freeze["frozen_lineages"])
    latched = store_psycopg.latched_pauses(conn)
    paused = any(latched.get(queue_id, (False, False)) != (False, False)
                 for queue_id in queue_ids)
    try:
        active_spec = store_psycopg.load_model_spec(conn, view.active)
    except Exception:  # Descriptive reads can still report the recorded role digest.
        if not allow_unavailable_active:
            raise
        active_spec = None
    if active_spec is None and not allow_unavailable_active:
        raise ValueError("papersim_optimize_active_spec_missing")
    _family_id, target, _threshold = _optimize_threshold(lineage)
    active_model_version = active_spec.model_version() if active_spec is not None else None
    entries = (_optimize_entry_count(conn, active_model_version, target)
               if active_model_version is not None else None)
    since = min(registered_at.values()) if registered_at else None
    integrity_ok = True
    guard_integrity_regression = False
    if since is not None:
        integrity_ok = not _optimize_scan_failure_since(conn, since)
    if last_handover_at is not None:
        guard_integrity_regression = _optimize_scan_failure_since(
            conn, last_handover_at)
    facts = policy.LineageFacts(
        lineage=lineage,
        active_spec=view.active,
        guard_spec=view.guard,
        candidate_specs=tuple(view.candidates),
        candidate_registered_at=registered_at,
        last_handover_at=last_handover_at,
        frozen=frozen,
        lineage_paused_or_stopped=paused,
        active_queue_entries=entries["total"] if entries is not None else 0,
        # v1 registered decision: no tunable family with season-frozen
        # parameters is registered yet (league-bridge has targets=());
        # the block code exists and the fact stays an explicit input.
        touches_season_frozen_params=False,
        integrity_ok=integrity_ok,
        guard_integrity_regression=guard_integrity_regression,
        # v1 registered limitation: the validation-gate regression flag
        # has no recorded face yet; the statistical rollback trigger is
        # fully wired and this flag stays a documented input.
        guard_validation_gate_regression=False,
    )
    return {"view": view, "facts": facts, "entries": entries,
            "active_model_version": active_model_version}


def _optimize_outcome_for(conn, rows) -> dict:
    """candidate_id -> (natural outcome YES/NO, observed_at), effective
    selection over the recorded observations (same discipline as the
    evaluate loader; UNKNOWN stays absent, never guessed)."""
    from polymarket_alpha_lab.papersim import store

    identities: dict[str, list[dict]] = {}
    condition_ids = sorted({row["condition_id"] for row in rows
                            if row["condition_id"] is not None})
    market_ids = sorted({row["market_id"] for row in rows
                         if row["market_id"] is not None})
    slugs = sorted({row["market_slug"] for row in rows
                    if row["market_slug"] is not None})
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT outcome_id, condition_id, market_id, market_slug, "
            "outcome, observed_at FROM papersim.market_outcomes "
            "WHERE condition_id = ANY(%s) OR market_id = ANY(%s) "
            "OR market_slug = ANY(%s) ORDER BY observed_at, outcome_id",
            (condition_ids, market_ids, slugs))
        columns = [description.name for description in cursor.description]
        for row in cursor.fetchall():
            record = dict(zip(columns, row, strict=True))
            for column in ("condition_id", "market_id", "market_slug"):
                identity = record[column]
                if identity is not None:
                    identities.setdefault(identity, []).append(record)
    resolved: dict[int, tuple[str, object]] = {}
    for row in rows:
        identity = row["condition_id"] or row["market_id"] or row["market_slug"]
        if identity is None:
            continue
        effective = store.select_effective_outcome(identities.get(identity, ()))
        if effective.is_effective and effective.outcome in ("YES", "NO"):
            resolved[int(row["candidate_id"])] = (effective.outcome,
                                                  effective.observed_at)
    return resolved


def _optimize_close_prices(conn, candidate_ids, *, selected_evidence=None) -> dict:
    """Latest usable CLOSE on each candidate's stored buy side."""
    if not candidate_ids:
        return {}
    with conn.transaction():
        cursor = conn.cursor()
        fields = (", p.evidence_id, p.observed_at, p.source_kind, p.snapshot_sha256, "
                  "p.last IS NOT NULL" if selected_evidence is not None else "")
        cursor.execute(
            "SELECT DISTINCT ON (p.candidate_id) p.candidate_id, COALESCE(p.last, p.mid)"
            + fields + " "
            "FROM papersim.candidate_price_evidence p "
            "JOIN papersim.candidates c ON c.candidate_id = p.candidate_id "
            "WHERE p.evidence_role = 'CLOSE' AND p.candidate_id = ANY(%s) "
            "AND p.side = c.side AND COALESCE(p.last, p.mid) IS NOT NULL "
            "ORDER BY p.candidate_id, p.observed_at DESC, p.evidence_id DESC",
            (sorted(candidate_ids),))
        rows = cursor.fetchall()
    if selected_evidence is None:
        return dict(rows)
    for cid, price, evidence_id, observed_at, source_kind, digest, used_last in rows:
        selected_evidence[cid] = {
            "evidence_id": evidence_id, "observed_at": observed_at.isoformat(),
            "source_kind": source_kind, "snapshot_sha256": digest,
            "price_field": "last" if used_last else "mid", "price": str(price),
        }
    return {row[0]: row[1] for row in rows}


def _optimize_close_sample(fills, close_prices):
    from fractions import Fraction

    from polymarket_alpha_lab.papersim.optimize.policy import CloseDeviationSample

    n_prices = missing_close = missing_fill = 0
    sum_delta = Fraction(0)
    for candidate_id, fill in fills.items():
        close = close_prices.get(candidate_id)
        missing_close += close is None
        missing_fill += fill is None
        if close is not None and fill is not None:
            n_prices += 1
            # PostgreSQL NUMERIC values arrive as exact Decimals.
            sum_delta += Fraction(close) - Fraction(fill)
    return CloseDeviationSample(
        markets=len(fills), n_prices=n_prices, missing_close=missing_close,
        missing_fill=missing_fill, sum_delta=sum_delta)


def _optimize_close_deviation(conn, *, shadow_spec_sha256, priced_spec_sha256,
                              priced_model_version, scan_lineage, target, since, evidence_report=None):
    """The would-bet shadows and the active entries are independent samples."""
    # Legacy scans carry only the stored model version and market type.
    # When present, the provider summary must cite this target and spec.
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.candidate_id, sp.shadow_prediction_id, sp.hypothetical_fill_price,
                   e.entry_id, e.price, c.side
            FROM papersim.candidates c
            JOIN papersim.scan_runs r ON r.scan_id = c.scan_id
            LEFT JOIN LATERAL (
                SELECT shadow_prediction_id, gate_pass, hypothetical_fill_price
                FROM papersim.shadow_predictions
                WHERE candidate_id = c.candidate_id AND spec_sha256 = %s
                    AND gate_pass IS TRUE
                ORDER BY predicted_at DESC, shadow_prediction_id DESC
                LIMIT 1
            ) sp ON TRUE
            LEFT JOIN papersim.entries e ON e.candidate_id = c.candidate_id
                AND c.conclusion = 'ACCEPTED'
            WHERE c.evaluated_at >= %s AND c.model_version = %s
                AND COALESCE(c.market_type, c.market_kind) = %s
                AND (r.params #> '{provider,specs}' IS NULL
                    OR r.params #>> ARRAY['provider', 'specs', %s::text, 'active_spec_sha256'] = %s)
                AND (sp.gate_pass IS TRUE OR e.entry_id IS NOT NULL)
            """, (shadow_spec_sha256, since, priced_model_version, target,
                  scan_lineage, priced_spec_sha256))
        rows = cursor.fetchall()
    candidate_fills = {candidate_id: fill for candidate_id, shadow_id, fill, _, _, _ in rows
                       if shadow_id is not None}
    active_fills = {candidate_id: price for candidate_id, _, _, entry_id, price, _ in rows
                    if entry_id is not None}
    selected = {} if evidence_report is not None else None
    prices = _optimize_close_prices(conn, candidate_fills.keys() | active_fills.keys(),
                                    selected_evidence=selected)
    candidate = _optimize_close_sample(candidate_fills, prices)
    active = _optimize_close_sample(active_fills, prices)
    if evidence_report is not None:
        evidence_report["since"] = since.isoformat()
        evidence_report["rows"] = [
            {
                "candidate_id": cid, "side": side, "close": selected.get(cid),
                "shadow_prediction_id": shadow_id,
                "shadow_fill": None if shadow_fill is None else str(shadow_fill),
                "entry_id": entry_id, "entry_fill": None if entry_fill is None else str(entry_fill),
            }
            for cid, shadow_id, shadow_fill, entry_id, entry_fill, side in sorted(rows, key=lambda row: row[0])
        ]
        evidence_report["missing_close_and_fill"] = {
            name: sample.missing_close + sample.missing_fill + sample.n_prices - sample.markets
            for name, sample in (("candidate", candidate), ("active", active))
        }
    return candidate, active


def _optimize_logdiff(p_judged, p_other, realized_yes: bool):
    """One paired log-loss difference in Decimal (judged minus other).

    Same arithmetic posture as optimize.metrics.production_binary_paired:
    side-aligned closed probabilities, Decimal logs, a zero probability
    on either side is an infinite pair (excluded and counted, never
    zero-filled).
    """
    from decimal import Decimal, localcontext

    from polymarket_alpha_lab.research_probability_scores import CONTEXT

    with localcontext(CONTEXT):
        judged = Decimal(str(p_judged))
        other = Decimal(str(p_other))
        judged_true = judged if realized_yes else Decimal(1) - judged
        other_true = other if realized_yes else Decimal(1) - other
        if judged_true == 0 or other_true == 0:
            return None
        return other_true.ln() - judged_true.ln()


def _optimize_pair_rows(conn, *, shadow_spec_sha256: str,
                        priced_model_version: str, since) -> list[dict]:
    """The shadow/priced pair rows of one judged pair.

    Pairing rule (plan section 4): a shadow prediction hangs on the
    candidate row the PRICED spec wrote for the same market in the same
    scan, so the join is by candidate_id; `since` bounds the window
    (candidate registration for FORWARD looks, the handover stamp for
    ROLLBACK looks)."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.candidate_id, c.side, c.condition_id, c.market_id, "
            "c.market_slug, c.market_type, c.model_version, c.event_key, "
            "c.event_start_at, c.evaluated_at, "
            "c.median_probability AS priced_p, "
            "sp.probability AS shadow_p, sp.gate_pass AS shadow_gate, "
            "sp.refusal_reason AS shadow_refusal "
            "FROM papersim.shadow_predictions sp "
            "JOIN papersim.candidates c ON c.candidate_id = sp.candidate_id "
            "WHERE sp.spec_sha256 = %s AND c.model_version = %s "
            "AND c.evaluated_at >= %s ORDER BY c.candidate_id",
            (shadow_spec_sha256, priced_model_version, since))
        columns = [description.name for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _optimize_stats_dict(stats) -> dict:
    """One ForwardStats as canonical JSON with price statistics as strings."""
    from polymarket_alpha_lab.papersim.optimize.policy import close_deviation_metrics

    return {
        "n_markets": stats.n_markets,
        "n_event_clusters": stats.n_event_clusters,
        "n_complete_blocks": stats.n_complete_blocks,
        "span_days": stats.span_days,
        "watermark": None if stats.watermark is None
        else stats.watermark.isoformat(),
        "paired_mean": None if stats.paired_mean is None
        else repr(stats.paired_mean),
        "ci95": [None if bound is None else repr(bound) for bound in stats.ci95],
        "ci99": [None if bound is None else repr(bound) for bound in stats.ci99],
        **close_deviation_metrics(stats),
        "unpaired": stats.unpaired,
    }


def _optimize_forward_stats(conn, lineage: str, *, shadow_spec_sha256: str,
                            priced_model_version: str, since, judged: str,
                            with_close_deviation: bool, stats_spec: str = "",
                            stats_comparator: str = "", close_evidence=None):
    """Assemble one ForwardStats from the store (pure over its rows).

    ``judged`` picks the diff direction: "shadow" (FORWARD look -- the
    candidate's shadow against the priced active) or "priced" (ROLLBACK
    look -- the priced NEW active against the guard's shadow).  The
    registered "场/市场" sample counts EVENT CLUSTERS (match/expiry
    keys), never raw lines; intervals come from the evaluation
    module's frozen cluster bootstrap at both registered levels.
    """
    from polymarket_alpha_lab.papersim.evaluation import cluster_bootstrap_mean_ci
    from polymarket_alpha_lab.papersim.optimize import policy

    family_id, target, threshold = _optimize_threshold(lineage)
    rows = _optimize_pair_rows(
        conn, shadow_spec_sha256=shadow_spec_sha256,
        priced_model_version=priced_model_version, since=since)
    resolved = _optimize_outcome_for(conn, rows)
    close_stats = {}
    if with_close_deviation:
        candidate_close, active_close = _optimize_close_deviation(
            conn, shadow_spec_sha256=shadow_spec_sha256,
            priced_spec_sha256=stats_comparator,
            priced_model_version=priced_model_version,
            scan_lineage=_optimize_scan_lineage(family_id, target),
            target=target, since=since, evidence_report=close_evidence)
        gap = candidate_close.gap_from(active_close)
        close_stats = {
            "close_dev_markets": candidate_close.markets,
            "close_dev_gap": None if gap is None else float(gap),
            "close_dev_candidate": candidate_close,
            "close_dev_active": active_close,
        }
    diffs: list[tuple[tuple[str, str], object]] = []
    starts: list = []
    clusters: dict[tuple[str, str], list[float]] = {}
    order: list[tuple[str, str]] = []
    unpaired = 0
    watermark = None
    for row in rows:
        outcome = resolved.get(int(row["candidate_id"]))
        if outcome is None or row["shadow_p"] is None \
                or row["priced_p"] is None or row["shadow_refusal"] is not None:
            unpaired += 1
            continue
        realized_yes = (row["side"].startswith("YES")) == (outcome[0] == "YES")
        if judged == "shadow":
            diff = _optimize_logdiff(row["shadow_p"], row["priced_p"], realized_yes)
        else:
            diff = _optimize_logdiff(row["priced_p"], row["shadow_p"], realized_yes)
        key = ("event_key", row["event_key"]) if row["event_key"] else (
            "market_key", str(row["condition_id"] or row["market_id"]
                              or row["market_slug"] or row["candidate_id"]))
        if diff is None:
            unpaired += 1
            continue
        if key not in clusters:
            clusters[key] = []
            order.append(key)
        clusters[key].append(float(diff))
        diffs.append(diff)
        if row["event_start_at"] is not None:
            starts.append(row["event_start_at"])
        observed = outcome[1]
        if watermark is None or observed > watermark:
            watermark = observed
    cluster_rows = [clusters[key] for key in order]
    lo95, hi95 = cluster_bootstrap_mean_ci(cluster_rows)
    lo99, hi99 = cluster_bootstrap_mean_ci(
        cluster_rows, percentiles=policy.FIRST_EVIDENCE_CI_PERCENTILES)
    mean = (float(sum(diffs) / len(diffs)) if diffs else None)
    family_class = threshold.family_class
    if family_class.startswith("club-soccer"):
        blocks = policy.complete_matchweek_blocks(starts, watermark) if cluster_rows else 0
    elif family_class == "intl":
        blocks = policy.intl_window_blocks(starts)
    else:
        blocks = 0
    span = policy.span_days_of(starts)
    return policy.ForwardStats(
        spec=stats_spec or shadow_spec_sha256,
        comparator=stats_comparator or shadow_spec_sha256,
        n_markets=len(order), n_event_clusters=len(order),
        n_complete_blocks=blocks, span_days=span, watermark=watermark,
        paired_mean=mean, ci95=(lo95, hi95), ci99=(lo99, hi99),
        unpaired=unpaired, **close_stats)


def _optimize_prior_decisions(conn, lineage: str, decision_class: str,
                              candidate_sha: str | None) -> list[dict]:
    """Prior rows of one (lineage, class, candidate) under the LIVE rule
    version -- the look-key counter and the evidence ordinal come from
    these (BLOCKED rows count toward the key, never the ordinal)."""
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize.policy import RULE_VERSION

    rows = [row for row in store_psycopg.load_promotion_decisions(conn, lineage)
            if row["decision_class"] == decision_class
            and row["rule_version"] == RULE_VERSION
            and row["candidate_spec_sha256"] == candidate_sha]
    return rows


def _optimize_write_decision(conn, *, lineage: str, decision_class: str,
                             candidate_sha, active_sha: str, look_number: int,
                             verdict, decided_at) -> dict:
    from polymarket_alpha_lab.papersim import store, store_psycopg
    from polymarket_alpha_lab.papersim.optimize.policy import RULE_VERSION

    submission = store.PromotionDecisionSubmission(
        lineage=lineage, decision_class=decision_class,
        candidate_spec_sha256=candidate_sha, active_spec_sha256=active_sha,
        rule_version=RULE_VERSION, look_number=look_number,
        evidence_watermark=verdict.evidence_watermark,
        metrics=verdict.metrics, conclusion=verdict.conclusion,
        block_reason=verdict.block_reason,
        decided_by=f"auto:{RULE_VERSION}", decided_at=decided_at)
    return store_psycopg.record_promotion_decision(conn, submission)


def _optimize_decide_lineage(conn, lineage: str, queue_ids) -> dict:
    """Every due look of one lineage, in the pinned order: the ROLLBACK
    look first (safety before promotion), then each candidate's FORWARD
    look in registry order.  Each row write is its own idempotent
    transaction; not-due looks write nothing."""
    import datetime as _datetime

    from polymarket_alpha_lab.papersim.optimize import policy

    _family_id, _target, threshold = _optimize_threshold(lineage)
    state = _optimize_lineage_facts(conn, lineage, queue_ids)
    facts: policy.LineageFacts = state["facts"]
    view = state["view"]
    written: list[dict] = []
    looks: list[dict] = []

    if facts.guard_spec is not None and facts.last_handover_at is not None:
        stats = _optimize_forward_stats(
            conn, lineage, shadow_spec_sha256=facts.guard_spec,
            priced_model_version=state["active_model_version"],
            since=facts.last_handover_at, judged="priced",
            with_close_deviation=False, stats_spec=facts.active_spec,
            stats_comparator=facts.guard_spec)
        prior = _optimize_prior_decisions(conn, lineage, "ROLLBACK",
                                          facts.active_spec)
        key_counter = len(prior) + 1
        ordinal = len([row for row in prior if row["conclusion"] != "BLOCKED"])
        regression = (facts.guard_integrity_regression
                      or facts.guard_validation_gate_regression)
        due = regression or policy.rollback_look_due(stats, threshold)
        looks.append({"class": "ROLLBACK", "spec": facts.active_spec,
                      "due": due, "key_counter": key_counter,
                      "evidence_ordinal": ordinal,
                      "blocked_looks_consumed": key_counter - 1 - ordinal,
                      "n_markets": stats.n_markets})
        if due and stats.watermark is not None:
            verdict = policy.decide_rollback(
                facts, stats, look_number=key_counter, threshold=threshold,
                as_of=stats.watermark)
            record = _optimize_write_decision(
                conn, lineage=lineage, decision_class="ROLLBACK",
                candidate_sha=facts.active_spec, active_sha=facts.active_spec,
                look_number=key_counter, verdict=verdict,
                decided_at=stats.watermark)
            written.append({**record, "conclusion": verdict.conclusion,
                            "class": "ROLLBACK"})

    for candidate_sha in view.candidates:
        if candidate_sha not in facts.candidate_registered_at:
            continue
        since = facts.candidate_registered_at[candidate_sha]
        stats = _optimize_forward_stats(
            conn, lineage, shadow_spec_sha256=candidate_sha,
            priced_model_version=state["active_model_version"],
            since=since, judged="shadow", with_close_deviation=True,
            stats_spec=candidate_sha, stats_comparator=facts.active_spec)
        prior = _optimize_prior_decisions(conn, lineage, "FORWARD",
                                          candidate_sha)
        key_counter = len(prior) + 1
        ordinal = len([row for row in prior if row["conclusion"] != "BLOCKED"])
        due = policy.forward_look_due(stats, threshold, ordinal)
        expired = (stats.watermark is not None and
                   stats.watermark - since >
                   _datetime.timedelta(days=threshold.max_shadow_days))
        looks.append({"class": "FORWARD", "spec": candidate_sha,
                      "due": bool(due or expired), "key_counter": key_counter,
                      "evidence_ordinal": ordinal,
                      "blocked_looks_consumed": key_counter - 1 - ordinal,
                      "n_markets": stats.n_markets})
        if (due or expired) and stats.watermark is not None:
            verdict = policy.decide_forward(
                facts, stats, look_number=key_counter, evidence_ordinal=ordinal,
                threshold=threshold, as_of=stats.watermark)
            record = _optimize_write_decision(
                conn, lineage=lineage, decision_class="FORWARD",
                candidate_sha=candidate_sha, active_sha=facts.active_spec,
                look_number=key_counter, verdict=verdict,
                decided_at=stats.watermark)
            written.append({**record, "conclusion": verdict.conclusion,
                            "class": "FORWARD"})
    return {"lineage": lineage, "looks": looks, "written": written}


def _optimize_forward_comparison(provider, scan_lineage, active_sha, active_version) -> dict:
    """Compare the registered active with the current scan provider selection."""
    selected = provider.specs_for_lineage(scan_lineage) if scan_lineage is not None else None
    pricing_sha = selected.active.spec_sha256() if selected is not None else None
    pricing_version = selected.active.model_version() if selected is not None else None
    fallback = selected.fallback_reason if selected is not None else None
    if active_sha is None:
        reason = "registry_not_initialized"
    elif selected is None:
        reason = "scan_lineage_unsupported"
    elif fallback is not None or active_version is None:
        reason = "pricing_identity_unavailable"
    elif active_sha != pricing_sha:
        reason = "pricing_spec_mismatch"
    else:
        reason = None
    return {
        "status": "available" if reason is None else "unavailable",
        "reason": reason,
        "scan_lineage": scan_lineage,
        "registered_active_spec_sha256": active_sha,
        "registered_active_model_version": active_version,
        "pricing_spec_sha256": pricing_sha,
        "pricing_model_version": pricing_version,
        "fallback_reason": fallback,
    }


def _optimize_score_lineage(conn, lineage: str, queue_ids, *,
                            pause_state_known: bool = True, provider=None,
                            with_close_price_evidence: bool = False) -> dict:
    """Descriptive forward statistics only -- no conclusion, ever."""
    from polymarket_alpha_lab.papersim.optimize import policy
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider

    family_id, target, threshold = _optimize_threshold(lineage)
    state = _optimize_lineage_facts(conn, lineage, queue_ids, allow_unavailable_active=True)
    facts: policy.LineageFacts = state["facts"]
    view = state["view"]
    if provider is None:
        provider = RegistryProvider.from_connection(conn)
    comparison = _optimize_forward_comparison(
        provider, _optimize_scan_lineage(family_id, target),
        facts.active_spec, state["active_model_version"])
    comparable = comparison["status"] == "available"
    priced_version = comparison["pricing_model_version"]
    pairs: list[dict] = []
    if facts.guard_spec is not None and facts.last_handover_at is not None:
        guard_stats = None
        if comparable:
            stats = _optimize_forward_stats(
                conn, lineage, shadow_spec_sha256=facts.guard_spec,
                priced_model_version=priced_version,
                since=facts.last_handover_at, judged="priced",
                with_close_deviation=False, stats_spec=facts.active_spec,
                stats_comparator=facts.guard_spec)
            guard_stats = _optimize_stats_dict(stats)
        pairs.append({"pair": "active-vs-guard",
                      "spec": facts.active_spec,
                      "comparator": facts.guard_spec,
                      "stats": guard_stats})
    for candidate_sha in view.candidates:
        if candidate_sha not in facts.candidate_registered_at:
            continue
        pair = {"pair": "candidate-vs-active", "spec": candidate_sha,
                "comparator": facts.active_spec, "stats": None, "next_look": None}
        pairs.append(pair)
        if with_close_price_evidence:
            pair["close_price_evidence"] = {"rule_version": policy.RULE_VERSION} if comparable else None
        if not comparable:
            continue
        stats = _optimize_forward_stats(
            conn, lineage, shadow_spec_sha256=candidate_sha,
            priced_model_version=priced_version,
            since=facts.candidate_registered_at[candidate_sha],
            judged="shadow", with_close_deviation=True,
            stats_spec=candidate_sha, stats_comparator=facts.active_spec,
            close_evidence=pair.get("close_price_evidence"))
        prior = _optimize_prior_decisions(conn, lineage, "FORWARD",
                                          candidate_sha)
        ordinal = len([row for row in prior if row["conclusion"] != "BLOCKED"])
        target_markets = (threshold.min_markets if ordinal == 0
                          else threshold.min_markets
                          * policy.SECOND_LOOK_SAMPLE_MULTIPLIER)
        pair.update(
            stats=_optimize_stats_dict(stats),
            next_look={
                "due": policy.forward_look_due(stats, threshold, ordinal),
                "evidence_ordinal": ordinal,
                "blocked_looks_consumed": len(prior) - ordinal,
                "target_markets": target_markets,
                "markets_to_next_look": max(target_markets
                                            - stats.n_markets, 0),
                "min_complete_blocks": threshold.min_complete_blocks,
                "min_span_days": threshold.min_span_days,
                "calendar_due": (
                    (threshold.min_complete_blocks is None or
                     stats.n_complete_blocks >= threshold.min_complete_blocks)
                    and (threshold.min_span_days is None or
                         stats.span_days >= threshold.min_span_days)),
            })
    return {
        "lineage": lineage,
        "roles": {"active": view.active, "baseline": view.baseline,
                  "candidates": list(view.candidates), "guard": view.guard},
        "frozen": facts.frozen,
        # Without a --config the pause state is UNKNOWN (null), never a
        # silent False -- the bindings live in the scan configs.
        "pause_state_known": pause_state_known,
        "lineage_paused_or_stopped": (facts.lineage_paused_or_stopped
                                      if pause_state_known else None),
        "last_handover_at": (None if facts.last_handover_at is None
                             else facts.last_handover_at.isoformat()),
        "active_queue_entries": state["entries"],
        "forward_comparison": comparison,
        "pairs": pairs,
    }


def _optimize_parse_time(value, error_code: str):
    """Parse an explicit ISO-8601 stamp (tz-aware) or fail closed."""
    if value is None:
        return None
    import datetime as _datetime

    if not isinstance(value, str):
        raise ValueError(error_code)
    try:
        stamp = _datetime.datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(error_code) from None
    if stamp.tzinfo is None:
        raise ValueError(error_code)
    return stamp


def _cmd_optimize_score(args) -> int:
    """Descriptive forward shadow statistics (read-only, no verdicts)."""
    try:
        queue_ids: list[str] = []
        has_config = bool(getattr(args, "config", None))
        include_rows = getattr(args, "close_price_evidence", False)
        if has_config:
            queue_ids = _optimize_queue_bindings(args.config, args.lineage)
        connection = _open_readonly_connection(
            args, label="PAPERSIM_OPTIMIZE_PG_DSN")
        try:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                report = _optimize_score_lineage(
                    connection, args.lineage, queue_ids,
                    pause_state_known=has_config,
                    with_close_price_evidence=include_rows)
                if include_rows:
                    cursor.execute("SELECT transaction_timestamp()")
                    report["read_transaction_started_at"] = cursor.fetchone()[0].isoformat()
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"optimize score: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


def _cmd_optimize_decide(args) -> int:
    """Apply the rule at every due look and write the decisions."""
    try:
        queue_ids = _optimize_queue_bindings(args.config, args.lineage)
        connection = _open_readonly_connection(
            args, label="PAPERSIM_OPTIMIZE_PG_DSN")
        try:
            from polymarket_alpha_lab.papersim import store_psycopg

            with store_psycopg.registry_write_transaction(connection):
                report = _optimize_decide_lineage(connection, args.lineage, queue_ids)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"optimize decide: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


def _optimize_decision_row(conn, decision_id: int) -> dict | None:
    """One promotion decision by id (the apply path's read face)."""
    if isinstance(decision_id, bool) or not isinstance(decision_id, int):
        raise ValueError("papersim_optimize_decision_invalid")
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(
            "SELECT decision_id, lineage, decision_class, "
            "candidate_spec_sha256, active_spec_sha256, conclusion, "
            "look_number, rule_version, decided_at, evidence_watermark, "
            "metrics, decided_by FROM "
            "papersim.promotion_decisions WHERE decision_id = %s",
            (decision_id,))
        columns = [description.name for description in cursor.description]
        row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row, strict=True))


_PROMOTION_CONCLUSIONS = frozenset({"PROMOTE", "PROMOTE_PENDING_CONFIRM"})
_PROMOTION_UNAVAILABLE = "papersim_optimize_apply_promotion_unavailable"


def _decision_roles(decision, view, *, manual_replay=False):
    """Bind every proposed role to the recorded judgment and its predecessor."""
    conclusion = decision["conclusion"]
    if conclusion in _PROMOTION_CONCLUSIONS:
        raise ValueError(_PROMOTION_UNAVAILABLE)
    if view is None:
        raise ValueError("papersim_optimize_decision_pair_mismatch")
    active = decision["active_spec_sha256"]
    candidate = decision["candidate_spec_sha256"]
    evidence = decision["evidence_watermark"]
    decision_class = decision["decision_class"]
    if conclusion == "ROLLBACK" and decision_class in {"ROLLBACK", "MANUAL"}:
        if view.guard is None:
            raise ValueError("papersim_optimize_apply_no_guard")
        legacy_manual = manual_replay and decision_class == "MANUAL" and set(evidence) == {"reason"}
        if (active != candidate or active != view.active or
                (not legacy_manual and
                 (evidence.get("spec") != active or evidence.get("comparator") != view.guard))):
            raise ValueError("papersim_optimize_decision_pair_mismatch")
        return [(active, "RETIRED"), (view.guard, "ACTIVE")]
    if decision_class != "FORWARD" or candidate not in view.candidates:
        raise ValueError("papersim_optimize_decision_pair_mismatch")
    expiry = conclusion == "RETIRE" and decision["metrics"].get("reason") == "shadow_window_expired"
    if evidence.get("spec") != candidate or evidence.get("comparator") not in (active, candidate):
        raise ValueError("papersim_optimize_decision_pair_mismatch")
    if not expiry and active != view.active:
        raise ValueError("papersim_optimize_decision_pair_mismatch")
    if conclusion == "RETIRE":
        return [(candidate, "RETIRED")]
    raise ValueError("papersim_optimize_apply_no_events")


def _decision_role_batch(decision, roles, actor, reason, commit):
    from polymarket_alpha_lab.papersim import store

    return store.RoleEventBatch(events=tuple(store.RoleEventSubmission(
        lineage=decision["lineage"], spec_sha256=digest, role=role,
        effective_at=decision["decided_at"], reason=reason, actor=actor,
        decision_id=decision["decision_id"], code_commit=commit,
    ) for digest, role in roles))


def _decision_predecessor(conn, lineage, instant):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize.registry import derive_lineage_roles

    batches = store_psycopg.load_all_role_events(conn).get(lineage, ())
    prefix = [batch for batch in batches if batch[0].effective_at < instant]
    return derive_lineage_roles(prefix), prefix


def _replay_decision_roles(conn, decision, actor, reason):
    from polymarket_alpha_lab.papersim import store_psycopg

    original = store_psycopg.load_decision_role_events(
        conn, decision["lineage"], decision["decision_id"])
    if not original:
        return None
    prior, _ = _decision_predecessor(conn, decision["lineage"], decision["decided_at"])
    roles = _decision_roles(decision, prior, manual_replay=True)
    batch = _decision_role_batch(decision, roles, actor, reason, original[0].code_commit)
    if (tuple(roles) != tuple((event.spec_sha256, event.role) for event in original)
            or any(event.actor != actor or event.reason != reason or
                   event.effective_at != decision["decided_at"] for event in original)):
        raise ValueError("papersim_optimize_decision_replay_conflict")
    return store_psycopg.record_role_events(conn, batch)


def _cmd_optimize_apply(args) -> int:
    """Apply or replay RETIRE and ROLLBACK role events.

    Promotion decisions are unavailable until scan pricing consumes the
    registered active. A first ROLLBACK checks freeze and pause against
    the fresh fold and uses the queue bindings from --config. Candidate
    retirement may proceed without those bindings.
    """
    from polymarket_alpha_lab.papersim import store_psycopg

    try:
        connection = _open_readonly_connection(args, label="PAPERSIM_OPTIMIZE_PG_DSN")
        try:
            with store_psycopg.registry_write_transaction(connection):
                decision = _optimize_decision_row(connection, args.decision_id)
                if decision is None:
                    raise ValueError("papersim_optimize_decision_unknown")
                conclusion = decision["conclusion"]
                if conclusion in _PROMOTION_CONCLUSIONS:
                    raise ValueError(_PROMOTION_UNAVAILABLE)
                lineage = decision["lineage"]
                actor = args.actor or f"auto:{decision['rule_version']}"
                if conclusion in ("CONTINUE", "HOLD", "BLOCKED"):
                    raise ValueError("papersim_optimize_apply_no_events")
                active_changing = conclusion != "RETIRE"
                queues = []
                if active_changing:
                    if not args.config:
                        raise ValueError("papersim_policy_queue_bindings_missing")
                    queues = _optimize_queue_bindings(args.config, lineage)
                commit = require_clean_worktree_path(args.repo_root)
                reason = (f"MO-8 {decision['decision_class']} look {decision['look_number']} "
                          f"decision {decision['decision_id']}")
                applied = _replay_decision_roles(connection, decision, actor, reason)
                if applied is None:
                    state = _optimize_lineage_facts(connection, lineage, queues)
                    if active_changing and state["facts"].frozen:
                        raise ValueError("papersim_optimize_apply_frozen")
                    if active_changing and state["facts"].lineage_paused_or_stopped:
                        raise ValueError("papersim_optimize_apply_paused")
                    # A legacy FORWARD decision can name its candidate twice in
                    # the watermark. Establish the actual pair at that decision.
                    evidence = decision["evidence_watermark"]
                    if (decision["decision_class"] == "FORWARD" and
                            evidence.get("comparator") == decision["candidate_spec_sha256"]):
                        prior, _ = _decision_predecessor(connection, lineage, decision["decided_at"])
                        if (prior is None or prior.active != decision["active_spec_sha256"] or
                                decision["candidate_spec_sha256"] not in prior.candidates):
                            raise ValueError("papersim_optimize_decision_pair_mismatch")
                    roles = _decision_roles(decision, state["view"])
                    batch = _decision_role_batch(decision, roles, actor, reason, commit)
                    applied = store_psycopg.record_role_events(connection, batch)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"optimize apply: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"applied": applied, "decision_id": args.decision_id,
                      "conclusion": conclusion, "actor": actor},
                     sort_keys=True, allow_nan=False))
    return 0


def _cmd_optimize_rollback(args) -> int:
    """Manual rollback: one MANUAL-class decision plus the rollback batch.

    The MANUAL decision's key candidate is the DEMOTED spec (P3-5): a
    retired spec can never re-register (registry candidate-not-new), so
    one row per demotion is structurally unique."""
    import datetime as _datetime

    from polymarket_alpha_lab.papersim import store, store_psycopg

    try:
        if not args.actor or args.actor.startswith("auto:"):
            raise ValueError("papersim_optimize_rollback_actor_invalid")
        at = _optimize_parse_time(args.at, "papersim_optimize_time_invalid")
        commit = require_clean_worktree_path(args.repo_root)
        connection = _open_readonly_connection(args, label="PAPERSIM_OPTIMIZE_PG_DSN")
        try:
            with store_psycopg.registry_write_transaction(connection):
                previous = [] if at is None else [
                    row for row in store_psycopg.load_promotion_decisions(connection, args.lineage)
                    if row["decision_class"] == "MANUAL" and row["decided_at"] == at]
                if previous:
                    if (len(previous) != 1 or previous[0]["decided_by"] != args.actor
                            or previous[0]["evidence_watermark"].get("reason") != args.reason):
                        raise ValueError("papersim_optimize_decision_replay_conflict")
                    decision = previous[0]
                    applied = _replay_decision_roles(connection, decision, args.actor, args.reason)
                    if applied is None:
                        raise ValueError("papersim_optimize_decision_replay_conflict")
                    written = {"decision_id": decision["decision_id"],
                               "decision_key": decision["decision_key"], "inserted": False}
                else:
                    state = _optimize_lineage_facts(connection, args.lineage, [])
                    view = state["view"]
                    if state["facts"].frozen:
                        raise ValueError("papersim_optimize_apply_frozen")
                    if view.guard is None:
                        raise ValueError("papersim_optimize_apply_no_guard")
                    instant = at or _datetime.datetime.now(_datetime.UTC)
                    prior, batches = _decision_predecessor(connection, args.lineage, instant)
                    if prior is None or prior.active != view.active or prior.guard != view.guard:
                        raise ValueError("papersim_optimize_decision_pair_mismatch")
                    references = {row.role: row.role_event_id for batch in batches for row in batch
                                  if (row.role, row.spec_sha256) in
                                  (("ACTIVE", view.active), ("GUARD", view.guard))}
                    if set(references) != {"ACTIVE", "GUARD"}:
                        raise ValueError("papersim_optimize_decision_pair_mismatch")
                    submission = store.PromotionDecisionSubmission(
                        lineage=args.lineage, decision_class="MANUAL",
                        candidate_spec_sha256=view.active, active_spec_sha256=view.active,
                        rule_version="manual-v1", look_number=1,
                        evidence_watermark={"reason": args.reason, "spec": view.active,
                                            "comparator": view.guard, "role_event_ids": references},
                        metrics={}, conclusion="ROLLBACK", block_reason=None,
                        decided_by=args.actor, decided_at=instant)
                    written = store_psycopg.record_promotion_decision(connection, submission)
                    decision = {"lineage": args.lineage, "decision_id": written["decision_id"],
                                "decided_at": instant}
                    batch = _decision_role_batch(decision, [(view.active, "RETIRED"),
                                                  (view.guard, "ACTIVE")],
                                                 args.actor, args.reason, commit)
                    applied = store_psycopg.record_role_events(connection, batch)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"optimize rollback: FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"decision": written, "applied": applied},
                     sort_keys=True, allow_nan=False))
    return 0


def _cmd_optimize_control(args, control_kind: str) -> int:
    """freeze/unfreeze: one optimizer_control_events row (MO-2 writer).

    FREEZE may carry an auto: actor (04 section 11 restricts only
    UNFREEZE to humans); UNFREEZE with an auto: actor is rejected by
    the three existing layers before any write.  The default
    occurred_at is the one deliberate wall-clock read of this command
    (documented): pass --occurred-at for a deterministic replay; the
    command echoes the idempotency key and row id either way."""
    import datetime as _datetime

    from polymarket_alpha_lab.papersim import store, store_psycopg

    try:
        connection = _open_readonly_connection(
            args, label="PAPERSIM_OPTIMIZE_PG_DSN")
        try:
            occurred_at = _optimize_parse_time(
                args.occurred_at,
                "papersim_optimize_time_invalid") or \
                _datetime.datetime.now(_datetime.UTC)
            submission = store.OptimizerControlEventSubmission(
                scope="LINEAGE" if args.lineage else "GLOBAL",
                lineage=args.lineage, control_kind=control_kind,
                reason=args.reason, actor=args.actor, occurred_at=occurred_at)
            # Belt beside braces: the writer validates again (and the
            # database CHECK is the third layer) -- validating BEFORE
            # the write keeps an invalid actor from ever reaching it.
            submission.validate()
            with store_psycopg.registry_write_transaction(connection):
                record = store_psycopg.record_optimizer_control_event(connection, submission)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"optimize {control_kind.lower()}: FAIL: {error}",
              file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True, allow_nan=False))
    return 0


def require_clean_worktree_path(repo_root):
    """The scan-module clean-worktree proof, re-exported for the MO-8
    role-event writers (role events carry a code_commit, B1 discipline)."""
    from polymarket_alpha_lab.papersim.scan import require_clean_worktree

    return require_clean_worktree(repo_root)


def _optimization_family(family_id):
    from polymarket_alpha_lab.papersim.optimize.runner import family_singleton

    try:
        return family_singleton(family_id)
    except KeyError:
        raise ValueError("papersim_optimize_family_unknown") from None


def _optimization_families():
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids

    return [_optimization_family(name) for name in iter_family_ids()]


def _optimization_catalog():
    applicability = {
        "club-soccer-frozen": "frozen_comparator",
        "crypto-gbm": "historical_inputs_pending_mo10",
        "league-bridge": "preseason_only",
    }
    result = []
    for family in _optimization_families():
        baseline = family.baseline_spec()
        scope = applicability.get(family.family_id, "historical")
        result.append({
            "family_id": family.family_id, "procedure_version": family.procedure_version,
            "targets": list(family.targets()), "applicability": scope,
            "searchable": scope == "historical",
            "param_space": [_jsonify(axis) for axis in family.param_space()],
            "baseline_spec_sha256": baseline.spec_sha256(),
            "baseline_model_version": baseline.model_version(),
        })
    return {"families": result}


def _optimization_status(conn, lineage_filter, config):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider
    from polymarket_alpha_lab.papersim.optimize.registry import (
        derive_lineage_roles, lineage_id,
    )

    families = {lineage_id(family.family_id, target): family
                for family in _optimization_families() for target in family.targets()}
    if lineage_filter is not None:
        if lineage_filter not in families:
            raise ValueError("papersim_optimize_lineage_unknown")
        families = {lineage_filter: families[lineage_filter]}
    reports = {}
    with conn.transaction():
        conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        history = store_psycopg.load_all_role_events(conn)
        freeze = store_psycopg.registry_freeze_state(conn)
        provider = RegistryProvider.from_connection(conn)
        for lineage, family in sorted(families.items()):
            batches = history.get(lineage, ())
            view = derive_lineage_roles(batches)
            registered = view is not None
            if registered:
                queue_ids = _optimize_queue_bindings(config, lineage) if config else []
                report = _optimize_score_lineage(conn, lineage, queue_ids,
                                                 pause_state_known=bool(config),
                                                 provider=provider)
                report["roles"] = view.as_dict()
                report["forward_plans"] = {
                    row.spec_sha256: row.forward_plan for batch in batches for row in batch
                    if row.role == "CANDIDATE" and row.spec_sha256 in view.candidates}
            else:
                baseline = family.baseline_spec().spec_sha256()
                family_id, target = lineage.split("/", 1)
                report = {"lineage": lineage, "roles": {"baseline": baseline, "active": None,
                          "guard": None, "candidates": [], "retired": []},
                          "fallback_spec_sha256": baseline, "forward_plans": {},
                          "pairs": [], "pause_state_known": False,
                          "forward_comparison": _optimize_forward_comparison(
                              provider, _optimize_scan_lineage(family_id, target), None, None),
                          "lineage_paused_or_stopped": None,
                          "frozen": bool(freeze["global_frozen"] or
                                         lineage in freeze["frozen_lineages"])}
            last = store_psycopg.latest_optimization_for_lineage(conn, lineage)
            report["last_run"] = last
            report["initialized"] = registered
            report["forward_sample_count"] = None
            reports[lineage] = report
    return {
        "lineages": reports,
        "freeze": {"global_frozen": freeze["global_frozen"],
                   "frozen_lineages": sorted(freeze["frozen_lineages"])},
    }


def _optimization_document(content, error_code):
    def unique(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise ValueError(error_code)
            record[key] = value
        return record

    def reject_constant(value):
        raise ValueError(error_code)

    try:
        result = json.loads(content.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ValueError(error_code) from None
    if not isinstance(result, dict):
        raise ValueError(error_code)
    return result


def _reject_legacy_optimization_input(path):
    invalid = "papersim_optimize_input_invalid"
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise ValueError(invalid) from None
    if not isinstance(document, dict):
        raise ValueError(invalid)

    # The legacy reader used iteration, which also accepted empty strings and maps.
    inputs = document.get("inputs", [])
    empty = inputs in ([], {}, "")
    if "archive_root" in document:
        if not empty or not isinstance(document["archive_root"], str):
            raise ValueError(invalid)
    elif empty:
        raise ValueError("papersim_optimize_inputs_empty")
    elif not isinstance(inputs, list) or any(
        not isinstance(entry, dict) or not {"path", "kind"} <= entry.keys()
        for entry in inputs
    ):
        raise ValueError(invalid)
    raise ValueError("papersim_optimize_input_manifest_required")


def _optimization_pointer(path):
    from polymarket_alpha_lab.papersim.snapshots import snapshot_digest_argument

    # The pointer holds one integer version, a byte length and a SHA-256 digest.
    with Path(path).open("rb") as stream:
        content = stream.read(4097)
    code = "papersim_optimize_manifest_pointer_invalid"
    if len(content) > 4096:
        raise ValueError(code)
    value = _optimization_document(content, code)
    if (set(value) != {"schema_version", "manifest_sha256", "manifest_byte_length"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["manifest_byte_length"]) is not int
            or value["manifest_byte_length"] <= 0):
        raise ValueError(code)
    digest = snapshot_digest_argument(value["manifest_sha256"])
    if digest != value["manifest_sha256"]:
        raise ValueError(code)
    return digest, value["manifest_byte_length"]


def _registered_optimization_manifest(conn, digest, size, protocol):
    from hashlib import sha256
    from pathlib import PurePosixPath
    from polymarket_alpha_lab.papersim import snapshots

    if protocol is None:
        from polymarket_alpha_lab.papersim import store_psycopg
        from polymarket_alpha_lab.papersim.optimize import runner

        content = store_psycopg._nba_odr_registration_snapshot_content(
            conn, runner.SourceArtifactRef(digest, size), kind="optimizer_experiment_manifest")
    else:
        row = snapshots.read_snapshot(conn, digest)
        if (row is None or row["kind"] != "optimizer_experiment_manifest"
                or row["schema_version"] != 1 or row["byte_length"] != size
                or len(row["content"]) != size or sha256(row["content"]).hexdigest() != digest):
            raise ValueError("papersim_optimize_manifest_snapshot_invalid")
        content = row["content"]
    code = "papersim_optimize_manifest_invalid"
    manifest = _optimization_document(content, code)
    def canonical(document):
        return json.dumps(document, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    if (set(manifest) != {"schema_version", "protocol", "sources"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or canonical(manifest) != content
            or not isinstance(manifest["sources"], list) or not manifest["sources"]):
        raise ValueError(code)
    if protocol is None:
        # The admitted NBA v2 cutoff binds the exact protocol to its registered materials.
        try:
            materials = manifest["protocol"]["registration_materials"]
            materials_ref = runner.SourceArtifactRef(materials["sha256"], materials["byte_length"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(code) from None
        protocol = runner.nba_odr_historical_protocol(materials_ref)
    if canonical(manifest["protocol"]) != canonical(protocol):
        raise ValueError(code)
    identities, types = set(), {}
    for source in manifest["sources"]:
        fields = {"role", "key", "kind", "schema_version", "sha256", "byte_length"}
        allowed = fields | {"required", "absent_reason", "provenance"}
        if not isinstance(source, dict) or not fields <= set(source) <= allowed:
            raise ValueError(code)
        role, key, kind = (source.get(field) for field in ("role", "key", "kind"))
        if any(not isinstance(v, str) or not v for v in (role, key, kind)):
            raise ValueError(code)
        relative = PurePosixPath(key)
        if (relative.is_absolute() or ".." in relative.parts or str(relative) != key or key == "."
                or ":" in key or "\\" in key or (role, key) in identities):
            raise ValueError(code)
        provenance = source.get("provenance", {})
        if (not isinstance(provenance, dict) or
                set(provenance) - {"clock_basis", "captured_at", "provider_published_at"}):
            raise ValueError(code)
        if "clock_basis" in provenance and provenance["clock_basis"] not in {
                "snapshot_generated_at", "provider_timestamp", "retained_archive_mtime"}:
            raise ValueError(code)
        for name in ("captured_at", "provider_published_at"):
            if name in provenance and _optimize_parse_time(provenance[name], code) is None:
                raise ValueError(code)
        identities.add((role, key))
        schema = source.get("schema_version")
        if (type(schema) is not int or schema < 1 or
                type(source.get("required", True)) is not bool):
            raise ValueError(code)
        length = source.get("byte_length")
        if source.get("sha256") is None:
            if (source.get("required") is not False or length is not None
                    or not isinstance(source.get("absent_reason"), str)
                    or not source["absent_reason"].strip()):
                raise ValueError(code)
            continue
        if "absent_reason" in source:
            raise ValueError(code)
        ref = snapshots.snapshot_digest_argument(source["sha256"])
        identity = (length, kind, schema)
        if (ref != source["sha256"] or ref == digest or type(length) is not int or length < 1
                or types.setdefault(ref, identity) != identity):
            raise ValueError(code)
    if not types:
        raise ValueError(code)
    return manifest, {ref: fields[0] for ref, fields in types.items()}


def _export_optimization_pointer(path, output):
    from os import fsync
    from tempfile import NamedTemporaryFile

    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".optimizer-pointer-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(output + "\n")
            stream.flush()
            fsync(stream.fileno())
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _cmd_optimize_experiment(args):
    import time

    start_ns = time.monotonic_ns()
    from polymarket_alpha_lab.papersim import store_psycopg

    command = args.optimize_command
    clock = None
    connection = None
    pointer_path = None
    exit_code = 0
    try:
        if command == "families":
            result = _optimization_catalog()
        else:
            if command == "run":
                if args.input is not None:
                    _reject_legacy_optimization_input(args.input)
                from polymarket_alpha_lab.papersim.optimize import runner
                from polymarket_alpha_lab.papersim.optimize.nba_odr import NBA_ODR_AS_OF

                clock = store_psycopg.optimization_command_clock(start_ns)
                family = _optimization_family(args.family)
                protocol = runner.historical_protocol(family)
                cutoff = _optimize_parse_time(args.as_of, "papersim_optimize_time_invalid")
                nba_odr_v2 = family.family_id == "nba-pace-efficiency" and cutoff == NBA_ODR_AS_OF
                expected_seed = runner._NBA_ODR_SEED if nba_odr_v2 else protocol["seed"]
                expected_cutoff = NBA_ODR_AS_OF if nba_odr_v2 else _optimize_parse_time(
                    protocol["data_cutoff"], "papersim_optimize_time_invalid")
                seed = expected_seed if args.seed is None else args.seed
                if seed != expected_seed or args.attempt < 1 or cutoff != expected_cutoff:
                    raise ValueError("papersim_optimize_protocol_mismatch")
                if nba_odr_v2 and not args.managed:
                    raise ValueError("papersim_optimize_nba_odr_managed_required")
                digest, size = _optimization_pointer(args.input_manifest)
            if command == "register-experiment":
                from polymarket_alpha_lab.papersim.nba_odr_registration import _pinned_originals

                clock = store_psycopg.optimization_command_clock(start_ns)
                repo_root = Path(args.repo_root).resolve()
                if args.write_pointer is not None:
                    pointer_path = Path(args.write_pointer)
                    pointer_path = pointer_path.parent.resolve() / pointer_path.name
                    if pointer_path.is_relative_to(repo_root):
                        raise ValueError("papersim_optimize_manifest_pointer_inside_worktree")
                originals = _pinned_originals()
                with Path(args.approval_file).open("rb") as stream:
                    approval_bytes = stream.read(originals["approval"].ref.byte_length + 1)
                with Path(args.source_inventory_file).open("rb") as stream:
                    source_inventory_bytes = stream.read(originals["source_inventory"].ref.byte_length + 1)
            if command in {"run", "init", "register-experiment"}:
                code_commit = require_clean_worktree_path(args.repo_root)
            def connect():
                kwargs = {} if clock is None else {"clock": clock}
                return _open_readonly_connection(args, label="PAPERSIM_OPTIMIZE_PG_DSN", **kwargs)
            connection = connect()
            if command == "init":
                families = ([_optimization_family(name) for name in args.families]
                            if args.families else _optimization_families())
                effective_at = (_optimize_parse_time(args.effective_at, "papersim_optimize_time_invalid")
                                or datetime.datetime.now(datetime.UTC))
                with store_psycopg.registry_write_transaction(connection):
                    result = store_psycopg.initialize_registry(
                        connection, families=families, actor=args.actor,
                        code_commit=code_commit, effective_at=effective_at)
            elif command == "status":
                status = _optimization_status(connection, args.lineage, args.config)
                result = {**status, "lineages": list(status["lineages"].values()),
                          "shadow_predictions": "paired_by_lineage"}
            elif command == "register-experiment":
                registration = store_psycopg.register_managed_nba_odr_experiment(
                    connection, clock=clock, repo_root=repo_root, code_commit=code_commit,
                    approval_bytes=approval_bytes, source_inventory_bytes=source_inventory_bytes,
                    reconnect=connect)
                result = registration.pointer
            else:
                with store_psycopg.optimization_read_transaction(
                        connection, clock, stage="manifest_read", closeout=True):
                    manifest, refs = _registered_optimization_manifest(
                        connection, digest, size, None if nba_odr_v2 else protocol)
                if nba_odr_v2:
                    protocol = manifest["protocol"]

                request = dict(clock=clock, family=family, data_cutoff=cutoff,
                               random_seed=seed, code_commit=code_commit, attempt=args.attempt,
                               targets=protocol["targets"], schedule_id=protocol["schedule_id"],
                               manifest_sha256=digest, manifest_byte_length=size, manifest=manifest,
                               source_refs=refs, operation_key=args.operation_key)
                if args.dry_run:
                    receipt = store_psycopg.preview_managed_optimization(connection, **request)
                elif nba_odr_v2:
                    receipt = store_psycopg.run_managed_optimization(connection, **request, reconnect=connect)
                else:
                    def compute(*, policy_context, deadline, source_artifacts):
                        arguments = runner.historical_run_arguments(
                            family=family, manifest=manifest, source_artifacts=source_artifacts,
                            deadline=deadline)
                        return runner.execute_optimization_run(
                            **arguments, data_cutoff=cutoff, random_seed=seed, code_commit=code_commit,
                            policy_context=policy_context, deadline=deadline,
                            source_artifacts=source_artifacts, snapshot_refs=tuple(sorted(refs)))

                    receipt = store_psycopg.run_managed_optimization(
                        connection, **request, compute=compute, reconnect=connect)
                result = {k: v for k, v in receipt.items() if k != "artifact_bytes"}
                if nba_odr_v2:
                    exit_code = 0 if args.dry_run else receipt["exit_code"]
                else:
                    exit_code = int(result["outcome"] == "RUN_FAILED")
        output = json.dumps(_jsonify(result), sort_keys=True, allow_nan=False)
        if pointer_path is not None:
            try:
                _export_optimization_pointer(pointer_path, output)
            except OSError:
                raise ValueError("papersim_optimize_manifest_pointer_write_failed") from None
    except Exception as error:
        # Surface only project reason codes and proven identifiers, never raw input or DSNs.
        import re
        reason = str(error)
        if not re.fullmatch(r"papersim_[a-z0-9_]+", reason):
            reason = "papersim_optimize_input_or_store_failure"
        details = {k: getattr(error, k) for k in ("stage", "retryable", "indeterminate",
                   "started_event_id", "attempt_event_id") if hasattr(error, k)}
        if reason == "papersim_optimize_input_manifest_required":
            details["required_option"] = "--input-manifest"
        print(f"optimize {command}: FAIL: {reason} {json.dumps(details, sort_keys=True)}",
              file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(output)
    return exit_code


def _cmd_optimize(args) -> int:
    command = args.optimize_command
    if command in {"families", "init", "register-experiment", "run", "status"}:
        return _cmd_optimize_experiment(args)
    if command == "score":
        return _cmd_optimize_score(args)
    if command == "decide":
        return _cmd_optimize_decide(args)
    if command == "apply":
        return _cmd_optimize_apply(args)
    if command == "rollback":
        return _cmd_optimize_rollback(args)
    if command == "freeze":
        return _cmd_optimize_control(args, "FREEZE")
    if command == "unfreeze":
        return _cmd_optimize_control(args, "UNFREEZE")
    print(f"optimize: FAIL: unknown optimize command {command!r}",
          file=sys.stderr)
    return 2


#: MO-8 / MO-7 review P3-2: the run-scan shadow budget default.  An
#: OPERATIONAL default (seconds), deliberately NOT one of the
#: registered rule numbers of DELIVERY_PLAN 80.7 -- those live only in
#: optimize/policy.py.
RUN_SCAN_SHADOW_BUDGET_SECONDS = 30.0


# ---------------------------------------------------------------------------
# report (B4: the fourth M2 operational command; plan 05 section 2 /
# DELIVERY_PLAN 80.8)
# ---------------------------------------------------------------------------


def _wired_subcommands() -> frozenset[str]:
    """The real runtime subcommand registry (B4 report wiring evidence).

    The report labels a pipeline stage ``STAGE_NOT_WIRED`` exactly when
    its M2 command is absent from the CLI, so the wired set must be the
    live parser registry, never a hand-maintained copy that could drift.
    Argparse exposes the registered subcommands only through the
    ``choices`` mapping of its subparsers action, so the set is derived
    from a freshly built parser.
    """
    subparsers = next(
        action for action in _build_parser()._actions
        if isinstance(action, argparse._SubParsersAction))
    return frozenset(subparsers.choices)


def _cmd_report(args) -> int:
    # B4 (plan 05 section 2 / DELIVERY_PLAN 80.8): every heavy import
    # stays inside the function body so the CLI startup module count is
    # untouched (FX-20; tests/test_init_lazy_imports.py pins the limit).
    from collections import Counter
    from dataclasses import replace
    from functools import partial
    from polymarket_alpha_lab.papersim import evaluation, report, store_psycopg

    try:
        connection = _open_readonly_connection(args, label="PAPERSIM_REPORT_PG_DSN")
        try:
            # Driver order (B4 contract): ONE repeatable-read read-only
            # snapshot feeds every input -- pipeline receipts, scan runs,
            # latch rows, checks, coverage scalars AND the evaluation
            # candidate queue through the same store loader the evaluate
            # command uses (one metric face, one read) -- then the read
            # transaction closes before the pure assembly, and only then
            # the paper-evidence writes happen: STARTED receipt, one
            # papersim-report snapshot of the canonical bytes, SUCCEEDED
            # receipt carrying the content digest.  The operation
            # identity is the content-addressed report key, so a replay
            # over unchanged evidence inserts nothing (no duplicate
            # snapshot row, no duplicate succeeded receipt).
            evaluation_times = {}
            pending_settlements = set()
            state = store_psycopg.read_pipeline_state(
                connection, evaluation_loader=partial(
                    _evaluation_candidates_in_snapshot, evidence_times=evaluation_times,
                    pending_settlements=pending_settlements))
            candidates = state["evaluation_candidates"]
            pending_by_queue = Counter()
            if pending_settlements:
                # The market result is known. Only the trade's booked result
                # and settlement time are pending in this report snapshot.
                reported = []
                for candidate in candidates:
                    if candidate.candidate_id in pending_settlements:
                        pending_by_queue[(candidate.model_key, candidate.market_type, candidate.gate_version)] += 1
                        candidate = replace(candidate, outcome=None, settled_time=None)
                    reported.append(candidate)
                candidates = reported
            queue_report = evaluation.evaluate_queues(candidates)
            inputs = report.ReportInputs(
                scope=args.scope,
                wired_commands=_wired_subcommands(),
                command_run_events=state["command_run_events"],
                scan_runs=state["scan_runs"],
                queue_control_events=state["queue_control_events"],
                bankroll_checks=state["bankroll_checks"],
                coverage=report.PipelineCoverage(**state["coverage"]),
                stage_evidence_times={**state["stage_evidence_times"], **evaluation_times},
                basketball_evidence=state["basketball"],
                pending_recorded_by_queue=pending_by_queue,
                evaluation=queue_report)
            assembly = report.assemble_report(inputs)
            store_psycopg.archive_report(connection, assembly, attempt=args.attempt)
        finally:
            connection.close()
    except (ValueError, OSError) as error:
        print(f"report: FAIL: {error}", file=sys.stderr)
        return 1
    # stdout is presentation only: the exact canonical bytes that were
    # registered (durable evidence lives in papersim.snapshots).
    print(assembly.content.decode("utf-8"))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="papersim",
        description=(
            "Paper-simulation core library CLI (paper-only / report-only / "
            "readonly; no order, account or credential surface)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    euro_parser = subparsers.add_parser(
        "euro-soccer", help="collect or summarize prospective European football research",
    )
    euro_parser.add_argument("action", choices=("scan", "status"))
    euro_parser.add_argument("--repo-root", default=".", help="root of the managed research instance")
    subparsers.add_parser(
        "selfcheck",
        help="recompute the frozen regression anchors; exit 0 only if all pass",
    )
    subparsers.add_parser(
        "schema",
        help="emit the paper-sim ledger migration DDL verbatim",
    )
    subparsers.add_parser(
        "validate-dsn",
        help="report-only validation of the ledger DSN environment variable (never connects)",
    )
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="FX-11 queue evaluation over recorded paper evidence (read-only)",
        description=(
            "Evaluate paper-sim queues (scoring, OD-G promotion verdicts, "
            "pause suggestions) from the managed papersim store, an explicit "
            "isolated-cluster DSN, or an explicit JSON candidate file. "
            "Read-only: no order, account or credential surface."
        ),
    )
    evaluate_parser.add_argument(
        "--pnl-basis", choices=("recorded", "recomputed"), default="recorded",
        help="gross-PnL basis for ROI metrics (default: recorded; store reads "
             "take the recorded settlement PnL from papersim.settlements when "
             "present — an entered row without a settlement record fails "
             "loudly in recorded mode instead of being recomputed)",
    )
    evaluate_source = evaluate_parser.add_mutually_exclusive_group(required=True)
    evaluate_source.add_argument(
        "--managed", action="store_true",
        help="read through the project-private managed application instance",
    )
    evaluate_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    evaluate_source.add_argument(
        "--json-input",
        help="explicit JSON candidate file (list or {'candidates': [...]}; "
             "no connection is opened)",
    )
    evaluate_parser.add_argument(
        "--repo-root", default=".",
        help="repository root of the managed instance (default: .)",
    )
    replay_parser = subparsers.add_parser(
        "replay",
        help="FX-12 forensic replay of one recorded candidate (read-only)",
        description=(
            "Re-read one recorded candidate, verify its snapshots, code "
            "commit, policy fingerprint and decision-time evidence, recompute "
            "decide_entry in this reviewed environment and compare every "
            "field. The recorded code commit is compared against the "
            "operator-supplied --trusted-commit, never the ambient HEAD. "
            "Read-only: no order, account or credential surface."
        ),
    )
    replay_parser.add_argument(
        "candidate_id", type=int,
        help="candidate_id of the recorded candidate to replay",
    )
    replay_parser.add_argument(
        "--trusted-commit", required=True,
        help="the reviewed 40-hex code commit the record must match",
    )
    replay_source = replay_parser.add_mutually_exclusive_group(required=True)
    replay_source.add_argument(
        "--managed", action="store_true",
        help="read through the project-private managed application instance",
    )
    replay_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    replay_parser.add_argument(
        "--repo-root", default=".",
        help="repository root of the managed instance (default: .)",
    )
    run_scan_parser = subparsers.add_parser(
        "run-scan",
        help="B1 paper scan: candidates, entries and cash events in one atomic write",
        description=(
            "Run one paper scan (plan 05 section 2 / DELIVERY_PLAN 80.8): "
            "refuse a dirty worktree (FX-03), consult the model-spec "
            "provider once per lineage (empty registry = frozen baseline, "
            "bit-identical output), evaluate the FX-17 pause admission, "
            "decide every prepared market through decide_entry with FX-26 "
            "event-key binding, and write run row + candidates + entries + "
            "cash legs in ONE transaction. Markets come from prepared "
            "pack files named by the scan config; live adapter wiring "
            "arrives with the adapter lane. Paper-only: no order, "
            "account or credential surface."
        ),
    )
    run_scan_parser.add_argument(
        "--config", required=True,
        help="scan config JSON (team, operator, queues with league scope, "
             "data-source version and prepared-market pack paths)",
    )
    run_scan_source = run_scan_parser.add_mutually_exclusive_group(required=True)
    run_scan_source.add_argument(
        "--managed", action="store_true",
        help="write through the project-private managed application instance",
    )
    run_scan_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    run_scan_parser.add_argument(
        "--repo-root", default=".",
        help="repository root for the clean-worktree commit check and the "
             "managed instance (default: .)",
    )
    run_scan_parser.add_argument(
        "--operation-key", default=None,
        help="stable scheduling identity of this unattended run (default: "
             "run-scan|<team>|<started UTC stamp>|b1)",
    )
    run_scan_parser.add_argument(
        "--prepare-from", metavar="SHA256",
        help="sealed preparation input set (requires --managed and --operation-key)",
    )
    run_scan_parser.add_argument(
        "--attempt", type=int, default=1,
        help="attempt number of this operation for the run receipts (default: 1)",
    )
    run_scan_parser.add_argument(
        "--shadow-budget-seconds", type=float,
        default=RUN_SCAN_SHADOW_BUDGET_SECONDS,
        help="wall-clock budget of the shadow side; over budget the scan "
             "drops NEW shadow work, never the active path (default: "
             f"{RUN_SCAN_SHADOW_BUDGET_SECONDS}s, an operational default "
             "NOT a registered rule number)",
    )
    capture_parser = subparsers.add_parser(
        "capture-marks",
        help="B2 mark capture: periodic and close marks for ALL candidates",
        description=(
            "Capture periodic and close marks for every recorded candidate "
            "(plan 05 section 2 / DELIVERY_PLAN 80.8; FX-34): refuse a dirty "
            "worktree (FX-03), register each mark pack's raw bytes in "
            "papersim.snapshots before normalization, classify every "
            "observation against the candidate's FX-26 event_start_at (a "
            "CLOSE must be observed strictly before the event start -- an "
            "NBA full-game mark during overtime stays PERIODIC; a candidate "
            "without an event start produces no CLOSE), and write "
            "candidate_marks rows (plus CLOSE price evidence) through "
            "mark_capture_upsert. Marks come from prepared pack files named "
            "by the capture config; the command itself opens no network "
            "session. The coverage summary (candidates seen, marks written, "
            "CLOSE eligible, downgraded-to-periodic, missing event start, "
            "missing quote) goes to the SUCCEEDED receipt and stdout. "
            "Paper-only: no order, account or credential surface."
        ),
    )
    capture_parser.add_argument(
        "--config", required=True,
        help="capture config JSON (team, operator, packs: prepared-mark "
             "pack paths relative to the config file)",
    )
    capture_source = capture_parser.add_mutually_exclusive_group(required=True)
    capture_source.add_argument(
        "--managed", action="store_true",
        help="write through the project-private managed application instance",
    )
    capture_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    capture_parser.add_argument(
        "--repo-root", default=".",
        help="repository root for the clean-worktree commit check and the "
             "managed instance (default: .)",
    )
    capture_parser.add_argument(
        "--operation-key", default=None,
        help="stable scheduling identity of this unattended run (default: "
             "capture-marks|<team>|<started UTC stamp>|b2)",
    )
    capture_parser.add_argument(
        "--attempt", type=int, default=1,
        help="attempt number of this operation for the run receipts (default: 1)",
    )
    capture_parser.add_argument(
        "--inspect-close-window", metavar="BATCH_SHA256",
        help="read-only comparison against the original quote receipt, bought side and "
             "market boundary in this sealed managed batch; write the evidence JSON to stdout",
    )
    settle_parser = subparsers.add_parser(
        "settle",
        help="B3 paper settlement: per-entry natural/voided settlement from captured evidence",
        description=(
            "Settle open paper entries (plan 05 section 2 / DELIVERY_PLAN "
            "80.8): refuse a dirty worktree (FX-03), read the open entries "
            "and their EFFECTIVE captured final-outcome evidence in ONE "
            "repeatable-read read-only snapshot (FX-29), derive each "
            "settlement purely from the captured outcome token (NATURAL "
            "win/loss via the ledger, VOIDED stake refund; basketball "
            "full-game semantics are OT-inclusive because only "
            "completed-game evidence settles), and write one idempotent "
            "settlement row plus its single cash leg per entry (STARTED/"
            "SUCCEEDED/FAILED run receipts). An entry without captured "
            "terminal evidence stays open and is reported PENDING with a "
            "reason; early EXIT settlement stays operator-attested. "
            "Paper-only: no order, account or credential surface."
        ),
    )
    settle_parser.add_argument(
        "--team", required=True,
        help="team identity feeding the run receipts and the default operation key",
    )
    settle_source = settle_parser.add_mutually_exclusive_group(required=True)
    settle_source.add_argument(
        "--managed", action="store_true",
        help="write through the project-private managed application instance",
    )
    settle_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    settle_parser.add_argument(
        "--repo-root", default=".",
        help="repository root for the clean-worktree commit check and the "
             "managed instance (default: .)",
    )
    settle_parser.add_argument(
        "--operator", default="unattended",
        help="operator identity recorded on every settlement (default: unattended)",
    )
    settle_parser.add_argument(
        "--operation-key", default=None,
        help="stable scheduling identity of this unattended run (default: "
             "settle|<team>|<started UTC stamp>|b3)",
    )
    settle_parser.add_argument(
        "--attempt", type=int, default=1,
        help="attempt number of this operation for the run receipts (default: 1)",
    )
    optimize_parser = subparsers.add_parser(
        "optimize",
        help="model auto-optimization: shadow statistics, promotion "
             "decisions, role events and freeze control (MO-8; "
             "paper-only / report-only / readonly)",
    )
    optimize_sub = optimize_parser.add_subparsers(
        dest="optimize_command", required=True)

    def _optimize_source(parser, *, write: bool, required: bool = False):
        sources = parser.add_mutually_exclusive_group(required=required)
        sources.add_argument(
            "--managed", action="store_true",
            help="operate through the project-private managed application "
                 "instance",
        )
        sources.add_argument(
            "--dsn",
            help="explicit DSN for an isolated/throwaway cluster "
                 "(validated by the single audited local DSN validator)",
        )
        parser.add_argument("--repo-root", default=".",
                            help="repository root (default: .)")
        return parser

    optimize_sub.add_parser("families", help="list the registered model families and search spaces")
    init_parser = _optimize_source(optimize_sub.add_parser("init", help="initialize the registered baselines"),
                                   write=True, required=True)
    init_parser.add_argument("--families", nargs="*", help="initialize only these family IDs")
    init_parser.add_argument("--actor", default="optimize-init")
    init_parser.add_argument("--effective-at", help="explicit timezone-aware initialization time")
    register_parser = optimize_sub.add_parser(
        "register-experiment", help="register the approved NBA historical experiment")
    register_parser.add_argument("--managed", action="store_true", required=True,
                                 help="use the project-private managed application instance")
    register_parser.add_argument("--repo-root", default=".", help="repository root (default: .)")
    register_parser.add_argument("--approval-file", required=True, help="original owner approval document")
    register_parser.add_argument("--source-inventory-file", required=True, help="original source inventory")
    register_parser.add_argument("--write-pointer", help="atomically export the manifest pointer outside the worktree")
    run_parser = _optimize_source(optimize_sub.add_parser(
        "run", help="evaluate a registered historical experiment"), write=True)
    run_parser.add_argument("--family", required=True)
    run_parser.add_argument("--as-of", required=True)
    run_input = run_parser.add_mutually_exclusive_group(required=True)
    run_input.add_argument("--input-manifest", help="JSON pointer to the registered experiment manifest")
    run_input.add_argument("--input", help="previous input format; new runs require --input-manifest")
    run_parser.add_argument("--seed", type=int, help="registered seed (defaults to the experiment's seed)")
    run_parser.add_argument("--attempt", type=int, default=1)
    run_parser.add_argument("--operation-key", help="verify the operation key derived from the registered request")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="verify the request or read its sealed result without running the models")
    status_parser = _optimize_source(optimize_sub.add_parser(
        "status", help="show registered roles, progress and the latest linked result"),
        write=False, required=True)
    status_parser.add_argument("--lineage")
    status_parser.add_argument("--config", help="scan config with the queue-to-lineage bindings")

    score_parser = _optimize_source(optimize_sub.add_parser(
        "score",
        help="forward shadow statistics of one lineage (read-only, "
             "descriptive; never a verdict)",
    ), write=False)
    score_parser.add_argument("--lineage", required=True,
                              help="lineage id family_id/target")
    score_parser.add_argument(
        "--close-price-evidence", action="store_true",
        help="include the selected paper CLOSE, shadow fill and active entry for each forward comparison row")
    score_parser.add_argument(
        "--config", default=None,
        help="scan config JSON (queue->lineage bindings for the pause "
             "state; without it the pause state reports UNKNOWN fields "
             "only)")

    decide_parser = _optimize_source(optimize_sub.add_parser(
        "decide",
        help="apply rule v1 at every due look and write the decisions "
             "(idempotent per evidence watermark)",
    ), write=True)
    decide_parser.add_argument("--lineage", required=True,
                               help="lineage id family_id/target")
    decide_parser.add_argument(
        "--config", required=True,
        help="scan config JSON carrying the queue->lineage bindings of "
             "the pause check (P2-3)")

    apply_parser = _optimize_source(optimize_sub.add_parser(
        "apply",
        help="apply or replay a recorded ROLLBACK or RETIRE decision",
    ), write=True)
    apply_parser.add_argument("--decision-id", type=int, required=True)
    apply_parser.add_argument(
        "--config", default=None,
        help="scan config JSON carrying the queue->lineage bindings of "
             "the pause re-verification; REQUIRED for active-changing "
             "applies (fail-closed without it, the same loader decide "
             "uses); RETIRE-only applies need no config")
    apply_parser.add_argument("--actor", default=None,
                              help="applying actor (default auto:<rule version>)")
    apply_parser.add_argument("--confirm", action="store_true",
                              help="owner confirmation flag (promotion application unavailable)")

    rollback_parser = _optimize_source(optimize_sub.add_parser(
        "rollback",
        help="manual rollback to the guard: one MANUAL decision plus the "
             "rollback batch (human actor required)",
    ), write=True)
    rollback_parser.add_argument("--lineage", required=True)
    rollback_parser.add_argument("--reason", required=True)
    rollback_parser.add_argument("--actor", required=True,
                                 help="the human deciding the rollback")
    rollback_parser.add_argument(
        "--at", default=None,
        help="explicit decision/effective time (ISO-8601); default is the "
             "invocation moment (the command's one deliberate wall-clock "
             "read)")

    def _control(kind, help_text):
        parser = _optimize_source(optimize_sub.add_parser(kind,
                                                          help=help_text),
                                  write=True)
        parser.add_argument(
            "--lineage", default=None,
            help="scope the event to one lineage (default GLOBAL)")
        parser.add_argument("--reason", required=True)
        parser.add_argument(
            "--actor", required=True,
            help="deciding actor; an UNFREEZE actor may never start with "
                 "'auto:' (three enforcement layers)")
        parser.add_argument(
            "--occurred-at", default=None,
            help="explicit event time (ISO-8601) for deterministic "
                 "replays; default is the invocation moment (the one "
                 "deliberate wall-clock read, echoed with the row id)")
        return parser

    _control("freeze", "freeze the optimizer (global or one lineage); a "
                       "freeze blocks registrations, handovers and applies "
                       "of active-changing batches")
    _control("unfreeze", "unfreeze (human actor only; never an automatic "
                         "rule)")

    report_parser = subparsers.add_parser(
        "report",
        help="B4 pipeline report: the capture->settle stage matrix from stored evidence",
        description=(
            "Assemble one pipeline report (plan 05 section 2 / DELIVERY_PLAN "
            "80.8): read every pipeline receipt, scan run, queue-control "
            "latch row, bankroll check and honest coverage scalar inside "
            "ONE repeatable-read read-only transaction, reuse the evaluate "
            "command's queue metrics as the single metric face, and present "
            "the capture->settle stages in causal order. Stages whose "
            "command is not wired stay STAGE_NOT_WIRED and missing evidence "
            "is never zero-filled or synthesized. The canonical report bytes "
            "are registered as one papersim-report snapshot plus report run "
            "receipts (paper-evidence writes only). Paper-only / "
            "report-only / readonly: no order, account or credential "
            "surface."
        ),
    )
    report_source = report_parser.add_mutually_exclusive_group(required=True)
    report_source.add_argument(
        "--managed", action="store_true",
        help="read through the project-private managed application instance",
    )
    report_source.add_argument(
        "--dsn",
        help="explicit DSN for an isolated/throwaway cluster "
             "(validated by the single audited local DSN validator)",
    )
    report_parser.add_argument(
        "--repo-root", default=".",
        help="repository root of the managed instance (default: .)",
    )
    report_parser.add_argument(
        # Equals report.REPORT_SCOPE_ALL ("*"); the report module is
        # imported lazily at run time, so the parser states the literal
        # default (FX-20 startup-module cap).
        "--scope", default="*",
        help="report scope stamped into the document and its "
             "content-addressed key (default: * = the whole managed store)",
    )
    report_parser.add_argument(
        "--attempt", type=int, default=1,
        help="attempt number of this operation for the report receipts (default: 1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the papersim CLI; returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "euro-soccer":
        from .euro_soccer_study import command

        return command(args.action, args.repo_root)
    if args.command == "selfcheck":
        return _cmd_selfcheck()
    if args.command == "schema":
        return _cmd_schema()
    if args.command == "validate-dsn":
        return _cmd_validate_dsn()
    if args.command == "evaluate":
        return _cmd_evaluate(args)
    if args.command == "replay":
        return _cmd_replay(args)
    if args.command == "run-scan":
        return _cmd_run_scan(args)
    if args.command == "optimize":
        if args.optimize_command == "run" and args.input_manifest is not None:
            if not args.managed and args.dsn is None:
                parser.error("optimize run requires --managed or --dsn")
        return _cmd_optimize(args)
    if args.command == "settle":
        return _cmd_settle(args)
    if args.command == "capture-marks":
        return _cmd_capture_marks(args)
    if args.command == "report":
        return _cmd_report(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    raise AssertionError("unreachable")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
