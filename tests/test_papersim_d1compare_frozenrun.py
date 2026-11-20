"""D1 frozen-run staging/verification offline proofs.

Mirror bytes are used READ-ONLY: real mirror files are copied into
temporary fixture directories for hashing/staging/override tests and
the frozen scripts are NEVER executed.  Everything here verifies CODE:
no network call, no database, no timer, no managed write.  Synthetic
fixtures are code verification only -- no synthetic or historical
cycle counts as a real operation, and nothing here claims a positive
D1 day.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import datetime
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
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
