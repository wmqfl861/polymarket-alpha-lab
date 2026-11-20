"""WP-P1 W5 tests: papersim CLI (selfcheck / schema / validate-dsn) plus
the final CLI wiring (evaluate / replay, M1 final node).

The selfcheck invariant suite must run green end-to-end both in-process
(``cli.main``) and as a real subprocess (``python -m
polymarket_alpha_lab.papersim.cli``); the schema subcommand must emit the
ledger migration text byte-identically; validate-dsn must accept local
DSNs, reject hosted/file fallbacks, never print the DSN value, never
read the password file and obtain env-source metadata only through the
sanctioned ``supabase_papersim_config`` boundary (F9 static gate). The
evaluate/replay subcommands are driven offline against in-memory stubs
(read-only SELECT surface only), through the explicit JSON candidate
file, and through the argument/error boundary (missing source group,
unauthorized DSN rejected by the single audited validator before any
connection). Every test here is read-only: no network, no database
connection, no writes outside a temp dir. The opt-in native lane at the
bottom proves both subcommands against a real disposable cluster.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_alpha_lab import papersim
from polymarket_alpha_lab.papersim import cli, ledger

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
APP_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_APP_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN or not APP_DSN,
                            reason='explicit native papersim CLI proof is opt-in')

SELFCHECK_CHECK_NAMES = (
    "module-imports",
    "crypto-r19:ETH-2000",
    "crypto-r19:ETH-2250",
    "crypto-r19:BTC-65k",
    "soccer-r23:BRE-FOR_O2.5",
    "soccer-r23:HUL-IPS_O2.5",
    "soccer-r23:CHE-MUN_O3.5",
    "soccer-r23:BOU-LEE_O2.5",
    "no-bridge-path:HOF-LYO",
    "gate-arithmetic:#48",
)

LOCAL_DSNS = (
    "postgresql://paper_sim@127.0.0.1:55432/paper_sim",
    "postgresql://paper_sim@localhost:55432/paper_sim",
    "host=127.0.0.1 port=55432 dbname=paper_sim user=paper_sim",
)
HOSTED_DSN = "postgresql://paper_sim@db.internal.example.com:5432/paper_sim"


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------


def test_selfcheck_passes_in_process(capsys) -> None:
    code = cli.main(["selfcheck"])
    out = capsys.readouterr().out
    assert code == 0
    for name in SELFCHECK_CHECK_NAMES:
        assert f"[PASS] {name} " in out
    assert "[FAIL]" not in out
    assert "selfcheck: PASS (10/10 checks)" in out


def test_selfcheck_passes_as_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli", "selfcheck"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "selfcheck: PASS (10/10 checks)" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_selfcheck_reports_failure_when_a_model_drifts(monkeypatch, capsys) -> None:
    # A drifted soccer model must fail exactly the four soccer anchors.
    def drifted(*args, **kwargs):
        return 0.5

    monkeypatch.setattr(
        "polymarket_alpha_lab.papersim.soccer_model.over_probability", drifted
    )
    code = cli.main(["selfcheck"])
    out = capsys.readouterr().out
    assert code == 1
    assert "selfcheck: FAIL (6/10 checks)" in out
    for name in SELFCHECK_CHECK_NAMES:
        expected = "FAIL" if name.startswith("soccer-r23:") else "PASS"
        assert f"[{expected}] {name} " in out


def test_selfcheck_fails_cleanly_when_fixtures_missing(monkeypatch, capsys) -> None:
    bogus = Path("/nonexistent-papersim-fixtures")
    monkeypatch.setattr(cli, "_fixtures_dir", lambda: bogus)
    code = cli.main(["selfcheck"])
    out = capsys.readouterr().out
    assert code == 1
    assert "selfcheck: FAIL (1/10 checks)" in out
    # Only the import check survives; every anchor names the missing file.
    assert out.count("[FAIL]") == 9
    assert "[PASS] module-imports " in out
    assert "frozen fixture missing" in out


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_schema_emits_ledger_ddl_verbatim(capsys) -> None:
    code = cli.main(["schema"])
    assert code == 0
    assert capsys.readouterr().out == ledger.ddl()


def test_schema_subprocess_matches_migration_file() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli", "schema"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    migration = (
        Path(ledger.__file__).resolve().parents[3]
        / "supabase"
        / "migrations"
        / ledger.PAPERSIM_LEDGER_MIGRATION_NAME
    )
    assert result.stdout == migration.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# validate-dsn
# ---------------------------------------------------------------------------


def test_validate_dsn_default_passes_without_env(monkeypatch, capsys) -> None:
    monkeypatch.delenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, raising=False)
    code = cli.main(["validate-dsn"])
    out = capsys.readouterr().out
    assert code == 0
    assert "validate-dsn: PASS" in out
    assert "documented default" in out
    assert "validate_local_postgres_dsn" in out
    # Redaction discipline: the DSN value is never printed.
    assert ledger.DEFAULT_PAPERSIM_PG_DSN not in out


@pytest.mark.parametrize("dsn", LOCAL_DSNS)
def test_validate_dsn_accepts_local_overrides(monkeypatch, capsys, dsn) -> None:
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, dsn)
    code = cli.main(["validate-dsn"])
    out = capsys.readouterr().out
    assert code == 0
    assert "validate-dsn: PASS" in out
    assert f"environment {ledger.PAPERSIM_PG_DSN_ENV_VAR}" in out
    assert dsn not in out  # never echoed


def test_validate_dsn_rejects_hosted_dsn(monkeypatch, capsys) -> None:
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, HOSTED_DSN)
    code = cli.main(["validate-dsn"])
    out = capsys.readouterr().out
    assert code == 1
    assert "validate-dsn: FAIL" in out
    assert ledger.PAPERSIM_PG_DSN_ENV_VAR in out
    # The rejected value is not leaked into the report.
    assert HOSTED_DSN not in out
    assert "db.internal.example.com" not in out


def test_validate_dsn_never_reads_password_file(monkeypatch, capsys, tmp_path) -> None:
    bogus = tmp_path / "no-such-password-file"
    monkeypatch.setenv(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR, str(bogus))
    monkeypatch.delenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, raising=False)
    code = cli.main(["validate-dsn"])
    out = capsys.readouterr().out
    assert code == 0
    assert not bogus.exists()
    assert ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR in out
    assert "never read" in out


def test_validate_dsn_source_metadata_comes_from_config_boundary(
    monkeypatch, capsys
) -> None:
    # F9: the env-vs-default source label must come from the sanctioned
    # supabase_papersim_config boundary (ledger.from_papersim_db_env),
    # which normalizes whitespace-only values to the documented default
    # instead of letting the CLI interpret raw env bytes itself.
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, "   ")
    code = cli.main(["validate-dsn"])
    out = capsys.readouterr().out
    assert code == 0
    assert "validate-dsn: PASS" in out
    assert "documented default" in out


def test_cli_source_has_no_direct_dsn_environment_read() -> None:
    """Static gate for the F9 review finding.

    README boundary: DSN environment reads live only inside the
    sanctioned ``supabase_*config.py`` modules. ``cli.py`` must obtain
    env-source metadata through ``ledger.from_papersim_db_env`` (the
    ``supabase_papersim_config`` boundary), so the CLI source may not
    touch ``environ``/``getenv`` at all, and the DSN variable name may
    appear only as the ``..._ENV_VAR`` name constants used for labels —
    never as a bare read key.
    """
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert re.search(r"\benviron\b", source) is None, (
        "cli.py must not read the process environment; DSN env reads "
        "belong to supabase_papersim_config only"
    )
    assert re.search(r"\bgetenv\b", source) is None, (
        "cli.py must not read the process environment; DSN env reads "
        "belong to supabase_papersim_config only"
    )
    bare = re.search(r"PAPERSIM_PG_DSN(?!_ENV_VAR)", source)
    if bare is not None:
        context = source[max(0, bare.start() - 60) : bare.end() + 60]
        pytest.fail(
            "cli.py references PAPERSIM_PG_DSN outside the _ENV_VAR name "
            f"constant; direct reads belong to supabase_papersim_config only: {context!r}"
        )


# ---------------------------------------------------------------------------
# argument surface
# ---------------------------------------------------------------------------


def test_unknown_command_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus-command"])
    assert excinfo.value.code == 2


def test_missing_command_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_cli_main_is_the_package_export() -> None:
    assert papersim.cli_main is cli.main
    assert callable(papersim.cli_main)


# ---------------------------------------------------------------------------
# evaluate / replay (final CLI wiring) -- offline stubs and boundaries
# ---------------------------------------------------------------------------

STUB_NOW = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=datetime.timezone.utc)
LOCAL_DSN = "host=127.0.0.1 port=55433 dbname=paper_sim user=paper_sim"


class _StubColumn:
    def __init__(self, name: str):
        self.name = name


class _StubCursor:
    def __init__(self, conn: "_StubStoreConn"):
        self._conn = conn
        self._rows: list = []
        self._columns: list[str] = []

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self._conn.statements.append(sql)
        self._rows, self._columns = self._conn._execute(sql)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    @property
    def description(self):
        return [_StubColumn(name) for name in self._columns]


class _StubTx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubStoreConn:
    """In-memory stand-in speaking only the evaluate SELECT surface.

    Candidate rows carry the settlement-join columns of the fixed
    candidates query, including entry attribution and the settlement
    fields, so one stub serves the whole evaluate surface.

    FX-29 (B3 changed-test proof, fix-list 03 section 0 rule 2): the
    loader now opens ONE repeatable-read read-only snapshot before its
    three SELECTs. Old proof: exactly the three SELECT statements
    reached the store (per-statement default isolation). New proof: the
    snapshot-level statement -- accepted at its exact text only -- may
    precede them; anything else still fails loudly, so the stub still
    proves no write SQL of any kind reaches the store.
    """

    SNAPSHOT_LEVEL = ("SET TRANSACTION ISOLATION LEVEL "
                      "REPEATABLE READ READ ONLY")

    def __init__(self, candidates, evidence=(), outcomes=()):
        self._candidates = candidates
        self._evidence = evidence
        self._outcomes = outcomes
        self.statements: list[str] = []

    def _execute(self, sql):
        if sql == self.SNAPSHOT_LEVEL:
            return [], []
        if sql.startswith("SELECT c.candidate_id"):
            # FX-26: the candidates statement now selects c.event_key right
            # after market_kind; stub rows without the key read as NULL
            # (the legacy-row shape), exactly like the real column.
            columns = ["candidate_id", "candidate_key", "conclusion", "side",
                       "market_type", "market_kind", "event_key", "model_name",
                       "model_version", "evaluated_at", "condition_id", "market_id",
                       "market_slug", "gate_version", "stake", "price", "fee_category",
                       "entry_id", "entered_at", "has_order_book", "classification", "win_loss",
                       "exit_price", "proceeds", "pnl_gross", "fee_total",
                       "pnl_net", "confirmed_at"]
            return [tuple(row.get(name) for name in columns) for row in self._candidates], columns
        if sql.startswith("SELECT candidate_id, evidence_role"):
            columns = ["candidate_id", "evidence_role", "model_probability", "mid",
                       "bid", "ask", "last"]
            return [tuple(row[name] for name in columns) for row in self._evidence], columns
        if sql.startswith("SELECT outcome_id, condition_id"):
            columns = ["outcome_id", "condition_id", "market_id", "market_slug",
                       "outcome", "observed_at", "confirmed_at", "source_kind"]
            return [tuple(row[name] for name in columns) for row in self._outcomes], columns
        raise AssertionError(f"unexpected SQL reached the stub: {sql}")

    def cursor(self):
        return _StubCursor(self)

    def transaction(self):
        return _StubTx()

    def close(self):
        pass


def _stub_store() -> _StubStoreConn:
    candidates = [
        {"candidate_id": 1, "candidate_key": "k-acc", "conclusion": "ACCEPTED",
         "side": "YES", "market_type": "terminal", "market_kind": "terminal",
         "model_name": "gbm", "model_version": "r19", "evaluated_at": STUB_NOW,
         "condition_id": None, "market_id": "m-1", "market_slug": None,
         "gate_version": "gates-2026-11-07", "stake": Decimal("250"),
         "price": Decimal("0.56"), "fee_category": "crypto",
         "entry_id": 11, "entered_at": STUB_NOW,
         "has_order_book": True},
        {"candidate_id": 2, "candidate_key": "k-rej", "conclusion": "REJECTED",
         "side": "NO", "market_type": None, "market_kind": "other",
         "model_name": "soccer", "model_version": "r23", "evaluated_at": STUB_NOW,
         "condition_id": None, "market_id": None, "market_slug": "m-2",
         "gate_version": "gates-2026-11-07", "stake": None, "price": None,
         "fee_category": None},
    ]
    evidence = [
        {"candidate_id": 1, "evidence_role": "DECISION",
         "model_probability": Decimal("0.62"), "mid": Decimal("0.5"),
         "bid": None, "ask": None, "last": None},
        {"candidate_id": 1, "evidence_role": "CLOSE", "model_probability": None,
         "mid": None, "bid": None, "ask": None, "last": Decimal("0.9")},
        {"candidate_id": 2, "evidence_role": "DECISION",
         "model_probability": Decimal("0.4"), "mid": None,
         "bid": Decimal("0.44"), "ask": Decimal("0.46"), "last": None},
    ]
    outcomes = [
        {"outcome_id": 1, "condition_id": None, "market_id": "m-1",
         "market_slug": None, "outcome": "YES",
         "confirmed_at": None, "source_kind": "gamma_close",
         "observed_at": STUB_NOW + datetime.timedelta(hours=2)},
        {"outcome_id": 2, "condition_id": None, "market_id": None,
         "market_slug": "m-2", "outcome": "NO",
         "confirmed_at": None, "source_kind": "gamma_close",
         "observed_at": STUB_NOW + datetime.timedelta(hours=2)},
    ]
    return _StubStoreConn(candidates, evidence, outcomes)


def test_evaluate_and_replay_help_document_the_read_only_contract(capsys) -> None:
    for command in ("evaluate", "replay"):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([command, "--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "ead-only" in out
        assert "--managed" in out and "--dsn" in out


def test_evaluate_reads_the_store_with_selects_only(monkeypatch, capsys) -> None:
    conn = _stub_store()
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: conn)
    # Unsettled rows carry no recorded settlement PnL, so they evaluate
    # under the recomputed basis (recorded basis fails loudly on them).
    code = cli.main(["evaluate", "--managed", "--pnl-basis", "recomputed"])
    out = capsys.readouterr().out
    assert code == 0, out
    # FX-29 (B3 changed-test proof, fix-list 03 section 0 rule 2): one
    # load is now [snapshot level, three SELECTs] -- the snapshot-level
    # statement first, then only SELECTs. Old proof: the stream was
    # exactly SELECTs (per-statement default isolation). New proof: the
    # stream is exactly the four statements of ONE consistent
    # repeatable-read read-only snapshot, in order, and still contains
    # no write SQL of any kind.
    assert conn.statements[0] == _StubStoreConn.SNAPSHOT_LEVEL
    assert all(sql.startswith("SELECT") for sql in conn.statements[1:])
    assert len(conn.statements) == 4
    report = json.loads(out)
    from polymarket_alpha_lab.papersim import evaluation
    assert report["algorithm_version"] == evaluation.ALGORITHM_VERSION
    assert report["promotion_rules_version"] == evaluation.PROMOTION_RULES_VERSION
    assert report["paper_only"] is True and report["readonly"] is True
    assert len(report["queues"]) >= 1
    # Deterministic: the same evidence evaluates to identical output bytes.
    cli.main(["evaluate", "--managed", "--pnl-basis", "recomputed"])
    assert capsys.readouterr().out == out


# ---------------------------------------------------------------------------
# evaluate / replay -- settlement-ledger wiring (M1 final review P2-01;
# receipt in repo: docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log)
# ---------------------------------------------------------------------------


def _decision_evidence(candidate_id: int, probability, mid):
    return {"candidate_id": candidate_id, "evidence_role": "DECISION",
            "model_probability": Decimal(str(probability)),
            "mid": Decimal(str(mid)), "bid": None, "ask": None, "last": None}


def _market_outcome(market_id: str, outcome: str, hours: float):
    return {"outcome_id": abs(hash(market_id)) % 1000, "condition_id": None,
            "market_id": market_id, "market_slug": None, "outcome": outcome,
            "observed_at": STUB_NOW + datetime.timedelta(hours=hours),
            "confirmed_at": None, "source_kind": "gamma_close"}


def _entered_candidate(candidate_id: int, market_id: str, *, side: str,
                       stake: str, price: str, has_order_book: bool,
                       settlement: dict | None = None) -> dict:
    row = {"candidate_id": candidate_id, "candidate_key": f"k-{market_id}",
           "conclusion": "ACCEPTED", "side": side, "market_type": "terminal",
           "market_kind": "terminal", "model_name": "gbm",
           "model_version": "r19", "evaluated_at": STUB_NOW,
           "condition_id": None, "market_id": market_id, "market_slug": None,
           "gate_version": "gates-2026-11-07", "stake": Decimal(stake),
           "price": Decimal(price), "fee_category": "sports",
           "entry_id": candidate_id + 100, "entered_at": STUB_NOW,
           "has_order_book": has_order_book}
    if settlement is not None:
        row.update(settlement)
    return row


def _exit_settlement(**overrides) -> dict:
    """The final-review repro settlement: 250 @ 0.50 exited at 0.30.

    Gross -100.00 (proceeds 150 on 500 shares); sports-rate two-sided fees
    6.25 + 5.25 = 11.50; net -111.50.
    """
    row = {"classification": "EXIT", "win_loss": None,
           "exit_price": Decimal("0.30"), "proceeds": Decimal("150.00"),
           "pnl_gross": Decimal("-100.00"), "fee_total": Decimal("11.50"),
           "pnl_net": Decimal("-111.50"),
           "confirmed_at": STUB_NOW + datetime.timedelta(hours=3)}
    row.update(overrides)
    return row


def test_evaluate_wires_settlement_ledger_for_exit_trades(
    monkeypatch, capsys
) -> None:
    """M1 final review P2-01: an early-exited trade must keep the ledger's
    realization (gross -100 / net -111.50 / exits 1) instead of being
    recomputed as held-to-natural-settlement (+250 / +243.75 / 0), and the
    candidates query must actually read ``papersim.settlements``."""
    from polymarket_alpha_lab.papersim import fees

    conn = _StubStoreConn(
        candidates=[_entered_candidate(
            11, "m-exit", side="YES", stake="250", price="0.50",
            has_order_book=True, settlement=_exit_settlement())],
        evidence=[_decision_evidence(11, "0.60", "0.50")],
        outcomes=[_market_outcome("m-exit", "YES", 6)],
    )
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: conn)
    # Default recorded basis: the settlement ledger supplies the PnL.
    code = cli.main(["evaluate", "--managed"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert any("papersim.settlements" in sql for sql in conn.statements), (
        "the evaluate store read must join papersim.settlements"
    )
    report = json.loads(out)
    queue = report["queues"][0]
    assert queue["counts"]["entered"] == 1
    assert queue["counts"]["exited"] == 1
    assert queue["counts"]["settled"] == 1
    assert queue["roi"]["gross_pnl"] == pytest.approx(-100.0)
    assert queue["roi"]["net_pnl"] == pytest.approx(-111.50)
    # Fee-implementation consistency: entry 250*0.05*0.5 = 6.25, exit
    # 500 shares * 0.05 * 0.3 * 0.7 = 5.25 (the same single schedule the
    # settlement worker charged; fee_total 11.50 == pnl_gross - pnl_net).
    assert queue["roi"]["entry_fees"] == pytest.approx(
        fees.entry_fee(250.0, 0.50, 0.05))
    assert queue["roi"]["exit_fees"] == pytest.approx(
        fees.exit_fee(250.0 / 0.50, 0.30, 0.05))
    assert queue["roi"]["entry_fees"] + queue["roi"]["exit_fees"] == (
        pytest.approx(11.50)
    )
    # The market's later natural YES stays an independent whole-market
    # scoring input; it must NOT turn the trade into a natural settlement.
    assert queue["scoring"]["markets_scored"] == 1
    assert queue["scoring"]["missing_outcome"] == 0
    # EXITED rows keep the recorded realization under the recomputed
    # basis too (the exit price is not recomputed from the natural result).
    code = cli.main(["evaluate", "--managed", "--pnl-basis", "recomputed"])
    recomputed = json.loads(capsys.readouterr().out)
    rqueue = recomputed["queues"][0]
    assert rqueue["roi"]["gross_pnl"] == pytest.approx(-100.0)
    assert rqueue["roi"]["net_pnl"] == pytest.approx(-111.50)
    assert rqueue["counts"]["exited"] == 1


def test_evaluate_recorded_basis_reads_settlements_and_fails_without_them(
    monkeypatch, capsys
) -> None:
    """recorded basis: settlements supply recorded PnL when present; an
    entered row WITHOUT a settlement record fails loudly instead of being
    silently recomputed or zero-filled."""
    settled = _StubStoreConn(
        candidates=[_entered_candidate(
            12, "m-win", side="YES", stake="250", price="0.50",
            has_order_book=True,
            settlement={"classification": "NATURAL", "win_loss": "win",
                        "exit_price": None, "proceeds": Decimal("500.00"),
                        "pnl_gross": Decimal("250.00"),
                        "fee_total": Decimal("6.25"),
                        "pnl_net": Decimal("243.75"),
                        "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)})],
        evidence=[_decision_evidence(12, "0.60", "0.50")],
        outcomes=[_market_outcome("m-win", "YES", 7)],
    )
    monkeypatch.setattr(
        cli, "_open_readonly_connection", lambda args, label: settled)
    code = cli.main(["evaluate", "--managed"])  # default recorded basis
    out = capsys.readouterr().out
    assert code == 0, out
    queue = json.loads(out)["queues"][0]
    assert queue["roi"]["gross_pnl"] == pytest.approx(250.0)
    assert queue["roi"]["net_pnl"] == pytest.approx(243.75)
    assert queue["counts"]["exited"] == 0

    unsettled = _StubStoreConn(
        candidates=[_entered_candidate(
            14, "m-open", side="YES", stake="250", price="0.56",
            has_order_book=True)],
        evidence=[_decision_evidence(14, "0.62", "0.56")],
        outcomes=[_market_outcome("m-open", "YES", 6)],
    )
    monkeypatch.setattr(
        cli, "_open_readonly_connection", lambda args, label: unsettled)
    code = cli.main(["evaluate", "--managed"])
    assert code == 1
    err = capsys.readouterr().err
    assert "evaluate: FAIL" in err
    assert "recorded mode" in err and "pnl" in err


def test_evaluate_win_loss_labels_and_natural_outcome_stay_separate(
    monkeypatch, capsys
) -> None:
    """A NATURAL settlement carries its post-audit win/loss label into the
    trade outcome while the market-level natural result keeps feeding
    whole-market scoring (it is not duplicated into trade PnL)."""
    conn = _StubStoreConn(
        candidates=[
            _entered_candidate(
                15, "m-label", side="YES", stake="250", price="0.50",
                has_order_book=True,
                settlement={"classification": "NATURAL", "win_loss": "win",
                            "exit_price": None, "proceeds": Decimal("500.00"),
                            "pnl_gross": Decimal("250.00"),
                            "fee_total": Decimal("6.25"),
                            "pnl_net": Decimal("243.75"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}),
            _entered_candidate(
                16, "m-label-loss", side="YES", stake="250", price="0.50",
                has_order_book=True,
                settlement={"classification": "NATURAL", "win_loss": "loss",
                            "exit_price": None, "proceeds": Decimal("0.00"),
                            "pnl_gross": Decimal("-250.00"),
                            "fee_total": Decimal("6.25"),
                            "pnl_net": Decimal("-256.25"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}),
        ],
        evidence=[_decision_evidence(15, "0.60", "0.50"),
                  _decision_evidence(16, "0.60", "0.50")],
        outcomes=[_market_outcome("m-label", "YES", 7),
                  _market_outcome("m-label-loss", "NO", 7)],
    )
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: conn)
    code = cli.main(["evaluate", "--managed"])
    out = capsys.readouterr().out
    assert code == 0, out
    queue = json.loads(out)["queues"][0]
    assert queue["counts"]["settled"] == 2
    assert queue["counts"]["exited"] == 0
    # recorded basis: one win (+250) and one loss (-250) gross, net of the
    # two 6.25 entry fees.
    assert queue["roi"]["gross_pnl"] == pytest.approx(0.0)
    assert queue["roi"]["net_pnl"] == pytest.approx(-12.50)
    # Both markets keep their natural results for model-vs-market scoring.
    assert queue["scoring"]["markets_scored"] == 2
    assert queue["scoring"]["missing_outcome"] == 0


def test_evaluate_voided_settlement_excludes_the_trade_from_roi(
    monkeypatch, capsys
) -> None:
    """A VOIDED_REFUND settlement voids the trade even when the market
    later recorded a natural result: stake refunded, excluded from ROI."""
    conn = _StubStoreConn(
        candidates=[
            _entered_candidate(
                17, "m-void", side="YES", stake="250", price="0.50",
                has_order_book=True,
                settlement={"classification": "VOIDED_REFUND", "win_loss": None,
                            "exit_price": None, "proceeds": Decimal("250.00"),
                            "pnl_gross": Decimal("0.00"),
                            "fee_total": Decimal("0.00"),
                            "pnl_net": Decimal("0.00"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}),
            _entered_candidate(
                18, "m-win2", side="YES", stake="250", price="0.50",
                has_order_book=True,
                settlement={"classification": "NATURAL", "win_loss": "win",
                            "exit_price": None, "proceeds": Decimal("500.00"),
                            "pnl_gross": Decimal("250.00"),
                            "fee_total": Decimal("6.25"),
                            "pnl_net": Decimal("243.75"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}),
        ],
        evidence=[_decision_evidence(17, "0.60", "0.50"),
                  _decision_evidence(18, "0.60", "0.50")],
        outcomes=[_market_outcome("m-void", "YES", 7),
                  _market_outcome("m-win2", "YES", 7)],
    )
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: conn)
    code = cli.main(["evaluate", "--managed"])
    out = capsys.readouterr().out
    assert code == 0, out
    queue = json.loads(out)["queues"][0]
    assert queue["counts"]["voided"] == 1
    assert queue["counts"]["settled"] == 1
    assert queue["roi"]["counted"] == 1  # only the win row carries stake
    assert queue["roi"]["stake"] == pytest.approx(250.0)
    assert queue["roi"]["gross_pnl"] == pytest.approx(250.0)


def test_evaluate_fill_provenance_requires_a_recorded_order_book(
    monkeypatch, capsys
) -> None:
    """book_walk provenance needs the recorded walked book; a legacy
    import (no order book on the candidate) must stay recorded_price and
    surface as a criterion-B provenance gap, never as a silent book walk."""
    def store(legacy: bool) -> _StubStoreConn:
        rows = [
            _entered_candidate(
                21, "m-v2", side="YES", stake="250", price="0.50",
                has_order_book=True,
                settlement={"classification": "NATURAL", "win_loss": "win",
                            "exit_price": None, "proceeds": Decimal("500.00"),
                            "pnl_gross": Decimal("250.00"),
                            "fee_total": Decimal("6.25"),
                            "pnl_net": Decimal("243.75"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}),
        ]
        if legacy:
            rows.append(_entered_candidate(
                22, "m-leg", side="NO", stake="400", price="0.69",
                has_order_book=False,
                settlement={"classification": "NATURAL", "win_loss": "loss",
                            "exit_price": None, "proceeds": Decimal("0.00"),
                            "pnl_gross": Decimal("-400.00"),
                            "fee_total": Decimal("6.20"),
                            "pnl_net": Decimal("-406.20"),
                            "confirmed_at": STUB_NOW + datetime.timedelta(hours=6)}))
        return _StubStoreConn(
            candidates=rows,
            evidence=[_decision_evidence(row["candidate_id"], "0.60", "0.50")
                      for row in rows],
            outcomes=[_market_outcome("m-v2", "YES", 7),
                      _market_outcome("m-leg", "YES", 7)])

    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: store(legacy=False))
    code = cli.main(["evaluate", "--managed"])
    out = capsys.readouterr().out
    assert code == 0, out
    verdicts = {
        criterion["key"]: criterion
        for criterion in json.loads(out)["queues"][0]["promotion"]
    }
    assert not any("book-walk" in reason
                   for reason in verdicts["B"]["reasons"])

    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: store(legacy=True))
    code = cli.main(["evaluate", "--managed"])
    out = capsys.readouterr().out
    assert code == 0, out
    verdicts = {
        criterion["key"]: criterion
        for criterion in json.loads(out)["queues"][0]["promotion"]
    }
    assert verdicts["B"]["verdict"] == "NOT_ASSESSABLE"
    assert any("book-walk" in reason and "1 of 2" in reason
               for reason in verdicts["B"]["reasons"])


def test_evaluate_wires_construction_failed_count_from_the_store(
    monkeypatch, capsys
) -> None:
    """FX-30/OD-K (audit N-5, cli.py read path): one queue, three
    conclusions — 1 ACCEPTED, 1 REJECTED, 1 CONSTRUCTION_FAILED. The
    construction failure is reported in its own counts bucket and
    criterion A fails on "candidates 2 < 200" (accepted plus
    gate-rejected), never on 3."""
    candidates = [
        _entered_candidate(31, "m-cf-acc", side="YES", stake="250",
                           price="0.56", has_order_book=True),
        {"candidate_id": 32, "candidate_key": "k-cf-rej",
         "conclusion": "REJECTED", "side": "NO", "market_type": "terminal",
         "market_kind": "terminal", "model_name": "gbm",
         "model_version": "r19", "evaluated_at": STUB_NOW,
         "condition_id": None, "market_id": "m-cf-rej", "market_slug": None,
         "gate_version": "gates-2026-11-07", "stake": None, "price": None,
         "fee_category": None},
        {"candidate_id": 33, "candidate_key": "k-cf-failed",
         "conclusion": "CONSTRUCTION_FAILED", "side": None,
         "market_type": "terminal", "market_kind": "terminal",
         "model_name": "gbm", "model_version": "r19",
         "evaluated_at": STUB_NOW, "condition_id": None, "market_id": None,
         "market_slug": None, "gate_version": "gates-2026-11-07",
         "stake": None, "price": None, "fee_category": None},
    ]
    evidence = [
        _decision_evidence(31, "0.60", "0.50"),
        _decision_evidence(32, "0.40", "0.45"),
    ]
    outcomes = [
        _market_outcome("m-cf-acc", "YES", 6),
        _market_outcome("m-cf-rej", "NO", 6),
    ]
    conn = _StubStoreConn(candidates=candidates, evidence=evidence,
                          outcomes=outcomes)
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: conn)
    # Unsettled accepted row: recomputed basis (recorded fails loudly).
    code = cli.main(["evaluate", "--managed", "--pnl-basis", "recomputed"])
    out = capsys.readouterr().out
    assert code == 0, out
    queue = json.loads(out)["queues"][0]
    counts = queue["counts"]
    assert counts["candidates"] == 3
    assert counts["accepted"] == 1
    assert counts["rejected"] == 1
    assert counts["construction_failed"] == 1
    verdicts = {c["key"]: c for c in queue["promotion"]}
    assert any("candidates 2 < 200" in reason
               for reason in verdicts["A"]["reasons"])
    assert not any("candidates 3 < 200" in reason
                   for reason in verdicts["A"]["reasons"])


def test_evaluate_json_input_runs_without_any_connection(monkeypatch, capsys, tmp_path) -> None:
    def _forbidden(args, label):
        raise AssertionError("no connection may be opened for --json-input")

    monkeypatch.setattr(cli, "_open_readonly_connection", _forbidden)
    document = {
        "candidates": [
            {"candidate_id": "j1", "model_key": "gbm:r19", "market_type": "terminal",
             "gate_version": "gates-2026-11-07", "market_key": "m-1",
             "model_probability": 0.62, "market_mid": 0.5, "accepted": True,
             "stake": 250.0, "fill_price": 0.56, "side": "YES",
             "fee_category": "crypto", "fill_provenance": "book_walk",
             "outcome": "YES", "recorded_pnl": 194.4,
             "decision_time": "2026-11-15T12:00:00+00:00",
             "settled_time": "2026-11-15T14:00:00+00:00", "close_price": 0.9},
            {"candidate_id": "j2", "model_key": "soccer:r23", "market_type": "other",
             "gate_version": "gates-2026-11-07", "market_key": "m-2",
             "model_probability": 0.4, "market_mid": 0.45, "accepted": False,
             "side": "NO", "outcome": None, "decision_time": "2026-11-15T12:00:00+00:00"},
        ]
    }
    path = tmp_path / "queue.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    code = cli.main(["evaluate", "--json-input", str(path)])
    out = capsys.readouterr().out
    assert code == 0, out
    report = json.loads(out)
    assert sum(queue["counts"]["candidates"] for queue in report["queues"]) == 2
    cli.main(["evaluate", "--json-input", str(path)])
    assert capsys.readouterr().out == out  # deterministic


def test_evaluate_json_input_accepts_construction_failed_flag(
    monkeypatch, capsys, tmp_path
) -> None:
    """The JSON face takes the new optional ``construction_failed`` field;
    rows without it keep defaulting to False (backward compatible)."""
    def _forbidden(args, label):
        raise AssertionError("no connection may be opened for --json-input")

    monkeypatch.setattr(cli, "_open_readonly_connection", _forbidden)
    document = {"candidates": [
        {"candidate_id": "j1", "model_key": "gbm:r19",
         "market_type": "terminal", "gate_version": "gates-2026-11-07",
         "market_key": "m-1", "model_probability": 0.62, "market_mid": 0.5,
         "accepted": True, "stake": 250.0, "fill_price": 0.56, "side": "YES",
         "fee_category": "crypto", "fill_provenance": "book_walk",
         "outcome": "YES", "recorded_pnl": 194.4,
         "decision_time": "2026-11-15T12:00:00+00:00",
         "settled_time": "2026-11-15T14:00:00+00:00", "close_price": 0.9},
        {"candidate_id": "j9", "model_key": "gbm:r19",
         "market_type": "terminal", "gate_version": "gates-2026-11-07",
         "market_key": "m-9", "accepted": False,
         "construction_failed": True, "side": None,
         "decision_time": "2026-11-15T12:00:00+00:00"},
    ]}
    path = tmp_path / "queue-cf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    code = cli.main(["evaluate", "--json-input", str(path)])
    out = capsys.readouterr().out
    assert code == 0, out
    queue = json.loads(out)["queues"][0]
    assert queue["counts"]["candidates"] == 2
    assert queue["counts"]["construction_failed"] == 1


def test_evaluate_json_input_rejects_unknown_fields_and_garbage(capsys, tmp_path) -> None:
    bad_fields = tmp_path / "bad-fields.json"
    bad_fields.write_text(json.dumps([{"candidate_id": "x", "rogue": 1}]), encoding="utf-8")
    code = cli.main(["evaluate", "--json-input", str(bad_fields)])
    assert code == 1
    assert "papersim_cli_json_field_unknown" in capsys.readouterr().err
    garbage = tmp_path / "garbage.json"
    garbage.write_text(json.dumps({"no_candidates": []}), encoding="utf-8")
    code = cli.main(["evaluate", "--json-input", str(garbage)])
    assert code == 1
    assert "papersim_cli_json_input_invalid" in capsys.readouterr().err
    missing = tmp_path / "absent.json"
    code = cli.main(["evaluate", "--json-input", str(missing)])
    assert code == 1
    assert "evaluate: FAIL" in capsys.readouterr().err


def test_evaluate_requires_exactly_one_source() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["evaluate"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["evaluate", "--managed", "--json-input", "x.json"])
    assert excinfo.value.code == 2


def test_evaluate_rejects_unauthorized_dsn_before_any_connection() -> None:
    # Subprocess isolation: the offline suite keeps the driver out of
    # sys.modules; the validator path still runs for real in the child.
    result = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli",
         "evaluate", "--dsn", HOSTED_DSN],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 1
    assert "evaluate: FAIL" in result.stderr
    assert "must point to local Postgres" in result.stderr
    # The rejected value is not leaked into the report.
    assert "db.internal.example.com" not in result.stderr


def test_replay_requires_candidate_trusted_commit_and_source() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["replay"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["replay", "7", "--dsn", LOCAL_DSN])  # missing --trusted-commit
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["replay", "not-an-int", "--trusted-commit", "a" * 40, "--dsn", LOCAL_DSN])
    assert excinfo.value.code == 2


def test_replay_rejects_unauthorized_dsn_before_any_connection() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli",
         "replay", "7", "--trusted-commit", "a" * 40, "--dsn", HOSTED_DSN],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 1
    assert "replay: FAIL" in result.stderr
    assert "must point to local Postgres" in result.stderr
    assert "db.internal.example.com" not in result.stderr


def test_connect_explicit_rejects_unauthorized_dsn_without_the_driver() -> None:
    # M1 CI fix (offline run 43741792290): importing the connection adapter
    # must not require the driver, and the audited validator must reject an
    # unauthorized DSN before psycopg is ever imported. The child simulates
    # the driverless offline lane by blocking the import via sys.modules.
    child = (
        "import sys\n"
        "from polymarket_alpha_lab.papersim import store_psycopg\n"
        "print('import-loaded-driver', 'psycopg' in sys.modules)\n"
        "sys.modules['psycopg'] = None  # simulate the offline lane\n"
        "try:\n"
        "    store_psycopg.connect_explicit(sys.argv[1])\n"
        "except ValueError as error:\n"
        "    print('rejected', error)\n"
        "else:\n"
        "    print('accepted')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", child, HOSTED_DSN],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    # The module imported cleanly without loading the driver, the validator
    # (not an ImportError) produced the rejection, and the DSN value itself
    # never appears in the child's output.
    assert "import-loaded-driver False" in result.stdout
    assert "rejected " in result.stdout
    assert "must point to local Postgres" in result.stdout
    assert "accepted" not in result.stdout
    assert "db.internal.example.com" not in result.stdout


class _ClosableConn:
    """Adapter giving the in-memory record the connection's close face."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


