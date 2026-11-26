"""D1 frozen staging and preflight proofs.

Mirror files serve as read-only inputs. Unit tests cover verification and
temporary staging. Opt-in Linux tests use disposable managed PostgreSQL and
the ordinary CLI in separate processes, with fixtures at the capture
transport boundary. Every case has zero frozen script starts and zero D1 days.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim.d1compare import frozenrun

REPO_ROOT = Path(__file__).resolve().parents[1]
MIRROR_ROOT = REPO_ROOT / "docs" / "paper-sim" / "server-mirror"
UTC = datetime.timezone.utc

SOCCER_DESIGNATED = (
    "round43/raw/enum43e.py",
    "round43/raw/build_board43c.py",
    "round43/raw/scan43.py",
    "round43/raw/write_candidates43a.py",
    "round43/raw/write_candidates43e.py",
)
CRYPTO_DESIGNATED = ("round33/raw/scan_crypto_33.py",)


@dataclass(frozen=True)
class FrozenLibrary:
    commit: str
    tree: str
    epoch: int
    length: int
    digest: str
    module: str

    @property
    def pin(self) -> str:
        return self.commit[:8]


# The independently measured Git-export v2 values from the accepted plan.
FROZEN_LIBRARIES = {
    "soccer": FrozenLibrary(
        "1a2760bc411f1157647be6ec87a57cd368b2eb9a",
        "7ee14a247dced867cd2d902de3631065f714f65c",
        1794058265, 95_262_720,
        "f4e8dde6e5ec42c5dbb6bbffe632b680e921f5c2651393cdba2e7ddd79e03931",
        "polymarket_alpha_lab/papersim/gates.py",
    ),
    "crypto": FrozenLibrary(
        "195017196f90287128f27af9ae832ea42864e5dc",
        "d6ae3a86263373f80a03f68fd7e37c2bbf6bf34b",
        1793783030, 95_211_520,
        "cf14de7c221f10558dafc61aa8e6274dd9d6331294a354fe8c77b97b11f95d67",
        "polymarket_alpha_lab/papersim/crypto_model.py",
    ),
}


def git_bytes(repo: Path, *args: str, extra_env: dict[str, str] | None = None) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], env={**env, **(extra_env or {})},
        capture_output=True, check=True, timeout=120,
    ).stdout


def link_local_git_objects(repo: Path) -> None:
    git_bytes(repo, "init", "--quiet", "--template=", "--object-format=sha1")
    objects = git_bytes(
        REPO_ROOT, "rev-parse", "--path-format=absolute", "--git-path", "objects",
    )
    (repo / ".git/objects/info/alternates").write_bytes(objects)


def source_archive(repo: Path, spec: FrozenLibrary, *, umask: str | None = "0002",
                   extra_env: dict[str, str] | None = None) -> bytes:
    options = [] if umask is None else ["-c", f"tar.umask={umask}"]
    return git_bytes(
        repo, "-c", "core.attributesFile=/dev/null", "-c", "core.autocrlf=false",
        *options, "archive", "--format=tar", f"--mtime=@{spec.epoch}",
        f"{spec.commit}:src", extra_env=extra_env,
    )


@pytest.fixture(scope="module")
def clean_object_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("d1-git-objects")
    link_local_git_objects(repo)
    return repo


@pytest.fixture(scope="module")
def crypto_archive(clean_object_repo: Path) -> bytes:
    return source_archive(clean_object_repo, FROZEN_LIBRARIES["crypto"])


@pytest.fixture
def private_work_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "lease-scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def stage_work(private_work_root: Path):
    with frozenrun._private_work() as work:
        assert work.path.is_relative_to(private_work_root)
        yield work
    assert not work.path.exists()


def _crypto_manifest() -> dict:
    spec = FROZEN_LIBRARIES["crypto"]
    (mirror_path,) = CRYPTO_DESIGNATED
    digest, size = frozenrun.manifest_rows(MIRROR_ROOT / "MANIFEST.sha256")[mirror_path]
    return {
        "stage_version": 2,
        "legs": {"crypto": {
            "library": {
                "pin": spec.pin, "commit": spec.commit, "tree": spec.tree,
                "subpath": "src", "format": "tar", "export_version": 2, "tar_umask": "0002",
                "commit_epoch": spec.epoch, "archive_sha256": spec.digest,
                "archive_byte_length": spec.length,
            },
            "scripts": [{"mirror_path": mirror_path, "sha256": digest, "size_bytes": size}],
            "bindings": [
                {"hard_coded_path": f"{frozenrun.LIBRARY_HOME}/src",
                 "source_path": f"libraries/{spec.pin}/src", "kind": "dir"},
                {"hard_coded_path": frozenrun.SERVER_HOME,
                 "source_path": "sandbox/paper-sim", "kind": "dir"},
                {"hard_coded_path": f"{frozenrun.SERVER_HOME}/{mirror_path}",
                 "source_path": f"sandbox/paper-sim/{mirror_path}", "kind": "file"},
            ],
        }},
    }


@pytest.fixture
def offline_chain(crypto_archive: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = _crypto_manifest()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(manifest_bytes).hexdigest()

    def read_staged_archives(manifest_sha256, *, repo_root):
        assert manifest_sha256 == digest
        assert repo_root == REPO_ROOT
        return manifest, {"crypto": crypto_archive}

    monkeypatch.setattr(frozenrun, "read_staged_archives", read_staged_archives)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return {
        "mirror_root": MIRROR_ROOT, "staging_root": tmp_path / "caller-staging",
        "repo_root": REPO_ROOT, "manifest_sha256": digest,
    }


# ---------------------------------------------------------------------------
# designation table and allow-lists
# ---------------------------------------------------------------------------


def test_designation_table_matches_the_accepted_plan() -> None:
    staged_soccer = {item.mirror_path for item in frozenrun.designated_leg("soccer")}
    staged_crypto = {item.mirror_path for item in frozenrun.designated_leg("crypto")}
    assert staged_soccer == set(SOCCER_DESIGNATED)
    assert staged_crypto == set(CRYPTO_DESIGNATED)
    soccer_pins = {item.library_pin for item in frozenrun.designated_leg("soccer")}
    crypto_pins = {item.library_pin for item in frozenrun.designated_leg("crypto")}
    assert soccer_pins == {frozenrun.SOCCER_LIBRARY_PIN}   # 1a2760bc
    assert crypto_pins == {frozenrun.CRYPTO_LIBRARY_PIN}   # 19501719


def test_scan43_allow_list_pins_paths_and_window_not_league_scope() -> None:
    scan43 = frozenrun.DESIGNATION_BY_PATH["round43/raw/scan43.py"]
    assert scan43.overridable == frozenset({
        "HOME", "UNDERSTAT_LATEST", "UNDERSTAT_POINTER", "FOTMOB_CHAMP",
        "ERED", "KAPPA_PATH", "OUTDIR", "OUT", "EVENTS", "ESPN", "LO", "HI"})
    # league-scope tuples are FIXED read-only inventory (binding review
    # note 5): recorded, never overridable
    assert {"BIG5", "TOTALS_ONLY", "BOARD_ONLY"} <= scan43.read_only_inventory
    assert not ({"BIG5", "TOTALS_ONLY", "BOARD_ONLY"} & scan43.overridable)


def test_crypto_allow_list_is_out_and_libsrc_window_not_overridable() -> None:
    crypto = frozenrun.DESIGNATION_BY_PATH["round33/raw/scan_crypto_33.py"]
    assert crypto.overridable == frozenset({"OUT", "_LIB_SRC"})
    # no window constant exists in the code; any window override is rejected
    assert "LO" not in crypto.overridable and "HI" not in crypto.overridable


def test_undesignated_soccer_scan33_is_recorded_but_refused_for_staging(stage_work) -> None:
    soccer33 = frozenrun.DESIGNATION_BY_PATH["round33/raw/soccer_scan33.py"]
    assert soccer33.staged is False
    assert {"LO", "HI", "DISC_LO", "DISC_HI"} <= soccer33.overridable
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.stage_scripts(MIRROR_ROOT, stage_work, designations=(soccer33,))
    assert error.value.code == "frozen_script_not_designated"
    assert not list(stage_work.path.iterdir())


# ---------------------------------------------------------------------------
# manifest parsing and staging (real mirror bytes, temp staging dirs)
# ---------------------------------------------------------------------------


def test_manifest_rows_parses_and_refuses_malformed(tmp_path: Path) -> None:
    rows = frozenrun.manifest_rows(MIRROR_ROOT / "MANIFEST.sha256")
    digest, size = rows["round43/raw/scan43.py"]
    content = (MIRROR_ROOT / "round43/raw/scan43.py").read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest
    assert size == len(content)
    broken = tmp_path / "MANIFEST.sha256"
    broken.write_text("not-a-row\n", encoding="utf-8")
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.manifest_rows(broken)
    assert error.value.code == "frozen_manifest_row_invalid"


def test_stage_scripts_verifies_every_file_against_manifest(stage_work) -> None:
    staged = frozenrun.stage_scripts(MIRROR_ROOT, stage_work)
    assert {item.designation.mirror_path for item in staged} == \
        set(SOCCER_DESIGNATED) | set(CRYPTO_DESIGNATED)
    rows = frozenrun.manifest_rows(MIRROR_ROOT / "MANIFEST.sha256")
    for item in staged:
        digest, size = rows[item.designation.mirror_path]
        assert item.sha256 == digest and item.size_bytes == size
        copied = (stage_work.path / item.staged_path).read_bytes()
        assert hashlib.sha256(copied).hexdigest() == digest
        # staging copies land inside the sandbox paper-sim layout
        assert item.staged_path.startswith("sandbox/paper-sim/")


def test_stage_scripts_refuses_tampered_mirror_bytes(tmp_path: Path, stage_work) -> None:
    fixture_mirror = tmp_path / "mirror"
    fixture_mirror.mkdir()
    for name in SOCCER_DESIGNATED + CRYPTO_DESIGNATED:
        target = fixture_mirror / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MIRROR_ROOT / name, target)
    tampered = fixture_mirror / "round43/raw/scan43.py"
    tampered.write_bytes(tampered.read_bytes() + b"\n# tampered\n")
    shutil.copy2(MIRROR_ROOT / "MANIFEST.sha256", fixture_mirror / "MANIFEST.sha256")
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.stage_scripts(fixture_mirror, stage_work)
    assert error.value.code == "frozen_stage_manifest_mismatch"
    assert not list(stage_work.path.iterdir())


def test_stage_scripts_refuses_unknown_designation(stage_work) -> None:
    fake = frozenrun.ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/enum43a.py",
        library_pin=frozenrun.SOCCER_LIBRARY_PIN,
        overridable=frozenset({"HOME"}), read_only_inventory=frozenset())
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.stage_scripts(MIRROR_ROOT, stage_work, designations=(fake,))
    assert error.value.code == "frozen_designation_unknown"
    assert not list(stage_work.path.iterdir())


def test_stage_scripts_requires_an_active_lease(tmp_path: Path, private_work_root: Path) -> None:
    staging = tmp_path / "call-00000000"
    staging.mkdir()
    with frozenrun._private_work() as work:
        assert work.path.is_relative_to(private_work_root)
        for path in (staging, work.path):
            with pytest.raises(frozenrun.FrozenRunError) as error:
                frozenrun.stage_scripts(MIRROR_ROOT, path)
            assert error.value.code == "frozen_work_lease_required"
            assert not list(path.iterdir())
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.stage_scripts(MIRROR_ROOT, work)
    assert error.value.code == "frozen_work_lease_expired"
    assert not work.path.exists()
    assert not list(staging.iterdir())


# ---------------------------------------------------------------------------
# the recorded shim: override allow-list enforcement on real bytes
# ---------------------------------------------------------------------------


def test_apply_overrides_rewrites_only_allow_listed_lines() -> None:
    designation = frozenrun.DESIGNATION_BY_PATH["round43/raw/scan43.py"]
    original = (MIRROR_ROOT / designation.mirror_path).read_text(encoding="utf-8")
    overrides = {
        "LO": "2026-11-20T18:00:00Z",
        "HI": "2026-11-21T01:14:00Z",
        "OUT": "/sandbox/paper-sim/round43/raw/euro_night_scan.json",
    }
    shimmed, changed = frozenrun.apply_overrides(original, overrides, designation)
    assert len(changed) == 3
    for line in shimmed.splitlines():
        if line.startswith("LO ="):
            assert line == 'LO = "2026-11-20T18:00:00Z"'
        if line.startswith("HI ="):
            assert line == 'HI = "2026-11-21T01:14:00Z"'
    original_lines = original.splitlines()
    shimmed_lines = shimmed.splitlines()
    changed_indexes = {int(note.split(":")[0][1:]) - 1 for note in changed}
    assert all(shimmed_lines[i] == original_lines[i] for i in
               range(len(original_lines)) if i not in changed_indexes)


def test_apply_overrides_rejects_everything_off_the_allow_list() -> None:
    designation = frozenrun.DESIGNATION_BY_PATH["round43/raw/scan43.py"]
    original = (MIRROR_ROOT / designation.mirror_path).read_text(encoding="utf-8")
    # league-scope tuple: read-only inventory, mechanically rejected
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.apply_overrides(original, {"BIG5": '("EPL",)'}, designation)
    assert error.value.code == "frozen_override_not_allowed"
    assert "BIG5" in error.value.detail
    # arbitrary names, frozen API origins, window names from OTHER scripts
    # and anything else off the allow-list: rejected
    for name in ("LEAGUE_TAGS", "BASE", "CLOB", "TOTALS_ONLY", "EVIL",
                 "DISC_LO", "DISC_HI"):
        with pytest.raises(frozenrun.FrozenRunError) as error:
            frozenrun.apply_overrides(original, {name: "x"}, designation)
        assert error.value.code == "frozen_override_not_allowed"
    # an allow-listed name with no assignment line in the source is
    # refused, never guessed (synthetic source: FOTMOB is overridable
    # for soccer_scan33, not for scan43)
    synthetic = "HOME = '/original'\nprint('frozen')\n"
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.apply_overrides(synthetic, {"OUT": "/x"}, designation)
    assert error.value.code == "frozen_override_assignment_missing"


def test_apply_overrides_crypto_window_has_no_code_constant() -> None:
    designation = frozenrun.DESIGNATION_BY_PATH["round33/raw/scan_crypto_33.py"]
    original = (MIRROR_ROOT / designation.mirror_path).read_text(encoding="utf-8")
    shimmed, changed = frozenrun.apply_overrides(
        original, {"OUT": "/sandbox/round33/raw/crypto_scan.json"}, designation)
    assert len(changed) == 1
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.apply_overrides(original, {"LO": "2026-11-20T00:00:00Z"},
                                  designation)
    assert error.value.code == "frozen_override_not_allowed"


# ---------------------------------------------------------------------------
# pinned library materialization (local git object store only)
# ---------------------------------------------------------------------------


def test_materialize_library_archives_pin_from_local_objects(
    tmp_path: Path, clean_object_repo: Path, crypto_archive: bytes,
) -> None:
    digests = set()
    for leg, spec in FROZEN_LIBRARIES.items():
        archive = crypto_archive if leg == "crypto" else source_archive(clean_object_repo, spec)
        assert len(archive) == spec.length
        digest = hashlib.sha256(archive).hexdigest()
        assert digest == spec.digest
        digests.add(digest)
        frozenrun.validate_library_archive(
            leg, archive, repo_root=clean_object_repo, staging_root=tmp_path,
        )
        expected = git_bytes(clean_object_repo, "cat-file", "blob", f"{spec.commit}:src/{spec.module}")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as opened:
            member = opened.extractfile(spec.module)
            assert member is not None and member.read() == expected
    assert len(digests) == 2
    assert not list(tmp_path.iterdir())


def test_materialize_library_refuses_bad_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        (designation,) = frozenrun.designated_leg("crypto")
        for invalid_pin in ("no-such-pin", "deadbeef"):
            patch.setattr(frozenrun, "designated_leg", lambda leg: (
                replace(designation, library_pin=invalid_pin),
            ))
            with pytest.raises(frozenrun.FrozenRunError) as error:
                frozenrun.validate_library_archive(
                    "crypto", b"", repo_root=REPO_ROOT, staging_root=tmp_path,
                )
            assert error.value.code == "frozen_library_pin_invalid"
    repo = tmp_path / "repository-without-pinned-objects"
    repo.mkdir()
    git_bytes(repo, "init", "--quiet", "--template=")
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.validate_library_archive("crypto", b"", repo_root=repo, staging_root=tmp_path)
    assert error.value.code == "frozen_library_archive_failed"


def test_plan_chain_refuses_mixed_pins_before_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    soccer = frozenrun.designated_leg("soccer")
    mixed = (*soccer[:-1], replace(soccer[-1], library_pin=frozenrun.CRYPTO_LIBRARY_PIN))
    monkeypatch.setattr(frozenrun, "designated_leg", lambda leg: mixed)

    def unexpected_readback(*args, **kwargs):
        pytest.fail("mixed pins reached managed readback")

    monkeypatch.setattr(frozenrun, "read_staged_archives", unexpected_readback)
    with pytest.raises(frozenrun.FrozenRunError) as error:
        with frozenrun.plan_chain("soccer", mirror_root=MIRROR_ROOT, staging_root=tmp_path,
                                  repo_root=REPO_ROOT, manifest_sha256="0" * 64):
            pytest.fail("mixed pins produced an execution plan")
    assert error.value.code == "frozen_pin_mixed_within_leg"


@pytest.mark.parametrize(("damage", "code"), [
    ("duplicate", "frozen_library_archive_member_duplicate"),
    ("extra", "frozen_library_archive_member_unexpected"),
    ("type", "frozen_library_archive_type_mismatch"),
    ("blob", "frozen_library_archive_content_mismatch"),
    ("trailer", "frozen_library_archive_trailer_invalid"),
])
def test_library_validator_rejects_tree_and_byte_mismatches(
    clean_object_repo: Path, crypto_archive: bytes, tmp_path: Path, damage: str, code: str,
) -> None:
    block = tarfile.BLOCKSIZE
    if damage == "duplicate":
        modified = crypto_archive[:block] + crypto_archive
    elif damage == "extra":
        modified = tarfile.TarInfo("fixture-extra.txt").tobuf() + crypto_archive
    elif damage == "type":
        first = tarfile.TarInfo.frombuf(crypto_archive[:block], "utf-8", "strict")
        assert first.isdir()
        first.type = tarfile.REGTYPE
        modified = first.tobuf() + crypto_archive[block:]
    elif damage == "blob":
        with tarfile.open(fileobj=io.BytesIO(crypto_archive), mode="r:") as opened:
            member = opened.getmember(FROZEN_LIBRARIES["crypto"].module)
        index = member.offset_data
        modified = crypto_archive[:index] + bytes([crypto_archive[index] ^ 1]) + crypto_archive[index + 1:]
    else:
        modified = crypto_archive[:-1] + b"X"
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.validate_library_archive("crypto", modified, repo_root=clean_object_repo, staging_root=tmp_path)
    assert error.value.code == code
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("member_type", [tarfile.DIRTYPE, tarfile.REGTYPE])
def test_legacy_library_validator_requires_one_mask_for_the_entire_tree(
    clean_object_repo: Path, crypto_archive: bytes, stage_work, member_type: bytes,
) -> None:
    with tarfile.open(fileobj=io.BytesIO(crypto_archive), mode="r:") as archive:
        member = next(item for item in archive.getmembers()[1:] if item.type == member_type)
    start = member.offset_data - tarfile.BLOCKSIZE
    member.mode ^= 0o020
    modified = crypto_archive[:start] + member.tobuf() + crypto_archive[member.offset_data:]
    source = frozenrun._LIBRARY_SOURCES["crypto"]
    git = frozenrun._isolated_git(clean_object_repo, source, stage_work, "git")
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun._validate_tar(modified, frozenrun._git_tree(source, git), git, tar_umask=None)
    assert error.value.code == "frozen_library_archive_mode_mismatch"
    assert member.name in error.value.detail


def _original_crypto_manifest(archive_digest: str, archive_size: int) -> dict:
    current = _crypto_manifest()["legs"]["crypto"]
    pin = FROZEN_LIBRARIES["crypto"].pin
    return {"stage_version": 1, "legs": {"crypto": {
        "leg": "crypto",
        "library": {"pin": pin, "archive_sha256": archive_digest, "archive_size_bytes": archive_size},
        "staged": [{**script, "path": f"sandbox/paper-sim/{script['mirror_path']}", "library_pin": pin}
                   for script in current["scripts"]],
    }}}


@pytest.mark.parametrize(("damage", "code"), [
    ("missing_anchor", "frozen_archive_legacy_evidence_missing"),
    ("wrong_anchor", "frozen_archive_original_digest_mismatch"),
    ("duplicate_key", "frozen_archive_manifest_schema_invalid"),
    ("duplicate_leg", "frozen_archive_manifest_schema_invalid"),
    ("current", "frozen_archive_original_manifest_schema_invalid"),
    ("missing_pin", "frozen_archive_legacy_evidence_missing"),
    ("wrong_pin", "frozen_archive_manifest_binding_invalid"),
    ("bool_size", "frozen_archive_manifest_binding_invalid"),
    ("unknown_leg", "frozen_archive_manifest_binding_invalid"),
    ("empty_legs", "frozen_archive_manifest_binding_invalid"),
    ("missing_source", "frozen_archive_legacy_evidence_missing"),
    ("extra_source", "frozen_archive_original_input_invalid"),
    ("duplicate_source", "frozen_archive_original_input_invalid"),
    ("both_sources", "frozen_archive_original_input_invalid"),
    ("mismatched_reference", "frozen_archive_original_digest_mismatch"),
    ("file_size", "frozen_archive_original_length_mismatch"),
    ("symlink_file", "frozen_archive_original_file_invalid"),
    ("symlink_manifest", "frozen_archive_original_file_invalid"),
])
def test_import_original_validates_anchor_schema_and_explicit_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str, code: str,
) -> None:
    archive = tmp_path / "archive=fixture.tar"
    archive.write_bytes(b"wrong size")
    document = _original_crypto_manifest("a" * 64, 1)
    library = document["legs"]["crypto"]["library"]
    if damage == "missing_pin":
        del library["pin"]
    elif damage == "wrong_pin":
        library["pin"] = FROZEN_LIBRARIES["soccer"].pin
    elif damage == "bool_size":
        library["archive_size_bytes"] = True
    elif damage == "unknown_leg":
        document["legs"]["other"] = {}
    elif damage == "empty_legs":
        document["legs"] = {}
    elif damage == "current":
        document = _crypto_manifest()
    content = json.dumps(document).encode("utf-8")
    if damage == "duplicate_key":
        content = b'{"stage_version": 1, ' + content[1:]
    elif damage == "duplicate_leg":
        content = content.replace(b'"legs": {', b'"legs": {"crypto": null, ')
    anchor = hashlib.sha256(content).hexdigest()
    if damage == "missing_anchor":
        anchor = ""
    elif damage == "wrong_anchor":
        anchor = "b" * 64
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(content)
    references = []
    archive_files = [f"crypto={archive}"]
    if damage == "missing_source":
        archive_files = []
    elif damage == "extra_source":
        archive_files.append(f"soccer={archive}")
    elif damage == "duplicate_source":
        archive_files *= 2
    elif damage == "both_sources":
        references = ["crypto=" + "a" * 64]
    elif damage == "mismatched_reference":
        archive_files = []
        references = ["crypto=" + "b" * 64]
    elif damage in ("symlink_file", "symlink_manifest"):
        link = tmp_path / "symlink"
        if damage == "symlink_file":
            link.symlink_to(archive)
            archive_files = [f"crypto={link}"]
        else:
            link.symlink_to(manifest)
            manifest = link

    def unexpected(*args, **kwargs):
        pytest.fail("invalid evidence reached Git or managed PostgreSQL")

    monkeypatch.setattr(frozenrun, "_isolated_git", unexpected)
    monkeypatch.setattr(frozenrun, "_managed_archive", unexpected)
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.import_original_archives(
            anchor, repo_root=tmp_path, mirror_root=MIRROR_ROOT, manifest_file=manifest,
            archive_files=archive_files, archive_sha256s=references,
        )
    assert error.value.code == code


@pytest.mark.parametrize(("damage", "code"), [
    ("dir_mode", "frozen_library_archive_mode_mismatch"),
    ("file_mode", "frozen_library_archive_mode_mismatch"),
    ("link", "frozen_library_archive_type_mismatch"),
    ("duplicate", "frozen_library_archive_member_duplicate"),
    ("missing", "frozen_library_archive_members_missing"),
    ("unexpected", "frozen_library_archive_member_unexpected"),
    ("effective_path", "frozen_library_archive_unsafe"),
    ("blob", "frozen_library_archive_content_mismatch"),
    ("trailer", "frozen_library_archive_trailer_invalid"),
    ("script", "frozen_archive_mirror_binding_mismatch"),
])
def test_import_original_refuses_anchored_invalid_archive(
    clean_object_repo: Path, crypto_archive: bytes, private_work_root: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, damage: str, code: str,
) -> None:
    spec = FROZEN_LIBRARIES["crypto"]
    archive_bytes = crypto_archive
    block = tarfile.BLOCKSIZE
    with tarfile.open(fileobj=io.BytesIO(crypto_archive), mode="r:") as archive:
        members = archive.getmembers()
    if damage == "duplicate":
        archive_bytes = crypto_archive[:block] + crypto_archive
    elif damage == "unexpected":
        archive_bytes = tarfile.TarInfo("unexpected-fixture.txt").tobuf() + crypto_archive
    elif damage == "effective_path":
        extension = tarfile.TarInfo("././@LongLink")
        extension.type = tarfile.GNUTYPE_LONGNAME
        alternate = b"fixture-alternate-directory/\0"
        extension.size = len(alternate)
        archive_bytes = extension.tobuf() + alternate.ljust(block, b"\0") + crypto_archive
    elif damage == "missing":
        first_file = next(member for member in members if member.isfile())
        end = first_file.offset_data + (first_file.size + block - 1) // block * block
        archive_bytes = crypto_archive[:first_file.offset] + crypto_archive[end:]
    elif damage in ("dir_mode", "file_mode", "link"):
        member_type = tarfile.DIRTYPE if damage == "dir_mode" else tarfile.REGTYPE
        member = next(item for item in members[1:] if item.type == member_type)
        if damage == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "fixture-target"
            member.size = 0
        else:
            member.mode ^= 0o020
        archive_bytes = (crypto_archive[:member.offset_data - block] + member.tobuf()
                         + crypto_archive[member.offset_data:])
    elif damage == "blob":
        member = next(item for item in members if item.isfile() and item.size and item.name != spec.module
                      and not item.name.endswith("/gates.py"))
        offset = member.offset_data
        archive_bytes = crypto_archive[:offset] + bytes([crypto_archive[offset] ^ 1]) + crypto_archive[offset + 1:]
    elif damage == "trailer":
        archive_bytes = crypto_archive[:-1] + b"x"
    source = tmp_path / "archive=fixture.tar"
    source.write_bytes(archive_bytes)
    document = _original_crypto_manifest(hashlib.sha256(archive_bytes).hexdigest(), len(archive_bytes))
    if damage == "script":
        document["legs"]["crypto"]["staged"][0]["sha256"] = "0" * 64
    content = json.dumps(document).encode("utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(content)

    def unexpected_storage(*args, **kwargs):
        pytest.fail("invalid evidence reached managed PostgreSQL")

    monkeypatch.setattr(frozenrun, "_managed_archive", unexpected_storage)
    try:
        with pytest.raises(frozenrun.FrozenRunError) as error:
            frozenrun.import_original_archives(
                hashlib.sha256(content).hexdigest(), repo_root=clean_object_repo, mirror_root=MIRROR_ROOT,
                manifest_file=manifest, archive_files=[f"crypto={source}"],
            )
    finally:
        source.unlink()
    assert error.value.code == code
    assert not list(private_work_root.glob("polymarket-d1-frozen-*/call-*"))


# ---------------------------------------------------------------------------
# sandbox presentation, env, egress, recording layer
# ---------------------------------------------------------------------------


def test_mount_namespace_plan_is_recorded_not_executed(offline_chain: dict) -> None:
    with frozenrun.plan_chain("crypto", **offline_chain) as plan:
        root = plan.staging_root
        argv = plan.mount_argv
        assert argv[:5] == ("unshare", "--user", "--map-root-user", "--mount",
                            "--propagation")
        assert argv[5:8] == ("private", "sh", "-c")
        script = argv[8]
        assert "mount --bind" in script
        assert frozenrun.LIBRARY_HOME + "/src" in script
        assert frozenrun.SERVER_HOME in script
        for binding in plan.bindings:
            assert Path(binding.staged_path).is_relative_to(root)
            assert Path(binding.staged_path).exists()
            assert binding.staged_path in script and binding.hard_coded_path in script
        assert str(root) not in json.dumps(plan.as_mapping())
    assert not root.exists()
    with pytest.raises(frozenrun.FrozenRunError) as error:
        plan.run_child([sys.executable, "-B", "-c", "pass"], timeout=5)
    assert error.value.code == "frozen_work_lease_expired"
    assert not list(offline_chain["staging_root"].rglob("*"))


def test_child_environment_pins_utc_and_hashseed_and_drops_everything_else() -> None:
    env = frozenrun.child_environment({
        "PATH": "/usr/bin", "LANG": "C.UTF-8", "DATABASE_URL": "postgres://x",
        "SUPABASE_KEY": "secret", "PGPASSWORD": "pw", "TZ": "America/New_York",
        "PYTHONHASHSEED": "random", "HOME": "/home/real", "UNRELATED": "1"})
    assert env["TZ"] == "UTC"
    assert env["PYTHONHASHSEED"] == "0"
    assert env["HOME"] == frozenrun.SERVER_HOME
    assert set(env) == set(frozenrun.CHILD_ENV_ALLOWLIST)
    assert "DATABASE_URL" not in env and "PGPASSWORD" not in env


def test_egress_hosts_are_derived_from_the_designated_scripts() -> None:
    assert frozenrun.egress_hosts("soccer") == frozenset({
        "gamma-api.polymarket.com", "clob.polymarket.com",
        "site.api.espn.com"})
    assert frozenrun.egress_hosts("crypto") == frozenset({
        "gamma-api.polymarket.com", "clob.polymarket.com",
        "api.binance.com", "stooq.com"})
    with pytest.raises(frozenrun.FrozenRunError):
        frozenrun.egress_hosts("other")


def test_recording_urllib_records_and_refuses_offlist_hosts() -> None:
    stamp = datetime.datetime(2026, 11, 20, 0, 5, tzinfo=UTC)
    served = {"https://clob.polymarket.com/book?token_id=1":
              b'{"bids":[],"asks":[]}'}

    def fake_fetch(request, timeout=None):
        class _Response:
            def read(self):
                return served[request.full_url]

        return _Response()

    recorder = frozenrun.RecordingUrllib(
        frozenrun.egress_hosts("crypto"), fetch=fake_fetch,
        clock=lambda: stamp)
    request = type("R", (), {"full_url": "https://clob.polymarket.com/book?token_id=1",
                             "headers": {"User-Agent": "paper-sim r33",
                                         "Authorization": "Bearer x"}})()
    stream = recorder.urlopen(request, timeout=25)
    assert stream.read() == b'{"bids":[],"asks":[]}'
    entry = recorder.entries[0]
    assert entry.utc_fetched_at == stamp
    assert entry.content_sha256 == hashlib.sha256(b'{"bids":[],"asks":[]}').hexdigest()
    assert entry.request_meta["headers"] == {"User-Agent": "paper-sim r33"}
    assert entry.request_meta["host"] == "clob.polymarket.com"
    assert entry.request_meta["query"] == {"token_id": "1"}
    bad = type("R", (), {"full_url": "https://evil.example.net/x", "headers": {}})()
    with pytest.raises(frozenrun.FrozenRunError) as error:
        recorder.urlopen(bad)
    assert error.value.code == "frozen_run_egress_host_refused"


# ---------------------------------------------------------------------------
# plan assembly and run gates
# ---------------------------------------------------------------------------


def test_plan_chain_assembles_offline_plan_for_crypto_leg(offline_chain: dict) -> None:
    with frozenrun.plan_chain("crypto", **offline_chain) as plan:
        root = plan.staging_root
        assert plan.leg == "crypto"
        assert len(plan.staged) == 1
        assert plan.library.pin == frozenrun.CRYPTO_LIBRARY_PIN
        assert plan.egress_hosts == frozenrun.egress_hosts("crypto")
        assert plan.child_env["TZ"] == "UTC"
        document = plan.as_mapping()
        assert document["scripts"][0]["mirror_path"] == "round33/raw/scan_crypto_33.py"
        assert document["library"]["archive_sha256"] == plan.library.archive_sha256
        assert document["library"]["archive_byte_length"] == plan.library.archive_size_bytes
        assert document["manifest_sha256"] == offline_chain["manifest_sha256"]
        assert "mount_argv" not in document and "tree_path" not in document["library"]
        library = root / plan.library.tree_path
        assert (library / FROZEN_LIBRARIES["crypto"].module).is_file()
    assert not root.exists()


def test_run_gates_refuse_without_recorded_authorization() -> None:
    assert frozenrun.check_run_gates(None) == frozenrun.REQUIRED_RUN_GATES
    partial = frozenrun.DayRunAuthorization(b2_b3_b4_merged_commit="abc")
    unmet = frozenrun.check_run_gates(partial)
    assert "b2_b3_b4_merged_commit" not in unmet
    assert "g7_fix_evidence" in unmet and "s1_scheduler_install_record" in unmet
    full = frozenrun.DayRunAuthorization(
        b2_b3_b4_merged_commit="abc", g7_fix_evidence="evidence",
        d1_lane_reviewed_merge_commit="def", s1_scheduler_install_record="rec",
        execution_base_commit="fa01be0b")
    assert frozenrun.check_run_gates(full) == ()
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.DayRunAuthorization.from_mapping({"extra": "x"})
    assert error.value.code == "frozen_authorization_field_unknown"


@pytest.mark.parametrize(("gate", "value"), [
    ("b2_b3_b4_merged_commit", None),
    ("g7_fix_evidence", False),
    ("d1_lane_reviewed_merge_commit", 42),
    ("s1_scheduler_install_record", ["offline fixture"]),
    ("execution_base_commit", {"reference": "offline fixture"}),
])
@pytest.mark.parametrize("command", ["run-frozen", "run-new"])
def test_run_cli_rejects_non_text_gate_evidence(
    tmp_path: Path, command: str, gate: str, value: object,
) -> None:
    document: dict[str, object] = dict.fromkeys(frozenrun.REQUIRED_RUN_GATES, "offline fixture")
    document[gate] = value
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(document), encoding="utf-8")
    staging = tmp_path / "staging"
    if command == "run-frozen":
        options = ["--leg", "crypto", "--staging-root", str(staging)]
    else:
        options = ["--scan-config", str(tmp_path / "missing-config.json"),
                   "--prepare-from", "0" * 64, "--operation-key", "offline-example"]
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools/d1_compare.py"), command, *options,
         "--authorization", str(authorization)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert result.stderr == f"d1_compare: {command} refused: frozen_authorization_field_invalid: {gate}\n"
    assert result.stdout == ""
    assert not staging.exists()


def test_authorization_preserves_text_and_leaves_missing_facts_unmet() -> None:
    record = "  archived receipt reference  "
    authorization = frozenrun.DayRunAuthorization.from_mapping({
        "g7_fix_evidence": record, "s1_scheduler_install_record": " \t",
    })
    assert authorization.g7_fix_evidence == record
    assert frozenrun.check_run_gates(authorization) == tuple(
        name for name in frozenrun.REQUIRED_RUN_GATES if name != "g7_fix_evidence"
    )
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.DayRunAuthorization(g7_fix_evidence=None)
    assert error.value.code == "frozen_authorization_field_invalid"
    assert error.value.detail == "g7_fix_evidence"


def test_run_frozen_cli_accepts_text_records_up_to_the_existing_executor_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from tools import d1_compare

    authorization = tmp_path / "authorization.json"
    records = {name: f"offline receipt for {name}" for name in frozenrun.REQUIRED_RUN_GATES}
    authorization.write_text(json.dumps(records), encoding="utf-8")
    staging = tmp_path / "staging"
    assert d1_compare.main(["run-frozen", "--leg", "soccer", "--staging-root", str(staging),
                            "--authorization", str(authorization)]) == 2
    output = capsys.readouterr()
    assert "frozen_run_executor_not_wired" in output.err
    assert output.out == ""
    assert not staging.exists()


def test_execute_designated_chain_refuses_without_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_readback(*args, **kwargs):
        pytest.fail("a refused run reached managed readback")

    monkeypatch.setattr(frozenrun, "read_staged_archives", unexpected_readback)
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.execute_designated_chain(
            "crypto", mirror_root=MIRROR_ROOT, staging_root=tmp_path,
            repo_root=REPO_ROOT, authorization=None)
    assert error.value.code == "frozen_run_gates_unmet"
    assert "g7_fix_evidence" in error.value.detail
    full = frozenrun.DayRunAuthorization(
        b2_b3_b4_merged_commit="a", g7_fix_evidence="b",
        d1_lane_reviewed_merge_commit="c", s1_scheduler_install_record="d",
        execution_base_commit="e")
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.execute_designated_chain(
            "crypto", mirror_root=MIRROR_ROOT, staging_root=tmp_path,
            repo_root=REPO_ROOT, authorization=full)
    # even with gates recorded, this pass wires no executor
    assert error.value.code == "frozen_run_executor_not_wired"
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Frozen-input preflight through the ordinary CLI and an isolated PostgreSQL
# ---------------------------------------------------------------------------


_PREFLIGHT_COMMAND = "run-frozen-preflight"
_NATIVE_PREFLIGHT = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES") != "1",
    reason="explicit Linux native PostgreSQL proof is opt-in",
)
_CAPTURE_PROCESS = """\
from tests.test_papersim_d1compare_journal import _start_fixture_capture_process
_start_fixture_capture_process(with_failed_page=WITH_FAILED_PAGE)
"""
_PREFLIGHT_NUMERIC_CAPTURE = """\
from tests.test_papersim_d1compare_frozenrun import _start_numeric_capture_process
_start_numeric_capture_process()
"""
_PREFLIGHT_REPEATABLE_READ = """\
from contextlib import contextmanager
import runpy, sys
from polymarket_alpha_lab.papersim.d1compare import frozenexec
original_managed = frozenexec.frozenrun._managed_archive
original_event = frozenexec._event
pause = sys.argv.pop(1) == "hold"
@contextmanager
def managed(root, operation, **options):
    with original_managed(root, operation, **options) as connection:
        if operation == "preflight":
            with connection.transaction():
                connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield connection
