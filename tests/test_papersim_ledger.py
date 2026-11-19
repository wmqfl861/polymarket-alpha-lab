"""WP-P1 W4 tests: papersim ledger DDL mirror, DSN validation, settlement
arithmetic and the bankroll identity. Extended 2026-11-12 with the
FX-01 net-PnL semantics (fee_total / pnl_net, fee-aware settlement,
queue_performance) and the tools/papersim_ledger_report.py wiring.

Unit paths open no sockets and no database connections. The single
optional integration check runs only when ``PAPERSIM_PG_DSN`` is
explicitly provided and validates, and otherwise skips with an explicit
label; these tests never touch the live paper_sim cluster or any
``~/pal-artifacts`` path by default. Password material is never read,
printed or committed here.
"""

from dataclasses import fields
from hashlib import sha256
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import fees, ledger
from polymarket_alpha_lab.supabase_local_dsn import validate_local_postgres_dsn
from polymarket_alpha_lab.supabase_papersim_config import SupabasePapersimConfig

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "supabase" / "migrations" / ledger.PAPERSIM_LEDGER_MIGRATION_NAME
MANIFEST_PATH = ROOT / "database" / "migrations.lock.json"
INTEGRATION_LABEL = "papersim-ledger-integration"
SNAPSHOT_PATH = ROOT / "docs" / "paper-sim" / "review-2026-10-30" / "settled_ledger.json"
TOOL_PATH = ROOT / "tools" / "papersim_ledger_report.py"
_SPORTS = fees.taker_fee_rate("sports")


def test_ddl_is_byte_identical_to_checked_in_migration():
    assert MIGRATION_PATH.is_file()
    assert MIGRATION_PATH.read_text(encoding="utf-8") == ledger.ddl()
    assert b"\r" not in MIGRATION_PATH.read_bytes()


def test_ddl_contains_mirrored_tables_views_and_boundary_delta():
    text = ledger.ddl().lower()
    for fragment in (
        "create table public.decisions",
        "create table public.resolutions",
        "create table public.bankroll",
        "create view public.model_performance",
        "create view public.forecast_calibration",
        "check (paper_only is true)",
        "check (report_only is true)",
        "check (readonly is true)",
        "id bigserial primary key",
        "market_id text primary key",
        "team text default 'crypto_eth'::text",
    ):
        assert fragment in text


def test_ddl_exits_separated_from_wl_and_calibration_natural_only():
    text = ledger.ddl().lower()
    # Wins and losses require a natural outcome AND a pnl sign; EXITED
    # feeds only the exits counter.
    assert "and r.pnl > 0::numeric then 1 else 0 end) as wins" in text
    assert "and r.pnl <= 0::numeric then 1 else 0 end) as losses" in text
    assert "'exited'::text then 1 else 0 end) as exits" in text
    # Exits stay inside total PnL: the sum spans every joined row with no
    # outcome filter.
    assert "round(sum(r.pnl), 2) as total_pnl" in text
    # Calibration cohort is natural resolutions only.
    assert (
        "where r.outcome = any (array['yes'::text, 'no'::text, "
        "'yes-basket'::text])" in text
    )


def test_ddl_calibration_hit_predicates_identical_in_both_expressions():
    # F5 regression: forecast_hits and avg_prob_of_realized must use the
    # exact same hit predicate, so a winning NO-basket row contributes
    # model_prediction to avg_prob_of_realized instead of its complement.
    view_sql = ledger.ddl().lower().split(
        "create view public.forecast_calibration", 1
    )[1]
    predicates = [
        " ".join(fragment.split())
        for fragment in re.findall(r"case when (.*?) then ", view_sql, flags=re.DOTALL)
    ]
    assert len(predicates) == 2
    expected_predicate = (
        "(d.side = any (array['yes'::text, 'yes-basket'::text])) "
        "and r.outcome = d.side "
        "or d.side = 'no'::text and r.outcome = 'no'::text "
        "or d.side = 'no-basket'::text and r.outcome = 'no'::text"
    )
    assert predicates[0] == expected_predicate
    assert predicates[1] == expected_predicate


@pytest.mark.parametrize(
    "side,outcome,expected_hit",
    [
        ("YES", "YES", True),
        ("YES", "NO", False),
        ("YES-basket", "YES-basket", True),
        ("YES-basket", "NO", False),
        ("NO", "NO", True),
        ("NO", "YES", False),
        ("NO-basket", "NO", True),
        ("NO-basket", "YES", False),
    ],
)
def test_calibration_hit_matrix_matches_sql_predicate_semantics(
    side, outcome, expected_hit
):
    # Direct evaluation of the predicate the SQL mirrors: YES/YES-basket
    # sides hit when the outcome equals the side; NO/NO-basket sides hit
    # when the outcome is NO (basket legs resolve on the natural NO).
    assert ledger._natural_hit(side, outcome) is expected_hit


def test_calibration_no_basket_win_yields_avg_prob_of_realized_prediction():
    # The F5 book-of-record example: side='NO-basket', outcome='NO',
    # prediction=0.9 must report a hit and contribute 0.9 (not the 0.1
    # complement) to avg_prob_of_realized.
    prediction = 0.9
    hit = ledger._natural_hit("NO-basket", "NO")
    assert hit is True
    prob_of_realized = prediction if hit else 1.0 - prediction
    assert prob_of_realized == pytest.approx(0.9)


def test_migration_registered_in_lock_manifest():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["format"] == "native-postgres-migrations-v1"
    entries = manifest["migrations"]
    entry = next(
        item for item in entries if item["name"] == ledger.PAPERSIM_LEDGER_MIGRATION_NAME
    )
    assert set(entry) == {"name", "sha256", "transaction_wrapper"}
    assert entry["transaction_wrapper"] is False
    assert entry["sha256"] == sha256(MIGRATION_PATH.read_bytes()).hexdigest()
    actual = sorted(path.name for path in (ROOT / "supabase" / "migrations").glob("*.sql"))
    assert [item["name"] for item in entries] == actual


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (None, ledger.OUTCOME_OPEN),
        ("EXITED", ledger.OUTCOME_EXITED),
        ("YES", ledger.OUTCOME_SETTLED),
        ("NO", ledger.OUTCOME_SETTLED),
        ("YES-basket", ledger.OUTCOME_SETTLED),
    ],
)
def test_classify_outcome_buckets(outcome, expected):
    assert ledger.classify_outcome(outcome) == expected


