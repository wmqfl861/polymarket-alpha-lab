"""MO-4 dataset-layer tests: contracts, constructors and leakage guards.

Plan 05 node MO-4 acceptance, per constructor: future-row injection;
same snapshot bytes give bit-identical manifests (and equivalent-but-
different bytes give the same content-level summary); missing fields,
duplicate matches and time inversions fail closed with explicit codes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab.papersim.optimize import dataset as ds
from tools import wp_p4_history as history_tool

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]
FROZEN_DIR = REPO / "docs/paper-sim/research/league-admission-20261119"
NEW_SOCCER_PIN = "2e6669619a236b544347836801ffe683770eb831c4fecdf4cc6d443c990ec053"
NBA_PIN = "bbcc612ef6342fc12377ee994f2fc65a5c8f4a08f275ad7244684b87f1b4917a"

PAIRS = [(("A", "B"), ("C", "D")), (("A", "C"), ("D", "B")),
         (("A", "D"), ("B", "C")), (("B", "A"), ("D", "C")),
         (("C", "A"), ("B", "D")), (("D", "A"), ("C", "B"))]


def _xg_block(season: str, rounds: int, teams=("A", "B", "C", "D"), home_xg="1.3") -> dict:
    dates = []
    for rnd, round_pairs in enumerate(PAIRS[:rounds], 1):
        kickoff = datetime(int(season), 8, 1, 12, tzinfo=UTC) + timedelta(days=7 * (rnd - 1))
        for home, away in round_pairs:
            dates.append({"id": f"{season}-{rnd}-{home}", "isResult": True,
                          "h": {"title": home}, "a": {"title": away},
                          "datetime": kickoff.isoformat(),
                          "goals": {"h": "1", "a": "0"},
                          "xG": {"h": home_xg, "a": "1.1"}})
    teams_map = {team: {"id": team, "title": team, "history": []} for team in teams}
    return {"teams": teams_map, "dates": dates}


@pytest.fixture
def four_club_leagues(monkeypatch):
    monkeypatch.setitem(ds.LEAGUES, "EPL", (47, 4, 6))


# ---------------------------------------------------------------------------
# Move fidelity: the tool wrapper is a thin re-export shim (P3-3)
# ---------------------------------------------------------------------------

def test_tool_wrapper_reexports_surface_and_shares_objects():
    names = ("UTC", "LEAGUES", "ALIASES", "utc", "iso", "HistoricalMatch",
             "ScheduleFixture", "HistoryAudit", "Fold", "canonical_team",
             "parse_schedule", "parse_matches", "history_at", "build_folds",
             "to_model_history", "production_snapshot", "inventory",
             "history_from_archive", "load_history")
    for name in names:
        assert hasattr(history_tool, name), name
    assert history_tool.LEAGUES is ds.LEAGUES
    assert history_tool.ALIASES is ds.ALIASES
    assert history_tool.HistoricalMatch is ds.HistoricalMatch
    assert history_tool.build_folds is ds.build_folds
    assert history_tool.history_at is ds.history_at


def test_history_from_archive_parses_bytes_and_fails_closed(four_club_leagues):
    block = _xg_block("2025", 6)
    big5 = json.dumps({"EPL_2025": block}).encode()
    files = {"understat_latest_pointer.json": json.dumps(
                 {"sha256": hashlib.sha256(big5).hexdigest()}).encode(),
             "understat_latest.json": big5,
             "round31/raw/xg_fotmob_championship.json": b"{}",
             "round39/raw/xg_fotmob_eredivisie.json": b"{}"}
    audit = ds.history_from_archive(files)
    assert len(audit.matches) == 12
    assert [source["path"] for source in audit.sources] == [
        "understat_latest_pointer.json", "understat_latest.json",
        "round31/raw/xg_fotmob_championship.json", "round39/raw/xg_fotmob_eredivisie.json"]
    assert all("resolved_path" not in source for source in audit.sources)
    assert any("official round map absent" in note for note in audit.notes)
    # Pointer integrity still fires before any league file is consulted.
    bad = dict(files)
    bad["understat_latest_pointer.json"] = b'{"sha256": "wrong"}'
    with pytest.raises(ValueError, match="SHA256"):
        ds.history_from_archive(bad)
    del bad["round31/raw/xg_fotmob_championship.json"]
    bad["understat_latest_pointer.json"] = files["understat_latest_pointer.json"]
    with pytest.raises(ValueError, match="declared archive file missing"):
        ds.history_from_archive(bad)


# ---------------------------------------------------------------------------
# Constructor 1: registered snapshot bytes
# ---------------------------------------------------------------------------

def test_snapshot_bytes_xg_contract_and_rejections(four_club_leagues):
    content = json.dumps({"EPL_2025": _xg_block("2025", 6)}).encode()
    built = ds.from_snapshot_bytes(content=content, kind="understat_xg")
    assert built.manifest.origin == ds.ORIGIN_SNAPSHOT
    assert built.manifest.match_rows == 12
    assert built.manifest.first_kickoff == "2025-08-01T12:00:00Z"
    assert built.manifest.last_kickoff == "2025-09-05T12:00:00Z"
    assert all(match.available_at is None and match.matchweek is None
               for match in built.matches)
    with pytest.raises(ValueError, match="dataset_snapshot_kind_unknown"):
        ds.from_snapshot_bytes(content=content, kind="gamma_market")
    with pytest.raises(ValueError, match="dataset_snapshot_digest_invalid"):
        ds.from_snapshot_bytes(content=content, kind="understat_xg", content_sha256="zz")
    with pytest.raises(ValueError, match="dataset_snapshot_root_not_object"):
        ds.from_snapshot_bytes(content=b"[1]", kind="understat_xg")
    with pytest.raises(ValueError, match="dataset_snapshot_league_unknown"):
        ds.from_snapshot_bytes(content=b'{"Premier_2025": {}}', kind="understat_xg")
    with pytest.raises(ValueError, match="dataset_missing_field:dates"):
        ds.from_snapshot_bytes(content=b'{"EPL_2025": {"teams": {}}}', kind="understat_xg")


def test_same_bytes_produce_bit_identical_manifests(four_club_leagues):
    content = json.dumps({"EPL_2025": _xg_block("2025", 6)}).encode()
    first = ds.from_snapshot_bytes(content=content, kind="understat_xg")
    second = ds.from_snapshot_bytes(content=content, kind="understat_xg")
    assert first.manifest == second.manifest
    assert first.manifest.manifest_sha256() == second.manifest.manifest_sha256()
    assert first == second


def test_equivalent_bytes_share_content_summary_not_references(four_club_leagues):
    spelled = json.dumps({"EPL_2025": _xg_block("2025", 6, home_xg="1.30")}).encode()
    canonical = json.dumps({"EPL_2025": _xg_block("2025", 6, home_xg="1.3")}, sort_keys=True).encode()
    first = ds.from_snapshot_bytes(content=spelled, kind="understat_xg")
    second = ds.from_snapshot_bytes(content=canonical, kind="understat_xg")
    assert first.content_sha256() == second.content_sha256()
    assert first.manifest.snapshot_sha256 != second.manifest.snapshot_sha256
    assert first.manifest.manifest_sha256() != second.manifest.manifest_sha256()
    changed = json.dumps({"EPL_2025": _xg_block("2025", 6, home_xg="1.4")}).encode()
    third = ds.from_snapshot_bytes(content=changed, kind="understat_xg")
    assert third.content_sha256() != first.content_sha256()


def test_registry_only_kinds_pin_provenance_without_rows():
    for kind in ("intl_snapshot", "espn_nba_snapshot", "kraken_candles"):
        content = json.dumps({"generated_at": "2026-11-12T00:00:00Z", "teams": {}}).encode()
        built = ds.from_snapshot_bytes(content=content, kind=kind)
        assert built.matches == () and built.observations == ()
        assert built.manifest.snapshot_sha256 == (hashlib.sha256(content).hexdigest(),)
        assert any("MO-5b/MO-5c/MO-10" in note for note in built.notes)
    with pytest.raises(ValueError, match="dataset_snapshot_json_invalid"):
        ds.from_snapshot_bytes(content=b"\xff\xfe not json", kind="kraken_candles")


def test_snapshot_constructor_future_row_injection(four_club_leagues):
    # Round-level fold invariance is pinned by the unchanged wp_p4 tests
    # (mini_audit); this is the constructor-level guard: rows dated at or
    # after the cutoff must not change the history the cutoff can see.
    base_payload = {"EPL_2025": _xg_block("2025", 6), "EPL_2026": _xg_block("2026", 1)}
    base = ds.from_snapshot_bytes(content=json.dumps(base_payload).encode(), kind="fotmob_xg")
    # Round 2 kicks off exactly at the cutoff (same-day injection); round 3 later.
    cutoff = datetime(2026, 8, 8, 12, tzinfo=UTC)
    assert datetime(2026, 8, 1, 12, tzinfo=UTC) + timedelta(hours=3) < cutoff
    audit_base = ds.HistoryAudit(base.matches, (), (), (), ())
    train_before, groups_before = ds.history_at(audit_base, "EPL", "2026", cutoff)

    grown_payload = {"EPL_2025": _xg_block("2025", 6), "EPL_2026": _xg_block("2026", 3)}
    grown = ds.from_snapshot_bytes(content=json.dumps(grown_payload).encode(), kind="fotmob_xg")
    assert grown.manifest.match_rows == 18
    audit_grown = ds.HistoryAudit(grown.matches, (), (), (), ())
    train_after, groups_after = ds.history_at(audit_grown, "EPL", "2026", cutoff)
    assert train_after == train_before  # Bit-identical safe history at the cutoff.
    assert groups_after == groups_before
    assert len(train_before) == 12 and {m.season for m in train_before} == {"2025"}
    assert all(match.kickoff + timedelta(hours=3) < cutoff for match in train_after)


# ---------------------------------------------------------------------------
# Constructor 2: managed-store rows
# ---------------------------------------------------------------------------

def _candidate_rows(**overrides):
    row = {"candidate_id": 7, "condition_id": "0xabc", "market_id": None,
           "market_slug": None, "market_type": "1x2", "side": "YES"}
    row.update(overrides)
    return [row]


def _evidence_rows(extra=()):
    rows = [
        {"candidate_id": 7, "evidence_id": 10, "evidence_role": "DECISION",
         "observed_at": datetime(2026, 8, 8, 10, tzinfo=UTC), "model_probability": 0.6,
         "mid": None, "bid": "0.52", "ask": "0.56", "last": None,
         "snapshot_sha256": "a" * 64, "source_kind": "clob"},
        {"candidate_id": 7, "evidence_id": 11, "evidence_role": "CLOSE",
         "observed_at": datetime(2026, 8, 8, 11, tzinfo=UTC), "model_probability": None,
         "mid": "0.55", "bid": None, "ask": None, "last": "0.55",
         "snapshot_sha256": None, "source_kind": "gamma"},
        *extra,
    ]
    return rows


def _outcome_rows():
    return [{"outcome_id": 1, "condition_id": "0xabc", "market_id": None,
             "market_slug": None, "outcome": "YES",
             "observed_at": datetime(2026, 8, 8, 15, tzinfo=UTC), "source_kind": "gamma_close"}]


def test_managed_rows_contract_and_evidence_column_face():
    for statement in (ds.MANAGED_CANDIDATES_SQL, ds.MANAGED_EVIDENCE_SQL, ds.MANAGED_OUTCOMES_SQL):
        assert statement.lstrip().upper().startswith("SELECT")
        assert "INSERT" not in statement.upper() and "UPDATE" not in statement.upper()
    select_list = ds.MANAGED_EVIDENCE_SQL.split("FROM")[0]
    for column in ("evidence_id", "observed_at"):
        assert column in select_list  # P2-1: barrier and ordering columns present


def test_managed_rows_happy_path_with_event_start_at():
    candidates = _candidate_rows(event_start_at=datetime(2026, 8, 8, 12, tzinfo=UTC))
    built = ds.from_managed_rows(candidate_rows=candidates,
                                 evidence_rows=_evidence_rows(),
                                 outcome_rows=_outcome_rows())
    assert built.manifest.origin == ds.ORIGIN_MANAGED
    assert built.manifest.observation_rows == 1 and built.manifest.outcome_rows == 1
    observation = built.observations[0]
    assert observation.match_key == "0xabc" and observation.mid == pytest.approx(0.54)
    assert observation.observed_at == datetime(2026, 8, 8, 10, tzinfo=UTC)
    assert built.manifest.snapshot_sha256 == ("a" * 64,)
    assert not any("pending_event_start" in note for note in built.notes)


def test_managed_rows_pending_barrier_note_without_event_start_at():
    built = ds.from_managed_rows(candidate_rows=_candidate_rows(),
                                 evidence_rows=_evidence_rows(),
                                 outcome_rows=_outcome_rows())
    assert built.manifest.observation_rows == 1  # Not skipped, not fabricated.
    assert "dataset_close_barrier_pending_event_start:0xabc" in built.notes


def test_managed_rows_time_inversions_fail_closed():
    candidates = _candidate_rows(event_start_at=datetime(2026, 8, 8, 12, tzinfo=UTC))
    late_close = {"candidate_id": 7, "evidence_id": 12, "evidence_role": "CLOSE",
                  "observed_at": datetime(2026, 8, 8, 9, tzinfo=UTC), "model_probability": None,
                  "mid": "0.55", "bid": None, "ask": None, "last": None,
                  "snapshot_sha256": None, "source_kind": "gamma"}
    with pytest.raises(ValueError, match="dataset_time_inversion:decision_after_close"):
        ds.from_managed_rows(candidate_rows=candidates,
                             evidence_rows=_evidence_rows(extra=[late_close]),
                             outcome_rows=_outcome_rows())
    after_outcome = dict(late_close, observed_at=datetime(2026, 8, 8, 16, tzinfo=UTC),
                         evidence_id=12)
    with pytest.raises(ValueError, match="dataset_time_inversion:close_after_outcome"):
        ds.from_managed_rows(candidate_rows=candidates,
                             evidence_rows=_evidence_rows(extra=[after_outcome]),
                             outcome_rows=_outcome_rows())
    with pytest.raises(ValueError, match="dataset_time_inversion:decision_after_kickoff"):
        ds.from_managed_rows(candidate_rows=_candidate_rows(
                                 event_start_at=datetime(2026, 8, 8, 9, tzinfo=UTC)),
                             evidence_rows=_evidence_rows(), outcome_rows=_outcome_rows())
    with pytest.raises(ValueError, match="dataset_time_inversion:close_after_kickoff"):
        ds.from_managed_rows(candidate_rows=_candidate_rows(
                                 event_start_at=datetime(2026, 8, 8, 10, 30, tzinfo=UTC)),
                             evidence_rows=_evidence_rows(), outcome_rows=_outcome_rows())


def test_managed_rows_missing_duplicate_and_unpriceable_fail_closed():
    evidence = _evidence_rows()
    del evidence[0]["observed_at"]
    with pytest.raises(ValueError, match="dataset_missing_field:observed_at"):
        ds.from_managed_rows(candidate_rows=_candidate_rows(), evidence_rows=evidence,
                             outcome_rows=_outcome_rows())
    no_side = _candidate_rows()[0]
    del no_side["side"]
    with pytest.raises(ValueError, match="dataset_missing_field:side"):
        ds.from_managed_rows(candidate_rows=[no_side], evidence_rows=_evidence_rows(),
                             outcome_rows=_outcome_rows())
    with pytest.raises(ValueError, match="dataset_duplicate_match"):
        ds.from_managed_rows(candidate_rows=_candidate_rows() + _candidate_rows(),
                             evidence_rows=_evidence_rows(), outcome_rows=_outcome_rows())
    unpriced = [dict(_evidence_rows()[0], mid=None, bid=None, ask=None)]
    built = ds.from_managed_rows(candidate_rows=_candidate_rows(),
                                 evidence_rows=[unpriced[0], _evidence_rows()[1]],
                                 outcome_rows=_outcome_rows())
    assert built.manifest.observation_rows == 0
    assert "dataset_decision_price_missing:0xabc" in built.notes


def test_managed_rows_unknown_outcome_is_a_gap_not_a_value():
    unknown = [dict(row, outcome="UNKNOWN") for row in _outcome_rows()]
    built = ds.from_managed_rows(candidate_rows=_candidate_rows(),
                                 evidence_rows=_evidence_rows(), outcome_rows=unknown)
    assert built.outcomes == () and built.manifest.outcome_rows == 0


def test_managed_rows_future_close_row_injection():
    # A later CLOSE evidence row observed after the effective outcome is a
    # future leak against the managed timeline and must be rejected.
    future = {"candidate_id": 7, "evidence_id": 99, "evidence_role": "CLOSE",
              "observed_at": datetime(2026, 8, 9, 18, tzinfo=UTC),
              "model_probability": None, "mid": "0.9", "bid": None, "ask": None,
              "last": "0.9", "snapshot_sha256": None, "source_kind": "gamma"}
    with pytest.raises(ValueError, match="dataset_time_inversion:close_after_outcome"):
        ds.from_managed_rows(candidate_rows=_candidate_rows(),
                             evidence_rows=_evidence_rows(extra=[future]),
                             outcome_rows=_outcome_rows())


# ---------------------------------------------------------------------------
# Constructor 3: frozen research files (regression anchors only)
# ---------------------------------------------------------------------------

NEW_SOCCER_HEADER = ("league,id,day,phase,forecast_as_of,kickoff,home,away,"
                     "hg_90,ag_90,market,result\n")


def _new_soccer_content(rows):
    lines = [NEW_SOCCER_HEADER.rstrip("\n")]
    for row in rows:
        lines.append(",".join(str(cell) for cell in row))
    return ("\n".join(lines) + "\n").encode()


NS_ROWS = [
    ("epl", "m1", "2026-01-28", "Group", "2026-01-27T23:59:00+01:00",
     "2026-01-28T12:30:00+01:00", "Tampines", "Buriram", 1, 4, "1x2", "away"),
    ("epl", "m1", "2026-01-28", "Group", "2026-01-27T23:59:00+01:00",
     "2026-01-28T12:30:00+01:00", "Tampines", "Buriram", 1, 4, "ou2.5", "over"),
]


def test_frozen_new_soccer_contract_and_rejections():
    content = _new_soccer_content(NS_ROWS)
    built = ds.from_frozen_research(content=content, dataset_id=ds.FROZEN_NEW_SOCCER,
                                    expected_sha256=hashlib.sha256(content).hexdigest())
    assert built.manifest.origin == ds.ORIGIN_FROZEN
    assert built.manifest.anchor_rows == 2
    assert built.anchor_rows[0]["kickoff"] == "2026-01-28T11:30:00Z"
    assert built.anchor_rows[0]["forecast_as_of"] == "2026-01-27T22:59:00Z"
    with pytest.raises(ValueError, match="dataset_frozen_digest_mismatch"):
        ds.from_frozen_research(content=content, dataset_id=ds.FROZEN_NEW_SOCCER,
                                expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="dataset_frozen_id_unknown"):
        ds.from_frozen_research(content=content, dataset_id="wp-p4/whatever")
    clairvoyant = [("epl", "m2", "2026-02-01", "Group", "2026-02-01T12:30:00+01:00",
                    "2026-02-01T12:30:00+01:00", "X", "Y", 0, 0, "1x2", "home")]
    with pytest.raises(ValueError, match="dataset_time_inversion:forecast_not_before_kickoff"):
        ds.from_frozen_research(content=_new_soccer_content(clairvoyant),
                                dataset_id=ds.FROZEN_NEW_SOCCER)
    duplicate = NS_ROWS + [NS_ROWS[0]]
    with pytest.raises(ValueError, match="dataset_duplicate_match"):
        ds.from_frozen_research(content=_new_soccer_content(duplicate),
                                dataset_id=ds.FROZEN_NEW_SOCCER)
    missing = [("epl", "m3", "2026-02-01", "Group", "2026-01-31T23:59:00+01:00",
                "2026-02-01T12:30:00+01:00", "X", "Y", 0, 0, "", "home")]
    with pytest.raises(ValueError, match="dataset_missing_field:market"):
        ds.from_frozen_research(content=_new_soccer_content(missing),
                                dataset_id=ds.FROZEN_NEW_SOCCER)


def test_frozen_new_soccer_future_row_injection():
    base = ds.from_frozen_research(content=_new_soccer_content(NS_ROWS),
                                   dataset_id=ds.FROZEN_NEW_SOCCER)
    later = NS_ROWS + [("epl", "m9", "2026-06-01", "Group", "2026-05-31T23:59:00+01:00",
                        "2026-06-01T12:30:00+01:00", "P", "Q", 2, 2, "1x2", "home")]
    grown = ds.from_frozen_research(content=_new_soccer_content(later),
                                    dataset_id=ds.FROZEN_NEW_SOCCER)
    assert grown.manifest.anchor_rows == 3
    assert grown.anchor_rows[:2] == base.anchor_rows  # Anchor rows are keyed and stable.
    assert base.content_sha256() != grown.content_sha256()


def test_frozen_nba_contract_and_duplicate_key():
    header = ("season,date_et,espn_event_id,tip_utc,home,away,split,market,line,"
              "outcome,source,p_baseline\n")
    rows = [("2024-25", "2024-11-06", "401704741", "2024-11-07T01:00:00+00:00",
             "NO", "CLE", "train", "ml", "0.0", "0", "all_game_ml", "0.064"),
            ("2024-25", "2024-11-06", "401704741", "2024-11-07T01:00:00+00:00",
             "NO", "CLE", "train", "ml", "0.0", "0", "PM-explicit-full-game-OT", "0.064")]
    content = (header + "\n".join(",".join(str(cell) for cell in row) for row in rows)
               + "\n").encode()
    built = ds.from_frozen_research(content=content, dataset_id=ds.FROZEN_NBA)
    assert built.manifest.anchor_rows == 2  # source dimension distinguishes rows
    duplicate = rows + [rows[0]]
    content_dup = (header + "\n".join(",".join(str(cell) for cell in row)
                                      for row in duplicate) + "\n").encode()
    with pytest.raises(ValueError, match="dataset_duplicate_match"):
        ds.from_frozen_research(content=content_dup, dataset_id=ds.FROZEN_NBA)


def test_frozen_research_files_in_repo_load_with_pinned_digests():
    soccer_bytes = (FROZEN_DIR / "new-soccer/holdout_predictions.csv").read_bytes()
    built = ds.from_frozen_research(content=soccer_bytes,
                                    dataset_id=ds.FROZEN_NEW_SOCCER,
                                    expected_sha256=NEW_SOCCER_PIN)
    assert built.manifest.anchor_rows == 6900
    assert built.manifest.first_kickoff == "2026-01-23T20:30:00Z"
    nba_bytes = (FROZEN_DIR / "nba/predictions.csv").read_bytes()
    built_nba = ds.from_frozen_research(content=nba_bytes, dataset_id=ds.FROZEN_NBA,
                                        expected_sha256=NBA_PIN)
    assert built_nba.manifest.anchor_rows == 3435
    assert built_nba.manifest.first_kickoff == "2024-11-07T01:00:00Z"


# ---------------------------------------------------------------------------
# Import discipline (P3-7): exactly one new project module over optimize
# ---------------------------------------------------------------------------

def test_dataset_import_adds_exactly_one_project_module():
    script = "\n".join([
        "import json, sys",
        "import polymarket_alpha_lab.papersim as base",
        "import polymarket_alpha_lab.papersim.optimize as optimize_pkg",
        "before = set(sys.modules)",
        "import polymarket_alpha_lab.papersim.optimize.dataset as dataset_mod",
        "added = sorted(m for m in set(sys.modules) - before"
        "               if m.startswith('polymarket_alpha_lab'))",
        "print(json.dumps({'added': added, 'psycopg': 'psycopg' in sys.modules,",
        "                  'contract': dataset_mod.DATASET_CONTRACT_VERSION}))",
    ])
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True,
        cwd=str(REPO), env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["added"] == ["polymarket_alpha_lab.papersim.optimize.dataset"]
    assert payload["psycopg"] is False
    assert payload["contract"] == ds.DATASET_CONTRACT_VERSION