def event(connection, *args, **kwargs):
    if pause and args[3] == "STARTED":
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("fixture:preflight:pause",))
    return original_event(connection, *args, **kwargs)
frozenexec.frozenrun._managed_archive = managed
frozenexec._event = event
cli = sys.argv.pop(1)
runpy.run_path(cli, run_name="__main__")
"""
_PREFLIGHT_INSTANCE = """\
import os
from pathlib import Path
import runpy, sys
from polymarket_alpha_lab.papersim import store_psycopg
original = store_psycopg.connect_managed
declared, selected, replacement = (Path(sys.argv.pop(1)) for _ in range(3))
mode = sys.argv.pop(1)
opened = False
descriptor = selected / ".local/postgres/instance.json"
stored = descriptor.read_bytes() if mode == "descriptor" else None
def write_descriptor(content):
    update = descriptor.with_name("test-instance-change")
    with os.fdopen(os.open(update, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(content)
    update.replace(descriptor)
def connect(root, **kwargs):
    global opened
    assert not opened and root == selected
    connection = original(root, **kwargs)
    opened = True
    if mode == "descriptor":
        write_descriptor((replacement / ".local/postgres/instance.json").read_bytes())
    else:
        update = declared.with_name("test-project-root-change")
        update.symlink_to(replacement, target_is_directory=True)
        update.replace(declared)
    return connection
store_psycopg.connect_managed = connect
cli = sys.argv.pop(1)
try:
    runpy.run_path(cli, run_name="__main__")
finally:
    if stored is not None and opened:
        write_descriptor(stored)
"""
_INSPECT_PREFLIGHT = """\
import hashlib, json, sys
from pathlib import Path
from polymarket_alpha_lab.papersim import store_psycopg
root, operation = Path(sys.argv[1]), sys.argv[2]
with store_psycopg.connect_managed(root) as connection:
    events = connection.execute(
        "SELECT attempt, phase, command, queue_id, scheduled_for, config_digest, "
        "input_evidence, output_evidence, error_code, scan_id "
        "FROM papersim.command_run_events WHERE operation_key=%s ORDER BY run_event_id",
        (operation,),
    ).fetchall()
    documents = {}
    for row in events:
        for digest in row[7]:
            kind, saved = connection.execute(
                "SELECT kind, content FROM papersim.snapshots WHERE content_sha256=%s", (digest,),
            ).fetchone()
            assert hashlib.sha256(saved).hexdigest() == digest
            record = json.loads(saved)
            documents[kind] = record
            for observed in record.get("requests", []):
                raw_sha = observed["content_sha256"]
                if raw_sha is None:
                    assert observed["byte_length"] is None
                    continue
                raw, size = connection.execute(
                    "SELECT content,byte_length FROM papersim.snapshots WHERE content_sha256=%s", (raw_sha,),
                ).fetchone()
                assert len(raw) == size == observed["byte_length"]
                assert hashlib.sha256(raw).hexdigest() == raw_sha
    report = {"events": events, "documents": documents}
    all_preflight = connection.execute(
        "SELECT content_sha256,kind FROM papersim.snapshots "
        "WHERE kind IN ('d1_frozen_preflight_denominator','d1_frozen_preflight_receipt') "
        "OR source LIKE 'd1:frozen-preflight:%'"
    ).fetchall()
    referenced = {
        digest for row in connection.execute(
            "SELECT output_evidence FROM papersim.command_run_events WHERE command='run-frozen-preflight'"
        ).fetchall() for digest in row[0]
    }
    report["snapshot_totals"] = {
        kind: sum(value == kind for _, value in all_preflight) for _, kind in all_preflight
    }
    report["snapshot_sha256_by_kind"] = {
        kind: sorted(digest for digest, value in all_preflight if value == kind) for _, kind in all_preflight
    }
    report["orphan_preflight_snapshots"] = [digest for digest, _ in all_preflight if digest not in referenced]
    report["business"] = {
        name: connection.execute("SELECT count(*) FROM papersim." + name).fetchone()[0]
        for name in ("scan_runs", "candidates", "entries", "candidate_marks", "market_outcomes",
                     "settlements", "bankroll_events")
    }
    report["scheduled_non_capture_events"] = connection.execute(
        "SELECT count(*) FROM papersim.command_run_events "
        "WHERE command <> 'capture-inputs' AND scheduled_for IS NOT NULL"
    ).fetchone()[0]
print(json.dumps(report, default=str))
"""


_NUMERIC_GAMMA_INSERT = b'"line": 2.5, "handicap": -1.234567890123456789e-21, '
_NUMERIC_CLOB_KEY = b'"market": 0.5'


def _start_numeric_capture_process():
    """Capture exact numeric wire fields at the existing public transport seam."""
    import runpy
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from tests.test_papersim_d1compare_journal import _CONDITION, _GAMMA_PAGE, _GammaPagesFixture, _book_bytes

    gamma = _GAMMA_PAGE.replace(b'"endDate":', _NUMERIC_GAMMA_INSERT + b'"endDate":')
    fixture = _GammaPagesFixture({"soccer": (gamma, gamma), "empty": ()})
    source = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
    valid = _book_bytes("222", source)
    wrong = _book_bytes("111", source).replace(f'"market": "{_CONDITION}"'.encode(), _NUMERIC_CLOB_KEY)
    fixture.books = {"111": wrong, "222": valid}
    polymarket.BoundedPublicOpener = lambda: fixture
    cli = REPO_ROOT / "tools/d1_compare.py"
    sys.argv[0] = str(cli)
    runpy.run_path(str(cli), run_name="__main__")


def _preflight_fault_script(phase, *, after_commit):
    when = "after" if after_commit else "before"
    return f"""\
import os, runpy, sys
from polymarket_alpha_lab.papersim.d1compare import frozenexec
original = frozenexec._event
def deny_child(event, _arguments):
    if event in ("subprocess.Popen", "os.fork", "os.posix_spawn", "os.system"):
        raise AssertionError("preflight started a child")
sys.addaudithook(deny_child)
def event(connection, *args, **kwargs):
    match = args[3] == {phase!r}
    if match and {when!r} == "before":
        os._exit(83)
    result = original(connection, *args, **kwargs)
    if match and {when!r} == "after":
        pending.add(id(connection))
    return result
pending = set()
from contextlib import contextmanager
original_managed = frozenexec.frozenrun._managed_archive
class Connection:
    def __init__(self, connection):
        self.connection = connection
    def __getattr__(self, name):
        return getattr(self.connection, name)
    @contextmanager
    def transaction(self):
        with self.connection.transaction():
            yield
        if id(self) in pending:
            os._exit(84)
@contextmanager
def managed(root, operation, **options):
    with original_managed(root, operation, **options) as connection:
        yield Connection(connection) if operation == "preflight" else connection
frozenexec._event = event
frozenexec.frozenrun._managed_archive = managed
cli = sys.argv.pop(1)
runpy.run_path(cli, run_name="__main__")
"""


def test_preflight_gamma_readback_keeps_late_observation_and_captured_only_scope():
    from polymarket_alpha_lab.papersim.d1compare import frozenexec, journal
    from tests.test_papersim_d1compare_journal import _CONDITION, _GAMMA_PAGE

    cutoff = datetime.datetime(2026, 11, 20, tzinfo=UTC)
    received = cutoff + datetime.timedelta(seconds=1)
    request = {
        "sequence": 7, "status": "late", "source_time_status": "receipt_only",
        "source_observed_at": None, "request_meta": {"query": {"tag_slug": "nba"}},
    }
    entry = journal.capture("https://gamma-api.polymarket.com/events", received, {}, _GAMMA_PAGE)
    page, observations = frozenexec._gamma_observations(request, entry, cutoff)
    assert page["page_status"] == "partly_indexed"
    (market,) = observations
    assert market["request_sequence"] == 7 and market["condition_id"] == _CONDITION
    assert market["scope_class"] == "captured_only" and market["economic_direction"] is None
    assert market["page_sha256"] == hashlib.sha256(_GAMMA_PAGE).hexdigest()
    assert market["contract"]["description"] == "Overtime included."


@pytest.mark.parametrize(("mutation", "readback"), [
    ("asset_id", "book_identity_mismatch"), ("market", "book_identity_mismatch"),
    ("numeric_asset_id", "book_identity_mismatch"),
    ("timestamp", "book_source_clock_unproved"), ("price", "invalid_book_payload"),
])
def test_preflight_book_refuses_unbound_bytes_clock_or_level(mutation, readback):
    from polymarket_alpha_lab.papersim.d1compare import frozenexec, journal
    from tests.test_papersim_d1compare_journal import _CONDITION, STAMP, _book_bytes

    payload = json.loads(_book_bytes("111", STAMP))
    if mutation == "price":
        payload["asks"][0]["price"] = "1.01"
    elif mutation == "numeric_asset_id":
        payload["asset_id"] = 0.5
    else:
        payload[mutation] = {"asset_id": "222", "market": "0x" + "b" * 64,
                             "timestamp": str(int(STAMP.timestamp() * 1000) - 1)}[mutation]
    cutoff = STAMP + datetime.timedelta(seconds=2)
    received = STAMP + datetime.timedelta(seconds=1)
    entry = journal.capture("https://clob.polymarket.com/book?token_id=111",
                            received, {}, json.dumps(payload).encode())
    requests = {8: {"source_kind": "polymarket-clob", "request_meta": {"query": {"token_id": "111"}},
                    "utc_fetched_at": received.isoformat(), "source_observed_at": STAMP.isoformat(),
                    "content_sha256": entry.content_sha256, "status": "received"}}
    target = {"request_sequence": 8, "token_id": "111", "condition_id": _CONDITION, "reason_code": None}
    result = frozenexec._book_target(target, requests, {8: entry}, cutoff)
    assert result["readback_status"] == readback
    assert result["usable_for_decision"] is None
    if mutation == "numeric_asset_id":
        assert json.loads(frozenrun._canonical_json(result))["raw_asset_id"] == {
            "json_type": "number", "decimal": "0.5",
        }


def test_preflight_scalar_projection_preserves_exact_numeric_semantics():
    from decimal import Decimal
    from polymarket_alpha_lab.papersim.d1compare import frozenexec, journal
    from tests.test_papersim_d1compare_journal import _GAMMA_PAGE

    raw = _GAMMA_PAGE.replace(b'"endDate":', _NUMERIC_GAMMA_INSERT + b'"endDate":')
    raw = raw.replace(b'"description": "Overtime included."', b'"description": {"invalid":[0.5]}')
    cutoff = datetime.datetime(2026, 11, 20, tzinfo=UTC)
    request = {
        "sequence": 1, "status": "received", "source_time_status": "receipt_only",
        "source_observed_at": None, "request_meta": {"query": {"tag_slug": "soccer"}},
    }
    entry = journal.capture("https://gamma-api.polymarket.com/events", cutoff, {}, raw)
    _, (market,) = frozenexec._gamma_observations(request, entry, cutoff)
    projected = json.loads(frozenrun._canonical_json(market))["contract"]
    assert projected["line"] == {"json_type": "number", "decimal": "2.5"}
    assert Decimal(projected["handicap"]["decimal"]) == Decimal("-1.234567890123456789e-21")
    assert projected["description"] == {"json_type": "object"}
    assert market["page_sha256"] == hashlib.sha256(raw).hexdigest()
    assert frozenexec._source_scalar("2.5") == "2.5"


def test_preflight_recovery_refuses_a_same_operation_binding_conflict():
    from polymarket_alpha_lab.papersim import store
    from polymarket_alpha_lab.papersim.d1compare import frozenexec

    operation = "d1:frozen-preflight:v1:" + "a" * 64
    binding = {"manifest_sha256": "b" * 64, "batch_sha256": "c" * 64}

    class ConflictingEvent:
        def execute(self, _sql, args):
            assert args == (operation,)
            return self

        def fetchall(self):
            return [(store.command_run_event_key(operation, 1, "STARTED"), 1, "STARTED",
                     _PREFLIGHT_COMMAND, None, None, datetime.datetime.now(UTC), "a" * 64,
                     [binding["manifest_sha256"], "d" * 64], [], None, None, None)]

    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenexec._existing(ConflictingEvent(), operation, binding)
    assert error.value.code == "frozen_preflight_receipt_conflict"


@_NATIVE_PREFLIGHT
def test_managed_frozen_preflight_cli_receipts_replay_faults_and_zero_writes(tmp_path):
    from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import acquire
    from tests.test_papersim_d1compare_archive_native import (
        _cli, _isolated_project, _process_env, _receipt, _refused,
    )
    from tests.test_papersim_d1compare_journal import _CONDITION, _GAMMA_PAGE

    with _isolated_project(tmp_path) as project:
        staging = tmp_path / "caller-stage"
        stage = _receipt(_cli(project, "stage", staging, "--leg", "all"))
        assert set(stage["legs"]) == {"soccer", "crypto"}
        cli = str(REPO_ROOT / "tools/d1_compare.py")
        before = datetime.datetime.now(UTC)
        start, end = before - datetime.timedelta(minutes=1), before + datetime.timedelta(minutes=5)

        def process(source, *args):
            return subprocess.run(
                [sys.executable, "-B", "-c", source, *args], cwd=REPO_ROOT,
                env=_process_env(project), capture_output=True, timeout=90,
            )

        def capture(window_start, *, failed_page=False, numeric=False):
            source = _PREFLIGHT_NUMERIC_CAPTURE if numeric else _CAPTURE_PROCESS.replace(
                "WITH_FAILED_PAGE", repr(failed_page))
            result = process(
                source, "capture-inputs", "--repo-root", str(project.root),
                "--window-start", window_start.isoformat(), "--window-end", end.isoformat(),
                "--tag-slug", "soccer", "--tag-slug", "empty", "--allow-public-fetch",
            )
            assert result.returncode == 0 and not result.stderr, result.stderr
            return json.loads(result.stdout)["batch_sha256"]

        def options(leg, batch, manifest):
            return ("--preflight", "--leg", leg, "--manifest-sha256", manifest, "--batch-sha256", batch)

        def preflight(leg, batch, manifest=stage["manifest_sha256"]):
            return _cli(project, "run-frozen", staging, *options(leg, batch, manifest))

        def check_result(result, expected="incomplete"):
            assert result.returncode == 2 and not result.stderr, result.stderr
            outcome = json.loads(result.stdout)
            assert outcome["status"] == expected
            assert outcome["mode"] == "preflight" and outcome["qualifying"] is False
            assert outcome["child_started"] is False and outcome["day_credit"] == 0
            assert outcome["actor"] == "pal_app"
            assert outcome["receipt_sha256"] and outcome["attempt"] >= 1
            return outcome

        def inspect(summary):
            invocation = process(_INSPECT_PREFLIGHT, str(project.root), summary["operation_key"])
            assert invocation.returncode == 0 and not invocation.stderr, invocation.stderr
            return json.loads(invocation.stdout)

        batch = capture(start)
        def repeatable_read_preflight(mode):
            return process(
                _PREFLIGHT_REPEATABLE_READ, mode, cli, "run-frozen",
                "--repo-root", str(project.root), "--staging-root", str(staging),
                *options("soccer", batch, stage["manifest_sha256"]),
            )

        def wait_for_advisory_waiters(control, wanted):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                count = control.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"
                ).fetchone()[0]
                if count == wanted:
                    return
                time.sleep(0.05)
            pytest.fail(f"expected {wanted} pending native advisory lock acquisitions")

        # Ensure both processes reach the database wait before the winner may commit.
        pause_key = ("fixture:preflight:pause",)
        with ThreadPoolExecutor(max_workers=2) as workers, store_psycopg.connect_managed(project.root) as control:
            control.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", pause_key)
            try:
                winner = workers.submit(repeatable_read_preflight, "hold")
                wait_for_advisory_waiters(control, 1)
                follower = workers.submit(repeatable_read_preflight, "follow")
                wait_for_advisory_waiters(control, 2)
            finally:
                control.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", pause_key)
            results = [check_result(result.result()) for result in (winner, follower)]
        initial, retry = sorted(results, key=lambda result: result["reused"])
        assert retry == {**initial, "reused": True}
        assert initial["denominator_sha256"] and initial["denominator_revalidated"] is True
        assert initial["attempt"] == 1
        assert initial["readback"] == {"archive": "verified", "capture": "verified"}
        assert {"frozen_execution_capability_unproved", "registered_comparison_scope_missing",
                "frozen_gamma_pagination_limit_100", "registered_book_age_policy_missing"} <= set(initial["missing"])
        read = inspect(initial)
        assert read["orphan_preflight_snapshots"] == []
        assert all(count == 0 for count in read["business"].values())
        assert read["scheduled_non_capture_events"] == 0
        assert [(row[0], row[1], row[2]) for row in read["events"]] == [
            (1, phase, _PREFLIGHT_COMMAND) for phase in ("STARTED", "PREPARED", "FAILED")
        ]
        assert all(row[3] is row[4] is row[9] is None for row in read["events"])
        frozen = read["documents"]["d1_frozen_preflight_denominator"]
        assert frozen["stage"]["library"]["commit"] == FROZEN_LIBRARIES["soccer"].commit
        assert len(frozen["stage"]["scripts"]) == len(SOCCER_DESIGNATED)
        assert frozen["capture_event"]["command"] == "capture-inputs"
        assert frozen["capture_event"]["attempt"] == 1
        first, repeated = frozen["market_observations"]
        assert first["request_sequence"] != repeated["request_sequence"]
        assert first["page_sha256"] == repeated["page_sha256"] == hashlib.sha256(_GAMMA_PAGE).hexdigest()
        assert first["path"] == repeated["path"] == "/0/markets/0"
        assert first["condition_id"] == _CONDITION and first["event_id"] == "event-12"
        assert first["outcomes"] == ["No", "Yes"] and first["token_ids"] == ["111", "222"]
        assert first["outcomes_by_token"] == {"111": "no", "222": "yes"}
        assert set(first["book_request_sequences"]) == {"111", "222"}
        assert first["scope_class"] == first["final_adoption"] == "unknown"
        assert first["terms_sha256"]
        assert frozen["requests"][1]["utc_fetched_at"] < frozen["requests"][2]["utc_fetched_at"]
        assert {row["raw_asset_id"] for row in frozen["book_targets"]} == {"111", "222"}
        for book in frozen["book_targets"]:
            assert book["readback_status"] == "verified" and book["raw_condition_id"] == _CONDITION
            assert book["ladder_status"] == "one_side_empty"
            assert book["source_observed_at"] < book["utc_fetched_at"] <= frozen["decision_as_of"]
            assert book["freshness_policy_ref"] is book["usable_for_decision"] is None
        assert frozen["coverage"]["observed_market_lower_bound"] == 1
        assert frozen["coverage"]["unseen_frozen_market_count"] is None
        assert frozen["coverage"]["known_comparison_fields"] == 0
        raw_receipt = read["documents"]["d1_frozen_preflight_receipt"]
        assert raw_receipt["attempt"] == raw_receipt["binding"]["preflight_version"] == 1
        assert raw_receipt["terminal_event_key"] == initial["terminal_event_key"]

        missing_mirror = _cli(
            project, "run-frozen", staging, *options("soccer", batch, stage["manifest_sha256"]),
            "--mirror-root", str(tmp_path / "missing-mirror"),
        )
        _refused(missing_mirror, "frozen_preflight_cached_recheck_failed",
                 "frozen_preflight_local_input_unreadable", initial["receipt_sha256"])
        assert check_result(preflight("soccer", batch)) == retry
        assert inspect(initial) == read

        ordinary = _cli(project, "run-frozen", staging, "--leg", "soccer",
                        "--manifest-sha256", stage["manifest_sha256"])
        _refused(ordinary, "b2_b3_b4_merged_commit")
        mixed = _cli(project, "run-frozen", staging, *options("soccer", batch, stage["manifest_sha256"]),
                     "--authorization", str(tmp_path / "absent"))
        _refused(mixed, "preflight")
        unavailable = check_result(preflight("soccer", batch, "a" * 64), "failed")
        assert unavailable["denominator_sha256"] is None
        assert unavailable["denominator_revalidated"] is False
        assert unavailable["readback"]["archive"] == "unverified"

        # Both disconnect boundaries use ordinary CLI in a separate process.
        # The first exits inside the terminal transaction; the second drops
        # the reply after its actual terminal commit.
        def fault(leg, wanted_batch, *, after_commit):
            return process(
                _preflight_fault_script("FAILED", after_commit=after_commit), cli,
                "run-frozen", "--repo-root", str(project.root), "--staging-root", str(staging),
                *options(leg, wanted_batch, stage["manifest_sha256"]),
            )

        partial_batch = capture(start + datetime.timedelta(seconds=1), failed_page=True)
        previous = inspect(initial)
        before_break = previous["snapshot_totals"]
        broken = fault("crypto", partial_batch, after_commit=False)
        assert (broken.returncode, broken.stdout, broken.stderr) == (83, b"", b"")
        after_break = inspect(initial)
        assert after_break["orphan_preflight_snapshots"] == []
        assert after_break["snapshot_totals"] == {
            **before_break, "d1_frozen_preflight_denominator": before_break["d1_frozen_preflight_denominator"] + 1,
        }
        receipt_kind, denom_kind = "d1_frozen_preflight_receipt", "d1_frozen_preflight_denominator"
        assert after_break["snapshot_sha256_by_kind"][receipt_kind] == previous["snapshot_sha256_by_kind"][receipt_kind]
        assert set(previous["snapshot_sha256_by_kind"][denom_kind]) < set(after_break["snapshot_sha256_by_kind"][denom_kind])
        failed_recheck = check_result(_cli(
            project, "run-frozen", staging, *options("crypto", partial_batch, stage["manifest_sha256"]),
            "--mirror-root", str(tmp_path / "missing-mirror"),
        ), "failed")
        assert failed_recheck["denominator_sha256"] and failed_recheck["denominator_revalidated"] is False
        assert failed_recheck["attempt"] == 2
        recovered = check_result(preflight("crypto", partial_batch))
        assert recovered["attempt"] == 3 and recovered["reused"] is False
        assert recovered["denominator_sha256"] == failed_recheck["denominator_sha256"]
        recovered_rows = inspect(recovered)
        assert recovered_rows["orphan_preflight_snapshots"] == []
        assert [(row[0], row[1]) for row in recovered_rows["events"]] == [
            (1, "STARTED"), (1, "PREPARED"), (2, "STARTED"), (2, "FAILED"),
            (3, "STARTED"), (3, "PREPARED"), (3, "FAILED"),
        ]
        assert recovered_rows["events"][1][7] == recovered_rows["events"][5][7]
        gaps = recovered_rows["documents"]["d1_frozen_preflight_denominator"]
        assert gaps["coverage"]["incomplete_gamma_tags"] == ["soccer"]
        assert gaps["coverage"]["missing_or_unusable_gamma_sequences"]
        assert gaps["coverage"]["unseen_a1_market_count"] is None
        assert gaps["coverage"]["observed_market_lower_bound"] == 1
        assert recovered["operation_key"] != initial["operation_key"]

        lost = fault("crypto", batch, after_commit=True)
        assert (lost.returncode, lost.stdout, lost.stderr) == (84, b"", b"")
        after_loss = check_result(preflight("crypto", batch))
        assert after_loss["attempt"] == 1 and after_loss["reused"] is True
        assert len(inspect(after_loss)["events"]) == 3
        assert after_loss["operation_key"] not in {recovered["operation_key"], initial["operation_key"]}

        # A contradictory readiness entry cannot turn an A1 preflight into exit zero.
        rejected_ready_input = fault("soccer", partial_batch, after_commit=False)
        assert (rejected_ready_input.returncode, rejected_ready_input.stdout) == (83, b"")
        with store_psycopg.connect_managed(project.root) as connection:
            operation, config_digest, [denom_sha] = connection.execute(
                "SELECT operation_key,config_digest,output_evidence FROM papersim.command_run_events "
                "WHERE command=%s AND phase='PREPARED' AND input_evidence=%s::jsonb AND operation_key<>%s",
                (_PREFLIGHT_COMMAND, json.dumps([stage["manifest_sha256"], partial_batch]),
                 recovered["operation_key"]),
            ).fetchone()
            denom = json.loads(snapshots.read_snapshot(connection, denom_sha)["content"])
            occurred = datetime.datetime.now(UTC)
            forged = {
                **raw_receipt, "binding": denom["binding"], "operation_key": operation,
                "terminal_event_key": store.command_run_event_key(operation, 1, "SUCCEEDED"),
                "occurred_at": occurred.isoformat(), "denominator_sha256": denom_sha,
                "coverage": denom["coverage"], "missing": denom["missing"],
                "status": "ready", "error_code": None,
            }
            forged_sha = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
                frozenrun._canonical_json(forged), "d1_frozen_preflight_receipt",
                "d1:frozen-preflight:v1:receipt", occurred,
            ))["content_sha256"]
            store_psycopg.record_command_run_event(connection, store.CommandRunEventSubmission(
                operation_key=operation, phase="SUCCEEDED", command=_PREFLIGHT_COMMAND,
                occurred_at=occurred, config_digest=config_digest,
                input_evidence=[stage["manifest_sha256"], partial_batch], output_evidence=[forged_sha, denom_sha],
            ))
        _refused(preflight("soccer", partial_batch), "frozen_preflight_receipt_conflict")

        numeric_batch = capture(start + datetime.timedelta(seconds=2), numeric=True)
        numeric = check_result(preflight("soccer", numeric_batch))
        numeric_report = inspect(numeric)
        assert numeric_report["orphan_preflight_snapshots"] == []
        assert numeric["coverage"]["book_readback_verified_count"] == 1
        preserved = numeric_report["documents"]["d1_frozen_preflight_denominator"]
        market = preserved["market_observations"][0]
        projected = market["contract"]
        assert projected["line"] == {"json_type": "number", "decimal": "2.5"}
        assert projected["handicap"] == {"json_type": "number", "decimal": "-1.234567890123456789E-21"}
        wrong, valid = preserved["book_targets"]
        assert wrong["token_id"] == "111" and wrong["readback_status"] == "book_identity_mismatch"
        assert wrong["raw_condition_id"] == {"json_type": "number", "decimal": "0.5"}
        assert valid["token_id"] == "222" and valid["readback_status"] == "verified"
        with store_psycopg.connect_managed(project.root) as connection:
            raw = snapshots.read_snapshot(connection, market["page_sha256"])["content"]
            assert _NUMERIC_GAMMA_INSERT in raw
            raw = snapshots.read_snapshot(connection, wrong["content_sha256"])["content"]
            assert _NUMERIC_CLOB_KEY in raw

        # A correctly SHA-addressed copy with a different scope cannot use
        # the capture success event registered for the original scope.
        current, _ = acquire.read_capture(repo_root=project.root, batch_sha256=batch)
        orphan = {**current, "scope": {**current["scope"], "tag_slugs": ["wrong"]}}
        with store_psycopg.connect_managed(project.root) as connection:
            copied = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
                acquire._canonical(orphan), "d1_public_capture_manifest", "d1:public-capture:v2",
                datetime.datetime.fromisoformat(orphan["sealed_at"]), schema_version=2,
            ))
        orphan_failure = check_result(preflight("crypto", copied["content_sha256"]), "failed")
        assert orphan_failure["error_code"] == "frozen_preflight_capture_event_missing"
        assert orphan_failure["denominator_sha256"] is None
        assert orphan_failure["readback"] == {"archive": "verified", "capture": "unverified"}
        assert orphan_failure["terminal_event_key"] == store.command_run_event_key(
            orphan_failure["operation_key"], 1, "FAILED")
        altered = {**current, "requests": [{**current["requests"][0], "byte_length": 999},
                                          *current["requests"][1:]]}
        with store_psycopg.connect_managed(project.root) as connection:
            invalid_bytes = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
                acquire._canonical(altered), "d1_public_capture_manifest", "d1:public-capture:v2",
                datetime.datetime.fromisoformat(altered["sealed_at"]), schema_version=2,
            ))
        untrusted = check_result(preflight("crypto", invalid_bytes["content_sha256"]), "failed")
        assert untrusted["error_code"] == "d1_capture_manifest_request_invalid"
        assert untrusted["denominator_sha256"] is None
        assert inspect(initial)["orphan_preflight_snapshots"] == []
        assert not list(project.temporary.glob("polymarket-d1-frozen-*/call-*"))
        assert not staging.exists() or not list(staging.iterdir())


@_NATIVE_PREFLIGHT
def test_preflight_and_borrowed_archives_refuse_cross_instance_inputs(tmp_path):
    from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import acquire
    from tests.test_papersim_d1compare_archive_native import (
        _cli, _isolated_project, _process_env, _receipt, _refused,
    )

    left_dir, right_dir = tmp_path / "left", tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    with _isolated_project(left_dir) as left, _isolated_project(right_dir) as right:
        staging = tmp_path / "caller-stage"
        left_stage = _receipt(_cli(left, "stage", staging, "--leg", "crypto"))
        right_stage = _receipt(_cli(right, "stage", staging, "--leg", "all"))
        lefthash, righthash = left_stage["manifest_sha256"], right_stage["manifest_sha256"]
        assert lefthash != righthash
        assert left_stage["legs"]["crypto"] == right_stage["legs"]["crypto"]
        now = datetime.datetime.now(UTC)
        start, end = now - datetime.timedelta(seconds=60), now + datetime.timedelta(minutes=5)
        cli = str(REPO_ROOT / "tools/d1_compare.py")

        def process(project, source, *args):
            return subprocess.run([sys.executable, "-B", "-c", source, *args], cwd=REPO_ROOT,
                                  env=_process_env(project), capture_output=True, timeout=90)

        def capture(project, source, begin):
            result = process(project, source, "capture-inputs", "--repo-root", str(project.root),
                             "--tag-slug", "soccer", "--tag-slug", "empty", "--allow-public-fetch",
                             "--window-start", begin.isoformat(), "--window-end", end.isoformat())
            assert result.returncode == 0 and not result.stderr, result.stderr
            return json.loads(result.stdout)

        left_batch = capture(left, _CAPTURE_PROCESS.replace("WITH_FAILED_PAGE", "False"), start)
        right_batch = capture(right, _PREFLIGHT_NUMERIC_CAPTURE, start + datetime.timedelta(seconds=1))
        left_capture, right_capture = left_batch["batch_sha256"], right_batch["batch_sha256"]

        def choices(archive, batch):
            return ("--preflight", "--leg", "crypto", "--manifest-sha256", archive, "--batch-sha256", batch)

        def outcome(result):
            assert result.returncode == 2 and not result.stderr, result.stderr.decode()
            receipt = json.loads(result.stdout)
            assert receipt["child_started"] is False and receipt["day_credit"] == 0
            return receipt

        first = outcome(_cli(left, "run-frozen", staging, *choices(lefthash, left_capture)))
        assert first["status"] == "incomplete" and first["denominator_sha256"]
        other_archive = outcome(_cli(left, "run-frozen", staging, *choices(righthash, left_capture)))
        assert other_archive["error_code"] == "frozen_archive_snapshot_missing"
        assert other_archive["denominator_sha256"] is None

        right_document, right_entries = acquire.read_capture(repo_root=right.root, batch_sha256=right_capture)
        with store_psycopg.connect_managed(right.root) as connection:
            manifest = snapshots.read_snapshot(connection, right_capture)
        with store_psycopg.connect_managed(left.root) as connection, connection.transaction():
            # The same manifest and an A-local capture event still cannot
            # make a response registered only in B visible to A.
            registered = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
                manifest["content"], manifest["kind"], manifest["source"],
                manifest["fetched_at"], schema_version=manifest["schema_version"],
            ))
            assert registered["content_sha256"] == right_capture
            scope_sha = right_batch["scope_sha256"]
            store_psycopg.record_command_run_event(connection, store.CommandRunEventSubmission(
                operation_key=f"d1:public-capture:v1:{scope_sha}", command="capture-inputs",
                phase="SUCCEEDED", config_digest=scope_sha, output_evidence=[right_capture],
                scheduled_for=datetime.datetime.fromisoformat(right_document["scope"]["window_start"]),
                occurred_at=datetime.datetime.fromisoformat(right_batch["occurred_at"]),
            ))
            assert snapshots.read_snapshot(connection, right_entries[1].content_sha256) is None
        other_response = outcome(_cli(left, "run-frozen", staging, *choices(lefthash, right_capture)))
        assert other_response["error_code"] == "d1_capture_snapshot_missing"
        assert other_response["denominator_sha256"] is None

        alias = tmp_path / "project-alias"
        alias.symlink_to(left.root, target_is_directory=True)
        stable = outcome(process(
            left, _PREFLIGHT_REPEATABLE_READ, "follow", cli, "run-frozen",
            "--repo-root", str(alias), "--staging-root", str(staging), *choices(lefthash, left_capture),
        ))
        assert stable == {**first, "reused": True}
        for mode in ("alias", "descriptor"):
            result = process(
                left, _PREFLIGHT_INSTANCE, str(alias), str(left.root), str(right.root), mode,
                cli, "run-frozen", "--repo-root", str(alias), "--staging-root", str(staging),
                *choices(lefthash, left_capture),
            )
            _refused(result, "frozen_preflight_cached_recheck_failed",
                     "frozen_preflight_instance_binding_changed", first["receipt_sha256"])
            if mode == "alias":
                assert alias.readlink() == right.root
                alias.unlink()
                alias.symlink_to(left.root, target_is_directory=True)
        assert outcome(_cli(left, "run-frozen", staging, *choices(lefthash, left_capture))) == stable

        with store_psycopg.connect_managed(right.root) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with pytest.raises(frozenrun.FrozenRunError) as error:
                frozenrun.read_staged_archives(righthash, repo_root=left.root, _connection=connection)
            assert error.value.code == "frozen_archive_read_connection_unverified"
            assert error.value.detail == "d1_capture_read_instance_mismatch"
            assert connection.execute("SELECT current_setting('transaction_read_only')").fetchone() == ("on",)

        with store_psycopg.connect_managed(left.root) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            statement = "SELECT pg_backend_pid(), pg_current_snapshot()::text"
            original_view = connection.execute(statement).fetchone()
            with connection.transaction():
                selected, contents = frozenrun.read_staged_archives(lefthash, repo_root=left.root, _connection=connection)
                assert selected["legs"] == left_stage["legs"]
                assert hashlib.sha256(contents["crypto"]).hexdigest() == FROZEN_LIBRARIES["crypto"].digest
                del contents
                # An independent actual stage commits the previously B-only manifest.
                assert _receipt(_cli(left, "stage", staging, "--leg", "all"))["manifest_sha256"] == righthash
                with pytest.raises(frozenrun.FrozenRunError) as error:
                    frozenrun.read_staged_archives(righthash, repo_root=left.root, _connection=connection)
                assert error.value.code == "frozen_archive_snapshot_missing"
                assert connection.execute(statement).fetchone() == original_view
        restored, contents = frozenrun.read_staged_archives(righthash, repo_root=left.root)
        assert set(restored["legs"]) == set(contents) == {"soccer", "crypto"}
        del contents

        for project, operation, expected_count in (
            (left, first["operation_key"], 3), (right, first["operation_key"], 0),
        ):
            inspected = process(project, _INSPECT_PREFLIGHT, str(project.root), operation)
            assert (inspected.returncode, inspected.stderr) == (0, b"")
            report = json.loads(inspected.stdout)
            assert len(report["events"]) == expected_count
            assert report["orphan_preflight_snapshots"] == []
            assert report["scheduled_non_capture_events"] == 0
            assert all(count == 0 for count in report["business"].values())
            if project is right:
                assert report["snapshot_totals"] == {}
        assert not list(left.temporary.glob("polymarket-d1-frozen-*/call-*"))
        assert not list(right.temporary.glob("polymarket-d1-frozen-*/call-*"))


# ---------------------------------------------------------------------------
# operator tool parser wiring (pure construction, no live subcommand)
# ---------------------------------------------------------------------------


def test_operator_parser_accepts_documented_shapes() -> None:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import d1_compare

        parser = d1_compare.build_parser()
        args = parser.parse_args(["diff", "--path-l", "l.json",
                                  "--path-n", "n.json", "--out", "-"])
        assert args.command == "diff" and args.out == "-"
        args = parser.parse_args(["stage", "--staging-root", "/tmp/s"])
        assert args.leg == "all"
        args = parser.parse_args(["run-frozen", "--leg", "crypto",
                                  "--staging-root", "/tmp/s"])
        assert args.authorization is None
        args = parser.parse_args(["restore", "--manifest-sha256", "0" * 64,
                                  "--staging-root", "/tmp/empty-d1"])
        assert args.manifest_sha256 == "0" * 64
    finally:
        sys.path.pop(0)
