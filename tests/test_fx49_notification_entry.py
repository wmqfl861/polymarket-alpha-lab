"""Verify publication of the fixed caller bundle and its ordinary notification imports."""

from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ENTRY = Path(__file__).resolve().parents[1] / "docs/paper-sim/runtime/notification/entry.py"
SPEC = importlib.util.spec_from_file_location("registered_notification_entry", ENTRY)
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = tmp_path / "release"
    business = tmp_path / "working"
    repo.mkdir()
    business.mkdir()
    transport = repo / "weixin_delivery.py"
    transport.write_text('ORIGIN = "fixed-main"\n')
    (business / transport.name).write_text('raise AssertionError("unregistered transport selected")\n')
    (business / "fixture_feed.py").write_text('VALUE = "existing feed"\n')
    old_store = 'import weixin_delivery\nPROTOCOL = "legacy"\ndef site_only():\n    return "preserved"\n'
    old_daily = (
        'import fixture_feed, football_store, sys, weixin_delivery\nMODE = "legacy"\n'
        'def main():\n    return (weixin_delivery.ORIGIN, football_store.weixin_delivery.ORIGIN, MODE, '
        'football_store.PROTOCOL, football_store.site_only(), fixture_feed.VALUE, sys.argv[1:])\n'
    )
    patch = repo / "fixed-receipt.patch"
    pins = {}
    parts = []
    for name, text in (("football_store.py", old_store), ("football_daily.py", old_daily)):
        new = text.replace('"legacy"', '"fixed"')
        (business / name).write_text(text)
        parts.extend(difflib.unified_diff(text.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))
        pins[name] = (sha(text.encode()), sha(new.encode()))
    patch.write_text("".join(parts))

    def git(*args):
        return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    monkeypatch.setenv("PAL_WEIXIN_RELEASE_COMMIT", git("rev-parse", "HEAD").decode().strip())
    monkeypatch.setattr(entry, "ROOT", repo)
    monkeypatch.setattr(entry, "PATCH", patch)
    monkeypatch.setattr(entry, "TRANSPORT", transport)
    monkeypatch.setattr(entry, "CALLERS", pins)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    for name in ("weixin_delivery", "football_store", "football_daily", "fixture_feed"):
        monkeypatch.setitem(sys.modules, name, None)
        del sys.modules[name]
    return business, tmp_path / "notification.zip"


def test_stage_publishes_once_and_ordinary_worker_uses_verified_modules(case):
    business, bundle = case
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: entry.stage(business, bundle), range(2)))
    assert entry.run(business, bundle, ["worker", "3"]) == (
        "fixed-main", "fixed-main", "fixed", "fixed", "preserved", "existing feed", ["worker", "3"],
    )
    assert sys.modules["weixin_delivery"].__file__ == str(entry.TRANSPORT)
    assert b'"legacy"' in (business / "football_store.py").read_bytes()


@pytest.mark.parametrize("changed", ["business", "patch", "release"])
def test_changed_input_never_publishes(case, monkeypatch, changed):
    business, bundle = case
    if changed == "business":
        (business / "football_store.py").write_bytes(b"changed")
    elif changed == "patch":
        entry.PATCH.write_bytes(b"changed")
    else:
        monkeypatch.setenv("PAL_WEIXIN_RELEASE_COMMIT", "0" * 40)
    with pytest.raises(entry.EntryError):
        entry.stage(business, bundle)
    assert not bundle.exists()


@pytest.mark.parametrize("changed", ["module", "foreign_file", "transport", "installed_source"])
def test_run_refuses_drift_before_loading_business_code(case, changed):
    business, bundle = case
    entry.stage(business, bundle)
    if changed in {"module", "foreign_file"}:
        orig = entry.read_callers(bundle)
        if changed == "module":
            orig["football_store.py"] = b'raise AssertionError("unverified business code")\n'
        else:
            orig["unregistered.py"] = b"unknown"
        with zipfile.ZipFile(bundle, "w") as archive:
            for name, data in orig.items():
                archive.writestr(zipfile.ZipInfo(name), data)
    elif changed == "transport":
        entry.TRANSPORT.write_bytes(b'raise AssertionError("unverified transport")\n')
    else:
        (business / "football_daily.py").write_bytes(b"changed")
    with pytest.raises(entry.EntryError):
        entry.run(business, bundle, ["tick"])
    assert "weixin_delivery" not in sys.modules
    assert "football_store" not in sys.modules


def test_stage_preserves_a_conflicting_existing_bundle(case):
    business, bundle = case
    bundle.write_bytes(b"previous release")
    with pytest.raises(entry.EntryError, match="different_bundle_already_exists"):
        entry.stage(business, bundle)
    assert bundle.read_bytes() == b"previous release"