@pytest.mark.parametrize("outcome", ["", "yes", "NO-basket", "MUTUAL", "0", "exited"])
def test_classify_outcome_rejects_unknown_tokens(outcome):
    with pytest.raises(ValueError):
        ledger.classify_outcome(outcome)


def test_settle_natural_win_pays_shares_per_dollar():
    settlement = ledger.settle_natural(stake=250.0, price=0.23, side="YES", outcome="YES")
    assert settlement.classification == ledger.OUTCOME_SETTLED
    assert settlement.proceeds == pytest.approx(250.0 / 0.23)
    assert settlement.pnl == pytest.approx(250.0 / 0.23 - 250.0)
    assert settlement.win_loss == "win"


def test_settle_natural_basket_win_matches_book_of_record():
    settlement = ledger.settle_natural(
        stake=150.0, price=0.985, side="YES-basket", outcome="YES-basket"
    )
    assert settlement.pnl == pytest.approx(2.28, abs=0.01)
    assert settlement.win_loss == "win"


def test_settle_natural_loss_loses_stake():
    settlement = ledger.settle_natural(stake=350.0, price=0.56, side="YES", outcome="NO")
    assert settlement.proceeds == 0.0
    assert settlement.pnl == pytest.approx(-350.0)
    assert settlement.win_loss == "loss"


@pytest.mark.parametrize(
    "side,outcome,expected",
    [
        ("NO", "NO", "win"),
        ("NO", "YES", "loss"),
        ("NO-basket", "NO", "win"),
        ("NO-basket", "YES", "loss"),
        ("YES-basket", "NO", "loss"),
    ],
)
def test_settle_natural_side_outcome_matrix(side, outcome, expected):
    settlement = ledger.settle_natural(stake=100.0, price=0.5, side=side, outcome=outcome)
    assert settlement.win_loss == expected


def test_settle_natural_rejects_exited_and_bad_inputs():
    with pytest.raises(ValueError, match="settle_exit"):
        ledger.settle_natural(stake=400.0, price=0.69, side="NO", outcome="EXITED")
    with pytest.raises(ValueError):
        ledger.settle_natural(stake=400.0, price=0.0, side="NO", outcome="NO")
    with pytest.raises(ValueError):
        ledger.settle_natural(stake=400.0, price=1.0, side="NO", outcome="NO")
    with pytest.raises(ValueError):
        ledger.settle_natural(stake=0.0, price=0.69, side="NO", outcome="NO")
    with pytest.raises(ValueError):
        ledger.settle_natural(stake=400.0, price=0.69, side="MAYBE", outcome="NO")


def test_settle_exit_realizes_pnl_but_never_wl():
    settlement = ledger.settle_exit(stake=400.0, price=0.69, exit_price=0.585)
    assert settlement.classification == ledger.OUTCOME_EXITED
    assert settlement.proceeds == pytest.approx(400.0 / 0.69 * 0.585)
    assert settlement.pnl == pytest.approx(400.0 / 0.69 * 0.585 - 400.0)
    # The live book recorded -60.70 because it realized on 580 whole
    # shares; the exact per-share arithmetic is -60.87 on 579.71 shares.
    assert settlement.pnl == pytest.approx(-60.86956521739131)
    assert settlement.win_loss is None


def test_settle_exit_full_and_zero_bounds():
    zero = ledger.settle_exit(stake=200.0, price=0.5, exit_price=0.0)
    assert zero.proceeds == 0.0
    assert zero.pnl == pytest.approx(-200.0)
    full = ledger.settle_exit(stake=200.0, price=0.5, exit_price=1.0)
    assert full.pnl == pytest.approx(200.0)
    assert full.win_loss is None
    with pytest.raises(ValueError):
        ledger.settle_exit(stake=200.0, price=0.5, exit_price=1.2)


def _realize(stake, price, side, outcome, *, exit_price=0.585):
    if ledger.classify_outcome(outcome) == ledger.OUTCOME_EXITED:
        return ledger.settle_exit(stake, price, exit_price)
    return ledger.settle_natural(stake, price, side, outcome)


def test_portfolio_math_exits_excluded_from_wl_included_in_pnl():
    rows = [
        (250.00, 0.230, "YES", "YES"),
        (429.93, 0.510, "YES", "YES"),
        (350.00, 0.560, "YES", "NO"),
        (400.00, 0.690, "NO", "EXITED"),
        (249.57, 0.846, "NO", "EXITED"),
    ]
    settlements = [_realize(*row) for row in rows]
    assert settlements[1].pnl == pytest.approx(413.07, abs=0.01)
    wins = [item for item in settlements if item.win_loss == "win"]
    losses = [item for item in settlements if item.win_loss == "loss"]
    exits = [item for item in settlements if item.classification == ledger.OUTCOME_EXITED]
    assert (len(wins), len(losses), len(exits)) == (2, 1, 2)
    # Win-rate denominator mirrors the fixed view: natural resolutions only.
    assert len(wins) + len(losses) == 3
    # Exits carry realized PnL inside the total.
    exit_pnl = sum(item.pnl for item in exits)
    assert exit_pnl < 0
    total_pnl = sum(item.pnl for item in settlements)
    assert total_pnl == pytest.approx(
        sum(item.pnl for item in wins + losses) + exit_pnl
    )
    # Calibration cohort: natural resolutions only, via classify_outcome.
    cohort = [
        row for row in rows
        if ledger.classify_outcome(row[3]) == ledger.OUTCOME_SETTLED
    ]
    assert len(cohort) == 3