def test_replay_subcommand_end_to_end_on_the_in_memory_record(monkeypatch, capsys) -> None:
    from tests.test_papersim_replay import COMMIT, FakePapersim, bound_set

    db = _ClosableConn(FakePapersim())
    ids = bound_set(db._inner)
    db._inner.statements.clear()  # only the replay call's statements count
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda args, label: db)
    code = cli.main(["replay", str(ids["k-soccer48-accepted"]),
                     "--trusted-commit", COMMIT, "--dsn", LOCAL_DSN])
    out = capsys.readouterr().out
    assert code == 0, out
    result = json.loads(out)
    assert result["matched"] is True and result["mode"] == "entry_request"
    assert result["code_commit"] == COMMIT
    assert all(sql.startswith("SELECT") for sql in db.statements)

    code = cli.main(["replay", str(ids["k-soccer48-accepted"]),
                     "--trusted-commit", "b" * 40, "--dsn", LOCAL_DSN])
    assert code == 1
    assert "papersim_replay_code_commit_mismatch" in capsys.readouterr().err
    code = cli.main(["replay", "999", "--trusted-commit", COMMIT, "--dsn", LOCAL_DSN])
    assert code == 1
    assert "papersim_replay_candidate_missing" in capsys.readouterr().err


def test_cli_evaluate_replay_add_no_environment_reads() -> None:
    """The F9 static gate covers the new subcommands too: no direct env reads."""
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert re.search(r"\benviron\b", source) is None
    assert re.search(r"\bgetenv\b", source) is None


