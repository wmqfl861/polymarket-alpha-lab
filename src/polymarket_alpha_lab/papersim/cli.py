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
    "(c.order_book is not null) AS has_order_book, "
    "st.classification, st.win_loss, st.exit_price, st.proceeds, st.pnl_gross, "
    "st.fee_total, st.pnl_net, st.confirmed_at "
    "FROM papersim.candidates c JOIN papersim.scan_runs s ON s.scan_id = c.scan_id "
    "LEFT JOIN papersim.entries e ON e.candidate_id = c.candidate_id "
    "LEFT JOIN papersim.settlements st ON st.entry_id = e.entry_id "
    "ORDER BY c.candidate_id"
)
_EVAL_EVIDENCE_SQL = (
    "SELECT candidate_id, evidence_role, model_probability, mid, bid, ask, last "
    "FROM papersim.candidate_price_evidence ORDER BY evidence_id"
)
_EVAL_OUTCOMES_SQL = (
    "SELECT outcome_id, condition_id, market_id, market_slug, outcome, observed_at "
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


def _open_readonly_connection(args, *, label: str):
    """Read-only source connection through the reviewed papersim adapter.

    ``--managed`` opens the project-private managed application face
    (its DSN already passed the single audited local validator inside
    the adapter, before any connection is constructed); ``--dsn`` is an
    explicit DSN for an isolated/throwaway cluster validated by the
    same single audited validator. This module opens no connection
    itself and never reads the process environment.
    """
    from polymarket_alpha_lab.papersim import store_psycopg
    if args.managed:
        return store_psycopg.connect_managed(Path(args.repo_root))
    return store_psycopg.connect_explicit(args.dsn, label=label)


def _fetch_rows(conn, sql: str) -> list[dict]:
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [description.name for description in cursor.description]
        rows = cursor.fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


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
    row WITHOUT a settlement record keeps the market natural result for
    the recomputed basis and carries no recorded PnL, so the recorded
    basis fails loudly on it (honest gap, never zero-filled).
    ``book_walk`` provenance requires the candidate's recorded walked
    book; legacy imports (no stored order book) stay ``recorded_price``.
    """
    from polymarket_alpha_lab.papersim import evaluation, ledger, store

    candidate_rows = _fetch_rows(conn, _EVAL_CANDIDATES_SQL)
    decision_evidence: dict[int, dict] = {}
    close_evidence: dict[int, dict] = {}
    for row in _fetch_rows(conn, _EVAL_EVIDENCE_SQL):
        candidate_id = int(row["candidate_id"])
        if row["evidence_role"] == "DECISION":
            decision_evidence.setdefault(candidate_id, row)
        elif row["evidence_role"] == "CLOSE":
            close_evidence[candidate_id] = row
    observations: dict[str, list[dict]] = {}
    for row in _fetch_rows(conn, _EVAL_OUTCOMES_SQL):
        for column in ("condition_id", "market_id", "market_slug"):
            identity = row[column]
            if identity is not None:
                observations.setdefault(identity, []).append(row)
    built = []
    for row in candidate_rows:
        candidate_id = int(row["candidate_id"])
        decision = decision_evidence.get(candidate_id)
        close = close_evidence.get(candidate_id)
        identity = row["condition_id"] or row["market_id"] or row["market_slug"]
        outcome, settled_at = None, None
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
        entered = row["stake"] is not None
        recorded_pnl = None
        classification = row["classification"] if entered else None
        if classification is not None:
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
        built.append(evaluation.EvaluationCandidate(
            candidate_id=str(candidate_id),
            model_key=f"{row['model_name']}:{row['model_version']}",
            market_type=row["market_type"] or row["market_kind"],
            gate_version=row["gate_version"],
            market_key=identity or row["candidate_key"],
            model_probability=None if decision is None
            else _number(decision["model_probability"]),
            market_mid=_decision_mid(decision),
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
            recorded_pnl=recorded_pnl,
            decision_time=row["evaluated_at"],
            settled_time=settled_at,
            close_price=close_price,
            # FX-26 (N-1): cluster by the stored match key when present;
            # NULL keeps the frozen market-key fallback bit-for-bit.
            match_key=row["event_key"],
        ))
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="papersim",
        description=(
            "Paper-simulation core library CLI (paper-only / report-only / "
            "readonly; no order, account or credential surface)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the papersim CLI; returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
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
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    raise AssertionError("unreachable")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