def test_bankroll_identity_matches_book_of_record_rows():
    assert ledger.bankroll_identity(12374.33, 9488.68, 2885.65) is True
    assert ledger.bankroll_identity(12182.29, 7998.12, 4184.17) is True


def test_bankroll_identity_float_guard_and_mismatch():
    assert ledger.bankroll_identity(0.1 + 0.2, 0.1, 0.2) is True
    assert ledger.bankroll_identity(12374.34, 9488.68, 2885.65) is False
    assert ledger.bankroll_identity(
        12374.35, 9488.68, 2885.65, tolerance=0.05
    ) is True


def test_bankroll_identity_rejects_non_finite():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ledger.bankroll_identity(bad, 1.0, 1.0)


def test_papersim_dsn_default_is_documented_example_and_validates(monkeypatch):
    monkeypatch.delenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, raising=False)
    dsn = ledger.papersim_dsn()
    assert dsn == ledger.DEFAULT_PAPERSIM_PG_DSN
    assert dsn == "postgresql://paper_sim@127.0.0.1:55432/paper_sim"
    # Through the real repo validator.
    validate_local_postgres_dsn(dsn, env_var_name="PAPERSIM_PG_DSN")


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://paper_sim@127.0.0.1:55432/paper_sim",
        "postgresql://paper_sim@localhost:55432/paper_sim",
        "postgresql://paper_sim@[::1]:55432/paper_sim",
        "host=127.0.0.1 port=55432 dbname=paper_sim user=paper_sim",
    ],
)
def test_papersim_dsn_accepts_local_overrides(monkeypatch, value):
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, value)
    assert ledger.papersim_dsn() == value


@pytest.mark.parametrize(
    "bad",
    [
        "postgresql://paper_sim@db.internal.example.com:5432/paper_sim",
        "postgresql://paper_sim@10.0.0.5:5432/paper_sim",
        "sqlite:///papersim.db",
        "papersim-ledger.jsonl",
        "not a dsn",
    ],
)
def test_papersim_dsn_rejects_non_local_through_repo_validator(monkeypatch, bad):
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, bad)
    with pytest.raises(ValueError, match="PAPERSIM_PG_DSN"):
        ledger.papersim_dsn()


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-dsn ",
        "  postgresql://paper_sim@db.internal.example.com:5432/paper_sim",
        "\npostgresql://paper_sim@10.0.0.5:5432/paper_sim\t",
        "\tpapersim-ledger.jsonl\n",
        " sqlite:///papersim.db ",
    ],
)
def test_papersim_dsn_rejects_padded_bad_overrides_not_silent_default(
    monkeypatch, bad
):
    # F4 regression: a nonempty override must never silently select the
    # documented loopback default. Padded malformed/hosted/file values
    # are stripped once and then rejected through the repo validator.
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, bad)
    with pytest.raises(ValueError, match="PAPERSIM_PG_DSN"):
        ledger.papersim_dsn()


@pytest.mark.parametrize(
    "padded",
    [
        "  postgresql://paper_sim@127.0.0.1:55432/paper_sim  ",
        "\npostgresql://paper_sim@localhost:55432/paper_sim\t",
        " host=127.0.0.1 port=55432 dbname=paper_sim user=paper_sim ",
    ],
)
def test_papersim_dsn_padded_local_override_stripped_then_validated(
    monkeypatch, padded
):
    # A padded VALID local DSN is accepted after a single strip pass.
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, padded)
    resolved = ledger.papersim_dsn()
    assert resolved == padded.strip()
    validate_local_postgres_dsn(resolved, env_var_name="PAPERSIM_PG_DSN")


@pytest.mark.parametrize("empty", ["", "   ", "\t\n\r "])
def test_papersim_dsn_empty_or_whitespace_only_keeps_documented_default(
    monkeypatch, empty
):
    # Only unset / empty / whitespace-only selects the documented default.
    monkeypatch.setenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, empty)
    assert ledger.papersim_dsn() == ledger.DEFAULT_PAPERSIM_PG_DSN


def test_papersim_config_rejects_malformed_override_at_construction():
    # The environment boundary rejects at construction time, before any
    # caller can resolve or echo a connection target.
    with pytest.raises(ValueError, match="PAPERSIM_PG_DSN"):
        SupabasePapersimConfig(dsn="not-a-dsn ")


def test_papersim_dsn_accepts_explicit_environment_mapping():
    assert (
        ledger.papersim_dsn(
            {"PAPERSIM_PG_DSN": "postgresql://paper_sim@127.0.0.1:55432/paper_sim"}
        )
        == "postgresql://paper_sim@127.0.0.1:55432/paper_sim"
    )
    with pytest.raises(ValueError, match="PAPERSIM_PG_DSN"):
        ledger.papersim_dsn(
            {"PAPERSIM_PG_DSN": "postgresql://paper_sim@db.example.com:5432/paper_sim"}
        )


def test_papersim_dsn_never_reads_or_creates_password_file(monkeypatch, tmp_path):
    bogus = tmp_path / "no-such-password-file"
    monkeypatch.setenv(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR, str(bogus))
    monkeypatch.delenv(ledger.PAPERSIM_PG_DSN_ENV_VAR, raising=False)
    assert ledger.papersim_dsn() == ledger.DEFAULT_PAPERSIM_PG_DSN
    assert not bogus.exists()
    assert ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR == "PAPERSIM_PG_PASSWORD_FILE"
    assert "PAPERSIM_PG_PASSWORD_FILE" in ledger.papersim_dsn.__doc__