# ---------------------------------------------------------------------------
# evaluate / replay (final CLI wiring) -- opt-in native lane
# ---------------------------------------------------------------------------

def adapter():
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def connect(dsn, *, autocommit=False):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


@pytest.fixture(scope='module')
def managed_face():
    from polymarket_alpha_lab.papersim import migrations as pm
    owner = connect(OWNER_DSN)
    # Cluster-preparation wiring (M2 plan section 6.3): the applied count
    # follows the CURRENT catalog size; migration-history count assertions
    # live against the frozen M1 catalog fixture.
    catalog = pm.papersim_migration_catalog(ROOT)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] in range(len(catalog) + 1)
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    return OWNER_DSN, APP_DSN


@NATIVE
def test_native_evaluate_and_replay_over_the_managed_store(managed_face, capsys) -> None:
    store_psycopg = adapter()
    import dataclasses

    from polymarket_alpha_lab import replay
    from polymarket_alpha_lab.papersim import gates, snapshots, store
    from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
    from polymarket_alpha_lab.papersim.pipeline import EntryRequest, decide_entry
    from polymarket_alpha_lab.papersim.store import PriceEvidenceObservation

    _, app_dsn = managed_face
    app = connect(app_dsn)
    scan = replay.bind_scan_run(team='crypto_eth',
                                started_at=STUB_NOW - datetime.timedelta(minutes=10),
                                finished_at=STUB_NOW + datetime.timedelta(minutes=5),
                                code_commit='c' * 40)
    request = EntryRequest(
        market_id='m-native-cli', question='Ethereum below $2,000 on date',
        fixture='ETH-2000', market_kind='crypto_terminal', side='yes',
        book=[(0.15, 5000.0)], fee_category='crypto',
        probability=TerminalProbability(asset='ETH', barrier=2000.0,
                                        horizon_hours=1595.12,
                                        convention_a=0.2224, convention_b=0.2531),
        yes_when='below', buy_side='yes', yes_ask=0.15, no_ask=0.80,
        gamma_liquidity=6000.0,
        news=gates.check_news('ETH-2000', 'recheck T-1d', news_delta='unchanged'),
        bankroll=10_000.0, cash=10_000.0, reserved=0.0,
        book_time=STUB_NOW, now=STUB_NOW, max_book_age_seconds=300.0)
    decision = decide_entry(request)
    digest = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
        content=replay.bound_book_bytes(request), kind='clob_book',
        source='clob:m-native-cli', fetched_at=request.book_time))['content_sha256']
    accepted = replay.bind_candidate(
        request, decision, team='crypto_eth', candidate_key='n-cli-accepted',
        side='YES', market_kind='terminal', evaluated_at=STUB_NOW,
        market_ref_kind='market_id', market_id='m-native-cli',
        median_probability=replay.bound_median_probability(request, decision),
        snapshot_refs=[digest], book_snapshot_sha256=digest,
        entry=replay.bind_entry(decision, entered_at=STUB_NOW) if decision.accepted else None)
    close = PriceEvidenceObservation(
        observed_at=STUB_NOW + datetime.timedelta(hours=1), evidence_role='CLOSE',
        market_ref_kind='market_id', market_ref='m-native-cli', side='YES',
        last=Decimal('0.9'), source_kind='gamma_close', source_ref='close-m-native-cli')
    accepted = dataclasses.replace(accepted, price_evidence=accepted.price_evidence + (close,))
    stale = dataclasses.replace(
        request, book_time=STUB_NOW - datetime.timedelta(seconds=900))
    rejected = replay.bind_candidate(
        stale, decide_entry(stale), team='crypto_eth', candidate_key='n-cli-rejected',
        side='NO', market_kind='terminal', evaluated_at=STUB_NOW,
        market_ref_kind='market_id', market_id='m-native-cli',
        snapshot_refs=[digest], book_snapshot_sha256=digest)
    written = store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan, candidates=(accepted, rejected)))
    store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
        market_id='m-native-cli', outcome='YES',
        observed_at=STUB_NOW + datetime.timedelta(hours=2),
        source_kind='gamma_close', source_ref='close-m-native-cli'))
    app.commit()
    app.close()

    code = cli.main(['evaluate', '--dsn', app_dsn, '--pnl-basis', 'recomputed'])
    out = capsys.readouterr().out
    assert code == 0, out
    report = json.loads(out)
    assert report['queues'], 'the recorded scan must feed the evaluation queue'
    assert report['readonly'] is True

    candidate_id = written['candidate_ids']['n-cli-accepted']
    code = cli.main(['replay', str(candidate_id), '--trusted-commit', 'c' * 40,
                     '--dsn', app_dsn])
    out = capsys.readouterr().out
    assert code == 0, out
    assert json.loads(out)['matched'] is True
