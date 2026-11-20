"""MO-6 CLI tests: the optimize families/init/run/status wiring (T5).

The papersim CLI startup budget is pinned separately by
tests/test_init_lazy_imports.py (limit 20, measured baseline 19): the
optimize wiring adds ZERO startup imports — every heavy import lives
inside the command handlers.
"""

from __future__ import annotations

import json

import pytest

from polymarket_alpha_lab.papersim.cli import main


def test_optimize_families_lists_every_registered_family(capsys) -> None:
    assert main(["optimize", "families"]) == 0
    payload = json.loads(capsys.readouterr().out)
    families = payload["families"]
    ids = [family["family_id"] for family in families]
    assert ids == sorted(ids)
    # Seven registered families at MO-6 (league-bridge merged as MO-5e
    # with NO lineages: targets == ()).
    assert set(ids) == {
        "club-soccer-frozen", "club-soccer-recalib", "crypto-gbm",
        "intl-elo-goal-residuals", "intl-elo-poisson", "league-bridge",
        "nba-pace-efficiency",
    }
    bridge = next(f for f in families if f["family_id"] == "league-bridge")
    assert bridge["targets"] == []
    assert bridge["searchable"] is False
    recalib = next(f for f in families if f["family_id"] == "club-soccer-recalib")
    assert recalib["searchable"] is True
    assert len(recalib["param_space"]) == 2  # the v1 3x3 grid
    nba = next(f for f in families if f["family_id"] == "nba-pace-efficiency")
    assert nba["baseline_model_version"].startswith("wp-nba2+")
    frozen = next(f for f in families if f["family_id"] == "club-soccer-frozen")
    assert frozen["searchable"] is False
    assert frozen["baseline_model_version"] == "soccer-r23"


def test_optimize_families_leaves_startup_modules_untouched() -> None:
    # Focused duplicate of the pinned budget: importing the CLI (which
    # builds the optimize parsers) must not load the optimize runner.
    import subprocess
    import sys

    code = (
        "import polymarket_alpha_lab.papersim.cli, sys\n"
        "names = [n for n in sys.modules if n == 'polymarket_alpha_lab' "
        "or n.startswith('polymarket_alpha_lab.')]\n"
        "print(len(names)); "
        "print('optimize.runner' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code],
        capture_output=True, text=True, check=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert int(lines[0]) == 19
    assert lines[1] == "False"


def test_optimize_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["optimize"])
    assert excinfo.value.code == 2


def test_optimize_run_requires_a_connection_source() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", "whatever.json",
        ])
    assert excinfo.value.code == 2


def test_optimize_run_missing_input_fails_fast_without_connection(
    capsys, tmp_path
) -> None:
    # A malformed input document fails BEFORE any connection is opened
    # (the handler validates the input first).
    missing = tmp_path / "missing-input.json"
    assert (
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", str(missing),
            "--managed",
        ]) == 1
    )
    assert "FAIL" in capsys.readouterr().err


def test_optimize_run_rejects_unknown_connection_free_input_shapes(
    capsys, tmp_path
) -> None:
    document = tmp_path / "input.json"
    document.write_text("{}", encoding="utf-8")
    assert (
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", str(document),
            "--managed",
        ]) == 1
    )
    assert "papersim_optimize_inputs_empty" in capsys.readouterr().err


def test_optimize_help_names_the_four_subcommands(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["optimize", "--help"])
    assert excinfo.value.code == 0
    text = capsys.readouterr().out
    for token in ("families", "init", "run", "status"):
        assert token in text