def test_record_entry_builds_pure_entry_event():
    # FX-09L (design section 5.9): the former P0 stub is completed as a
    # PURE contract. The frozen field order stays asserted and construction
    # adds no I/O, no persistence, no clock read, no default fee category
    # and no rounding.
    assert tuple(field.name for field in fields(ledger.PaperLedgerEntry)) == (
        "position_id",
        "fixture",
        "question",
        "side",
        "notional",
        "price",
        "event",
        "evidence",
    )
    entry = ledger.record_entry(
        position_id="#48",
        fixture="Brentford FC vs. Nottingham Forest FC",
        question="Over 2.5",
        side="Over",
        notional=250.0,
        price=0.56,
        evidence=["book walk ok", "gate pass"],
    )
    assert entry == ledger.PaperLedgerEntry(
        position_id="#48",
        fixture="Brentford FC vs. Nottingham Forest FC",
        question="Over 2.5",
        side="Over",
        notional=250.0,
        price=0.56,
        event="entry",
        evidence=("book walk ok", "gate pass"),
    )
    assert entry.event == "entry"
    assert isinstance(entry.evidence, tuple)
    # Default evidence is the empty tuple (no hidden defaults anywhere else).
    assert ledger.record_entry("p", "f", "q", "YES", 10.0, 0.5).evidence == ()

    def call(**overrides):
        kwargs = dict(
            position_id="#48",
            fixture="BRE-FOR",
            question="Over 2.5",
            side="Over",
            notional=250.0,
            price=0.56,
            evidence=(),
        )
        kwargs.update(overrides)
        return ledger.record_entry(**kwargs)

    # ValueError family: non-empty string fields, finite positive notional,
    # open-interval price, string evidence items only.
    for overrides in (
        {"position_id": ""},
        {"position_id": 48},
        {"fixture": ""},
        {"fixture": None},
        {"question": ""},
        {"side": ""},
        {"evidence": ("ok", 7)},
        {"evidence": (b"ok",)},
        {"notional": 0.0},
        {"notional": -250.0},
        {"notional": float("nan")},
        {"notional": float("inf")},
        {"notional": "250"},
        {"price": 0.0},
        {"price": 1.0},
        {"price": 1.5},
        {"price": -0.1},
        {"price": float("nan")},
        {"price": float("inf")},
    ):
        with pytest.raises(ValueError):
            call(**overrides)


def test_mark_to_market_is_the_raw_per_share_delta():
    # FX-09L (design section 5.9): delta = exit_bid - entry_price, per
    # share of the held side, raw float subtraction (no rounding, no share
    # multiplication); the entry price keeps the open interval (0, 1) and
    # the mark/exit bid uses the CLOSED interval [0, 1] (settle_exit rule).
    assert ledger.mark_to_market(entry_price=0.56, exit_bid=0.54) == 0.54 - 0.56
    assert ledger.mark_to_market(0.56, 0.54) == pytest.approx(-0.02)
    assert ledger.mark_to_market(0.30, 0.30) == 0.0
    assert ledger.mark_to_market(0.25, 1.0) == pytest.approx(0.75)
    assert ledger.mark_to_market(0.25, 0.0) == pytest.approx(-0.25)
    for bad_entry, bad_bid in (
        (0.0, 0.5),
        (1.0, 0.5),
        (-0.1, 0.5),
        (1.5, 0.5),
        (float("nan"), 0.5),
        (float("inf"), 0.5),
        (0.5, -0.1),
        (0.5, 1.5),
        (0.5, float("nan")),
        (0.5, float("inf")),
    ):
        with pytest.raises(ValueError):
            ledger.mark_to_market(bad_entry, bad_bid)


@pytest.mark.skipif(
    not os.environ.get("PAPERSIM_PG_DSN"),
    reason=(
        INTEGRATION_LABEL
        + ": set PAPERSIM_PG_DSN (validated loopback paper_sim DSN) to enable; "
        "default runs never open any database connection"
    ),
)
def test_papersim_ledger_integration_relations_exist():
    dsn = ledger.papersim_dsn()
    validate_local_postgres_dsn(dsn, env_var_name="PAPERSIM_PG_DSN")
    psycopg = pytest.importorskip("psycopg")
    connect_kwargs = {"connect_timeout": 5}
    password_file = os.environ.get(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR)
    if password_file:
        connect_kwargs["password"] = Path(password_file).read_text(
            encoding="utf-8"
        ).strip()
    expected = {
        ("decisions", "BASE TABLE"),
        ("resolutions", "BASE TABLE"),
        ("bankroll", "BASE TABLE"),
        ("model_performance", "VIEW"),
        ("forecast_calibration", "VIEW"),
    }
    with psycopg.connect(dsn, **connect_kwargs) as connection:
        rows = connection.execute(
            "select table_name, table_type from information_schema.tables "
            "where table_schema = 'public' and table_name = any(%s)",
            ([name for name, _ in expected],),
        ).fetchall()
    assert {(str(name), str(kind)) for name, kind in rows} == expected


# ---------------------------------------------------------------------------
# FX-01 (2026-11-12): net-PnL semantics, queue_performance and the
# read-only report tool. Rates come from the FX-06 table, never literals
# in the settlement code; the hand cases below are the fix-list's.
# ---------------------------------------------------------------------------


def test_papersettlement_fee_field_defaults_keep_gross_contract():
    settlement = ledger.PaperSettlement(
        classification=ledger.OUTCOME_SETTLED, proceeds=100.0, pnl=50.0
    )
    assert settlement.fee_total == 0.0
    assert settlement.pnl_net == 50.0
    assert tuple(field.name for field in fields(ledger.PaperSettlement)) == (
        "classification",
        "proceeds",
        "pnl",
        "fee_total",
    )


def test_settle_natural_win_fee_hand_case():
    # $250 @ 0.56, sports rate 0.05: shares 446.4286, entry fee
    # 250 * 0.05 * 0.44 = 5.50; win gross +196.43, net +190.93.
    settlement = ledger.settle_natural(
        250.0, 0.56, "YES", "YES", fee_rate=_SPORTS
    )
    assert _SPORTS == 0.05
    assert 250.0 / 0.56 == pytest.approx(446.4286, abs=1e-4)
    assert settlement.pnl == pytest.approx(196.43, abs=0.01)
    assert settlement.fee_total == pytest.approx(5.50, abs=1e-9)
    assert settlement.pnl_net == pytest.approx(190.93, abs=0.01)
    assert settlement.win_loss == "win"


