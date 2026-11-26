"""Exercise the fixed-date profile reader with public archived and synthetic bytes."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import football_legacy as legacy


ARCHIVE = Path(__file__).resolve().parents[1] / "legacy"
RUNNER = "window_runner.py"
ASSESSMENT = "window_assessment.py"


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    for name in (RUNNER, ASSESSMENT):
        (tmp_path / name).write_bytes((ARCHIVE / name).read_bytes())
    monkeypatch.setattr(legacy, "RUNNER_PATH", tmp_path / RUNNER)
    legacy.load_runner.cache_clear()
    legacy._pinned_contracts.cache_clear()
    yield tmp_path
    legacy.load_runner.cache_clear()
    legacy._pinned_contracts.cache_clear()


def test_archived_runner_and_assessment_keep_their_fixed_fixture(profiles):
    runner = legacy.load_runner()
    fixture = "5781804"
    stage = runner.get_stage(fixture, 90)
    assert stage.kickoff == datetime(2026, 11, 22, 13, 30, tzinfo=UTC)
    example = json.loads((ARCHIVE / "example-assessment.json").read_bytes())
    rendered = runner.render(fixture, 90, example, now=datetime(2026, 11, 22, 12, 2, tzinfo=UTC))
    assert (rendered["status"], rendered["reason_code"]) == ("paper", "qualified")
    assessment = legacy._window_module("_football_fixed_assessment", profiles / ASSESSMENT)
    assert assessment.FIXTURES[fixture][:2] == ("AFC Ajax vs. ADO Den Haag", "13:30")
    assert assessment.identity({"market_id": "example", "outcome": "Yes"}) == ("example", "Yes")


@pytest.mark.parametrize("filename", [RUNNER, ASSESSMENT])
@pytest.mark.parametrize("change", ["one_byte", "other_approved_profile"])
def test_changed_or_wrong_role_bytes_are_refused_before_execution(profiles, monkeypatch, filename, change):
    path = profiles / filename
    other = ASSESSMENT if filename == RUNNER else RUNNER
    original = path.read_bytes()
    # Changing a single harmless docstring character preserves length and valid syntax.
    if change == "one_byte":
        contents = original.replace(b" ", b"_", 1)
        assert len(contents) == len(original) and sum(a != b for a, b in zip(contents, original)) == 1
    else:
        contents = (profiles / other).read_bytes()
    path.write_bytes(contents)
    monkeypatch.setenv("PAL_LEGACY_RUNNER_SHA256", hashlib.sha256(contents).hexdigest())
    monkeypatch.setenv("PAL_LEGACY_ASSESSMENT_SHA256", hashlib.sha256(contents).hexdigest())

    def unexpected_compile(*args, **kwargs):
        pytest.fail("unverified profile reached the compiler")

    monkeypatch.setattr(legacy, "compile", unexpected_compile, raising=False)
    with pytest.raises(ImportError, match="fixed_window_profile_unavailable"):
        if filename == RUNNER:
            legacy.load_runner()
        else:
            legacy._pinned_contracts("5781804", 90, None)


def test_only_checked_bytes_are_compiled_if_the_path_changes(profiles, monkeypatch):
    path = profiles / RUNNER
    original = path.read_bytes()
    checked = []

    def replace_path_after_read(source):
        assert source == original
        checked.append(source)
        path.write_bytes(b"# unrelated, valid, inert Python source\n")
        return hashlib.sha256(source)

    monkeypatch.setattr(legacy, "sha256", replace_path_after_read, raising=False)
    assert legacy.load_runner().get_stage("5781804", 90).lead == 90
    assert checked == [original] and path.read_bytes() != original


def test_review_reports_unavailable_for_a_changed_runner(profiles):
    path = profiles / RUNNER
    path.write_bytes(path.read_bytes() + b"\n")
    now = datetime(2026, 11, 22, 12, 2, tzinfo=UTC)
    row = {"stage": "t90", "legacy_id": "5781804", "kickoff": datetime(2026, 11, 22, 13, 30, tzinfo=UTC)}
    assert legacy.review(row, now, wait=False) == legacy.unavailable()