def test_settle_natural_loss_fee_hand_case():
    # Same entry losing: gross -250.00, net -255.50.
    settlement = ledger.settle_natural(250.0, 0.56, "YES", "NO", fee_rate=_SPORTS)
    assert settlement.pnl == pytest.approx(-250.00, abs=1e-9)
    assert settlement.fee_total == pytest.approx(5.50, abs=1e-9)
    assert settlement.pnl_net == pytest.approx(-255.50, abs=0.01)
    assert settlement.win_loss == "loss"


def test_settle_natural_entry_fee_matches_fees_module_exactly():
    rate = fees.taker_fee_rate("crypto")
    settlement = ledger.settle_natural(750.0, 0.515, "YES", "YES", fee_rate=rate)
    assert settlement.fee_total == pytest.approx(
        fees.entry_fee(750.0, 0.515, rate), abs=1e-12
    )
    zero = ledger.settle_natural(250.0, 0.56, "YES", "YES", fee_rate=0.0)
    assert zero.fee_total == 0.0
    assert zero.pnl_net == zero.pnl


def test_settle_exit_fee_hand_case():
    # $400 @ 0.69, exit 0.585, both legs sports rate: shares 579.7101,
    # entry fee 400 * 0.05 * 0.31 = 6.20, exit fee
    # 579.7101 * 0.05 * 0.585 * 0.415 = 7.04; gross -60.87, net -74.11.
    # (The live book recorded -60.70 on 580 whole shares; the exact
    # per-share arithmetic is authoritative here.)
    settlement = ledger.settle_exit(
        400.0, 0.69, 0.585, fee_rate=_SPORTS, exit_fee_rate=_SPORTS
    )
    assert settlement.pnl == pytest.approx(-60.87, abs=0.01)
    assert settlement.fee_total == pytest.approx(13.24, abs=0.01)
    assert settlement.pnl_net == pytest.approx(-74.11, abs=0.01)
    assert settlement.win_loss is None


def test_settle_exit_fee_legs_are_independent_and_default_free():
    both = ledger.settle_exit(400.0, 0.69, 0.585, fee_rate=_SPORTS, exit_fee_rate=_SPORTS)
    entry_only = ledger.settle_exit(400.0, 0.69, 0.585, fee_rate=_SPORTS)
    exit_only = ledger.settle_exit(400.0, 0.69, 0.585, exit_fee_rate=_SPORTS)
    none_charged = ledger.settle_exit(400.0, 0.69, 0.585)
    assert entry_only.fee_total == pytest.approx(6.20, abs=0.01)
    assert exit_only.fee_total == pytest.approx(7.04, abs=0.01)
    assert both.fee_total == pytest.approx(
        entry_only.fee_total + exit_only.fee_total, abs=1e-9
    )
    assert none_charged.fee_total == 0.0
    assert none_charged.pnl_net == none_charged.pnl
    # Bounds remain fee-free: selling at 0 or 1 costs nothing.
    zero = ledger.settle_exit(200.0, 0.5, 0.0, fee_rate=_SPORTS, exit_fee_rate=_SPORTS)
    assert zero.fee_total == pytest.approx(200.0 * 0.05 * 0.5, abs=1e-9)


@pytest.mark.parametrize("bad", [-0.01, float("nan"), float("inf")])
def test_settle_functions_reject_bad_fee_rates(bad):
    with pytest.raises(ValueError, match="fee_rate"):
        ledger.settle_natural(250.0, 0.56, "YES", "YES", fee_rate=bad)
    with pytest.raises(ValueError, match="fee_rate"):
        ledger.settle_exit(400.0, 0.69, 0.585, fee_rate=bad)
    with pytest.raises(ValueError, match="exit_fee_rate"):
        ledger.settle_exit(400.0, 0.69, 0.585, exit_fee_rate=bad)


def _snapshot_rows():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_queue_performance_recorded_mode_reproduces_published_audit_numbers():
    # FX-01 acceptance: on the 10-30 snapshot the report must reproduce
    # gross 23.30% (principal 10,954.36, pnl 2,552.43) and land the net
    # ROI in 20-21% (exact value depends on per-market classification).
    performance = ledger.queue_performance(
        _snapshot_rows(), pnl_source=ledger.PNL_SOURCE_RECORDED
    )
    assert performance.entries == 40
    assert performance.voided_excluded == 0
    assert performance.unresolved_excluded == 0
    assert performance.stake == pytest.approx(10954.36, abs=0.01)
    assert performance.gross_pnl == pytest.approx(2552.43, abs=0.01)
    assert performance.gross_roi == pytest.approx(0.2330, abs=0.0005)
    assert performance.entry_fees == pytest.approx(294.25, abs=0.05)
    assert performance.exit_fees == pytest.approx(27.09, abs=0.02)
    assert 0.20 <= performance.net_roi_entry_fees_only <= 0.21
    assert 0.20 <= performance.net_roi <= 0.21
    assert performance.fee_schedule_as_of == fees.FEE_SCHEDULE_AS_OF
    # Classification table: gold/equity rows in crypto_touch are
    # sports-rate (scan team rule), crypto teams are crypto-rate.
    classification = dict(
        (team, (category, rate))
        for team, category, rate in performance.team_classification
    )
    assert classification["commodities_gold"] == ("sports", 0.05)
    assert classification["equity_indices"] == ("sports", 0.05)
    assert classification["crypto_eth"] == ("crypto", 0.07)
    # crypto_touch is a MIXED queue: 2 crypto rows at 0.07 + 1 equity
    # row at 0.05 (V-6: queue entry fees 32.81).
    touch = next(q for q in performance.queues if q.market_type == "crypto_touch")
    assert touch.categories == ("crypto", "sports")
    assert touch.entries == 3
    assert touch.entry_fees == pytest.approx(32.81, abs=0.02)


def _synthetic_rows():
    return [
        {
            "id": 1, "team": "sports_soccer", "market_type": "ou_over",
            "side": "YES", "stake": 250.0, "price": 0.56,
            "outcome": "YES", "pnl": 196.43,
        },
        {
            "id": 2, "team": "sports_soccer", "market_type": "ou_over",
            "side": "YES", "stake": 350.0, "price": 0.56,
            "outcome": "NO", "pnl": -350.0,
        },
        {
            "id": 3, "team": "crypto_eth", "market_type": "crypto_touch",
            "side": "YES", "stake": 750.0, "price": 0.515,
            "outcome": "YES", "pnl": 706.31,
        },
        {
            "id": 4, "team": "crypto_btc", "market_type": "crypto_daily",
            "side": "YES", "stake": 250.0, "price": 0.5,
            "outcome": "win", "pnl": 250.0,
        },
        {
            "id": 5, "team": "crypto_btc", "market_type": "crypto_daily",
            "side": "YES", "stake": 100.0, "price": 0.25,
            "outcome": "loss", "pnl": -100.0,
        },
        {
            "id": 6, "team": "macro_rates", "market_type": "macro",
            "side": "NO", "stake": 400.0, "price": 0.69,
            "outcome": "EXITED", "pnl": -60.70,
        },
        {
            "id": 7, "team": "sports_soccer", "market_type": "btts",
            "side": "YES", "stake": 150.0, "price": 0.4,
            "outcome": "VOIDED", "pnl": 0.0,
        },
        {
            "id": 8, "team": "sports_soccer", "market_type": "btts",
            "side": "YES", "stake": 150.0, "price": 0.4,
            "outcome": None, "pnl": None,
        },
    ]


def test_queue_performance_recomputed_mode_v6_semantics():
    performance = ledger.queue_performance(_synthetic_rows())
    assert performance.entries == 6
    assert performance.voided_excluded == 1
    assert performance.unresolved_excluded == 1
    # Gross: recomputed per entry; exits take the recorded pnl.
    # r1 250@0.56 YES hit: +196.43; r2 miss: -350.00;
    # r3 750@0.515 hit: +706.31; r4 win label: +250.00;
    # r5 loss label: -100.00; r6 exit recorded: -60.70.
    expected_gross = (
        (250.0 / 0.56 - 250.0)
        - 350.0
        + (750.0 / 0.515 - 750.0)
        + (250.0 / 0.5 - 250.0)
        - 100.0
        - 60.70
    )
    assert performance.gross_pnl == pytest.approx(expected_gross, abs=0.01)
    # Entry fees: stake * rate * (1 - price) per row, team-rule rates.
    expected_entry_fees = (
        250.0 * 0.05 * 0.44
        + 350.0 * 0.05 * 0.44
        + 750.0 * 0.07 * 0.485
        + 250.0 * 0.07 * 0.50
        + 100.0 * 0.07 * 0.75
        + 400.0 * 0.05 * 0.31
    )
    assert performance.entry_fees == pytest.approx(expected_entry_fees, abs=0.01)
    # Exit fee: exit price reconstructed from recorded pnl
    # (-60.70 + 400) / (400 / 0.69) ~= 0.585 -> sell fee ~= 7.04.
    assert performance.exit_fees == pytest.approx(7.04, abs=0.02)
    assert performance.net_pnl == pytest.approx(
        expected_gross - expected_entry_fees - 7.04, abs=0.05
    )
    # VOIDED stake is refunded: not in the denominator.
    expected_stake = 250.0 + 350.0 + 750.0 + 250.0 + 100.0 + 400.0
    assert performance.stake == pytest.approx(expected_stake, abs=1e-9)
    daily = next(q for q in performance.queues if q.market_type == "crypto_daily")
    assert daily.categories == ("crypto",)
    assert daily.net_roi == pytest.approx(
        ((250.0 / 0.5 - 250.0) - 100.0 - 250.0 * 0.07 * 0.5 - 100.0 * 0.07 * 0.75)
        / 350.0,
        abs=1e-6,
    )


def test_queue_performance_cross_checks_settle_arithmetic():
    rows = [
        {
            "team": "sports_soccer", "market_type": "ou_over", "side": "YES",
            "stake": 250.0, "price": 0.56, "outcome": "YES", "pnl": 999.0,
        },
    ]
    performance = ledger.queue_performance(rows)
    settlement = ledger.settle_natural(
        250.0, 0.56, "YES", "YES", fee_rate=_SPORTS
    )
    assert performance.gross_pnl == pytest.approx(settlement.pnl, abs=1e-9)
    assert performance.entry_fees == pytest.approx(settlement.fee_total, abs=1e-9)
    # recorded mode ignores the recomputation and uses the recorded pnl.
    recorded = ledger.queue_performance(rows, pnl_source=ledger.PNL_SOURCE_RECORDED)
    assert recorded.gross_pnl == pytest.approx(999.0, abs=1e-9)


def test_queue_performance_rejects_unknown_inputs():
    rows = [
        {
            "team": "sports_soccer", "market_type": "ou_over", "side": "YES",
            "stake": 250.0, "price": 0.56, "outcome": "MUTUAL", "pnl": 0.0,
        },
    ]
    with pytest.raises(ValueError, match="unknown resolution outcome"):
        ledger.queue_performance(rows)
    with pytest.raises(ValueError, match="pnl_source"):
        ledger.queue_performance([], pnl_source="as-published")
    # recorded mode and EXITED rows require a recorded pnl.
    missing = [
        {
            "team": "sports_soccer", "market_type": "ou_over", "side": "YES",
            "stake": 250.0, "price": 0.56, "outcome": "YES",
        },
    ]
    with pytest.raises(ValueError, match="recorded"):
        ledger.queue_performance(missing, pnl_source=ledger.PNL_SOURCE_RECORDED)
    exited_missing = [
        {
            "team": "macro_rates", "market_type": "macro", "side": "NO",
            "stake": 400.0, "price": 0.69, "outcome": "EXITED",
        },
    ]
    with pytest.raises(ValueError, match="recorded"):
        ledger.queue_performance(exited_missing)
    empty = ledger.queue_performance([])
    assert empty.entries == 0
    assert empty.gross_roi is None
    assert empty.net_roi is None
    assert empty.queues == ()


# ---------------------------------------------------------------------------
# tools/papersim_ledger_report.py (FX-01 read-only report)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_tool():
    spec = importlib.util.spec_from_file_location(
        "papersim_ledger_report_under_test", TOOL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_tool_runs_on_audit_snapshot_with_acceptance_numbers(
    report_tool, capsys,
):
    exit_code = report_tool.main(["--ledger-json", str(SNAPSHOT_PATH)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "gross roi: +23.30%" in out
    assert "2552.43" in out
    assert "10954.36" in out
    assert "entries counted: 40" in out
    assert "pnl source: recorded" in out
    # Net ROI lands in the required 20-21% band (both fee variants).
    for line in out.splitlines():
        if "net roi:" in line:
            value = float(line.split("net roi: ")[1].strip("%+\n"))
            assert 20.0 <= value <= 21.0, line
    assert "commodities_gold -> sports (0.05)" in out
    assert "crypto_eth -> crypto (0.07)" in out
    assert "crypto+sports" in out  # the mixed crypto_touch queue
    assert "ESTIMATES" in out  # the historical-net caveat


def test_report_tool_csv_mode_recomputed_default(tmp_path, report_tool, capsys):
    decisions = tmp_path / "decisions.csv"
    resolutions = tmp_path / "resolutions.csv"
    decisions.write_text(
        "id,market_id,team,market_type,side,stake,price\n"
        "1,m1,sports_soccer,ou_over,YES,250.0,0.56\n"
        "2,m2,commodities_gold,crypto_touch,YES,249.73,0.565\n"
        "3,m3,sports_soccer,btts,YES,150.0,0.40\n"
        "4,m4,crypto_eth,crypto_touch,YES,750.0,0.515\n",
        encoding="utf-8",
    )
    resolutions.write_text(
        "market_id,outcome,payoff,pnl\n"
        "m1,YES,446.43,196.43\n"
        "m2,YES,441.99,192.27\n"
        "m3,VOIDED,150.00,0.00\n"
        "4,NO,0.00,-750.00\n",  # market_id mismatch -> unresolved join miss
        encoding="utf-8",
    )
    exit_code = report_tool.main(
        ["--decisions-csv", str(decisions), "--resolutions-csv", str(resolutions)]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "pnl source: recomputed" in out
    # m1 + m2 counted; m3 VOIDED (refunded); m4 join miss (unresolved).
    assert "entries counted: 2" in out
    assert "VOIDED refunded/excluded: 1" in out
    assert "unresolved/open excluded: 1" in out
    # Gold row in crypto_touch is sports-rate; the unresolved crypto_eth
    # row is excluded and never classified.
    assert "commodities_gold -> sports (0.05)" in out
    assert "crypto_eth" not in out


@pytest.mark.parametrize("pnl_source", ["recorded", "recomputed"])
def test_report_excludes_ambiguous_market_and_displays_reason(
    tmp_path, report_tool, capsys, pnl_source,
):
    decisions = tmp_path / "decisions.csv"
    resolutions = tmp_path / "resolutions.csv"
    decisions.write_text(
        "id,ts,market_id,team,market_type,side,stake,price,shares\n"
        "74,2026-11-15 08:13:09+00,6509633,sports_nba,MONEYLINE,YES,150,.59,254.24\n"
        "78,2026-11-15 16:20:37+00,6509633,sports_nba,MONEYLINE,YES,50,.60,83.33\n"
        "80,2026-11-15 08:00:00+00,m-ok,sports_soccer,btts,YES,100,.50,200\n",
        encoding="utf-8",
    )
    resolutions.write_text(
        "market_id,ts,outcome,payoff,pnl,evidence\n"
        "6509633,2026-11-15 14:31:29+00,win,83.33,33.33,Re-entry #78; old #74 VOIDED.\n"
        "m-ok,2026-11-15 23:00:00+00,YES,200,100,Evidence\n",
        encoding="utf-8",
    )
    assert report_tool.main([
        "--decisions-csv", str(decisions), "--resolutions-csv", str(resolutions),
        "--pnl-source", pnl_source,
    ]) == 0
    out = capsys.readouterr().out
    assert "entries counted: 1" in out
    assert "unresolved/open excluded: 0" in out
    assert "gross pnl: 100.00" in out
    assert "ambiguous settlements excluded: entries=2 markets=1" in out
    assert "papersim_legacy_resolution_precedes_entry" in out
    assert "market_id='6509633' decision_ids=['74', '78']" in out
    assert "sports_nba" not in out


def test_report_csv_allocates_verified_recorded_market_pnl_once(report_tool):
    source = ROOT / "docs" / "paper-sim" / "ledger-snapshots" / "2026-11-12"
    rows = report_tool.load_rows_csv(source / "decisions.csv", source / "resolutions.csv")
    by_id = {row["id"]: row for row in rows}
    assert by_id["1"]["pnl"] == pytest.approx(706.11)
    assert by_id["14"]["pnl"] == pytest.approx(599.09)
    assert all("settlement_issue" not in row for row in rows)
    recorded = ledger.queue_performance(rows, pnl_source=ledger.PNL_SOURCE_RECORDED)
    assert recorded.gross_pnl == pytest.approx(3768.33)


@pytest.mark.parametrize("evidence", [
    "Settles decision #102 only.",
    "This settlement is for decision #102 only.",
    "Settlement applies only to decision #102.",
    "Only decision #102 was settled.",
    "This settlement does not settle decision #101.",
    "Decision #101 was not settled.",
    "Settles decision #101. Decision #101 was not settled.",
])
def test_report_excludes_only_the_inconsistent_single_decision_attribution(
    evidence, tmp_path, report_tool, capsys,
):
    decisions = tmp_path / "decisions.csv"
    resolutions = tmp_path / "resolutions.csv"
    decisions.write_text(
        "id,market_id,team,market_type,side,stake,price\n"
        "101,m-wrong,sports_soccer,btts,YES,50,.50\n"
        "103,m-context,sports_soccer,btts,YES,80,.50\n"
        "104,m-blank,sports_soccer,btts,YES,40,.50\n"
        "105,m-other,sports_soccer,btts,YES,30,.50\n",
        encoding="utf-8",
    )
    resolutions.write_text(
        "market_id,outcome,payoff,pnl,evidence\n"
        f"m-wrong,YES,100,50,{evidence}\n"
        "m-context,YES,160,80,Compare previous market from decision #102.\n"
        "m-blank,YES,80,40,\n"
        "m-other,YES,60,30,This settlement does not settle decision #102.\n",
        encoding="utf-8",
    )
    assert report_tool.main([
        "--decisions-csv", str(decisions), "--resolutions-csv", str(resolutions),
    ]) == 0
    out = capsys.readouterr().out
    assert "entries counted: 3" in out
    assert "gross pnl: 150.00" in out
    assert "ambiguous settlements excluded: entries=1 markets=1" in out
    assert ("papersim_legacy_resolution_evidence_conflict: "
            "market_id='m-wrong' decision_ids=['101']") in out


def test_report_tool_pg_mode_is_read_only_join(report_tool, monkeypatch):
    captured: dict[str, object] = {}

    class _Cursor:
        description = [
            type("Col", (), {"name": name})()
            for name in (
                "id", "market_id", "team", "market_type", "side",
                "stake", "price", "ts", "shares", "resolution_ts", "outcome",
                "pnl", "payoff", "evidence",
            )
        ]

        def fetchall(self):
            return [
                (1, "m1", "sports_soccer", "ou_over", "YES", 250.0, 0.56,
                 "2026-10-01T12:00:00+00:00", 446.43, "2026-10-01T13:00:00+00:00",
                 "YES", 196.43, 446.43, "e1"),
                (2, "m2", "sports_soccer", "ou_over", "YES", 100.0, 0.50,
                 "2026-10-01T14:00:00+00:00", 200, "2026-10-01T13:00:00+00:00",
                 "YES", 100.00, 200, "e2"),
            ]

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            captured["sql"] = sql
            return _Cursor()

        def close(self):
            captured["closed"] = True

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return _Connection()

    monkeypatch.setattr(
        report_tool.ledger, "papersim_dsn", lambda: "postgresql://paper_sim@127.0.0.1:55432/paper_sim"
    )
    rows = report_tool.load_rows_pg(fake_connect)
    sql = str(captured["sql"]).lower()
    assert sql.strip().startswith("select")
    assert "left join public.resolutions" in sql
    assert "r.ts as resolution_ts" in sql
    assert "update" not in sql and "insert" not in sql and "delete" not in sql
    # Session is forced read-only at connect time.
    assert str(captured["kwargs"].get("options", "")).find("default_transaction_read_only=on") >= 0
    assert rows[0] == {
        "id": 1, "market_id": "m1", "team": "sports_soccer",
        "market_type": "ou_over", "side": "YES", "stake": 250.0,
        "price": 0.56, "outcome": "YES", "pnl": 196.43,
    }
    assert rows[1]["outcome"] is rows[1]["pnl"] is None
    assert "papersim_legacy_resolution_precedes_entry" in rows[1]["settlement_issue"]
    assert "market_id='m2' decision_ids=['2']" in rows[1]["settlement_issue"]
    # The connection is explicitly closed (review P3#4).
    assert captured.get("closed") is True


def test_report_tool_requires_exactly_one_source(report_tool):
    with pytest.raises(SystemExit):
        report_tool.build_parser().parse_args([])
    with pytest.raises(SystemExit):
        report_tool.build_parser().parse_args(
            ["--ledger-json", "x.json", "--from-pg"]
        )
    with pytest.raises(SystemExit, match="resolutions-csv"):
        report_tool.main(["--decisions-csv", "decisions.csv"])


def test_report_tool_production_connect_routes_through_the_adapter(
    report_tool, monkeypatch,
):
    # FX-09L (design 6.2 L3): the production --from-pg connect is built by
    # the single allowlisted adapter; a fake injected into sys.modules keeps
    # this an offline proof (the real adapter imports psycopg at module
    # level, which must never happen in the offline suite).
    import sys
    import types

    calls: list[tuple[str, str | None]] = []

    def fake_connect_legacy_readonly(dsn, *, password_file=None):
        calls.append((dsn, password_file))
        sentinel = object()
        return sentinel

    fake_module = types.ModuleType(
        "polymarket_alpha_lab.papersim.store_psycopg"
    )
    fake_module.connect_legacy_readonly = fake_connect_legacy_readonly
    monkeypatch.setitem(
        sys.modules,
        "polymarket_alpha_lab.papersim.store_psycopg",
        fake_module,
    )
    dsn = "postgresql://paper_sim@127.0.0.1:55432/paper_sim"

    monkeypatch.setenv(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR, "/tmp/ops-pw")
    connect = report_tool._build_connect()
    connection = connect(
        dsn,
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
    )
    assert connection is not None
    assert calls == [(dsn, "/tmp/ops-pw")]

    monkeypatch.delenv(ledger.PAPERSIM_PG_PASSWORD_FILE_ENV_VAR, raising=False)
    calls.clear()
    connect = report_tool._build_connect()
    connect(
        dsn,
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
    )
    assert calls == [(dsn, None)]

    # The contract kwargs are checked, not silently swallowed: any drift of
    # the load_rows_pg callback shape fails loudly instead of bypassing the
    # adapter's read-only forcing.
    with pytest.raises(ValueError, match="unexpected load_rows_pg connect kwargs"):
        connect(dsn, connect_timeout=9, options="-c default_transaction_read_only=on")
    with pytest.raises(ValueError, match="unexpected load_rows_pg connect kwargs"):
        connect(dsn)
    # No fake adapter call happened for the rejected shapes.
    assert calls == [(dsn, None)]
