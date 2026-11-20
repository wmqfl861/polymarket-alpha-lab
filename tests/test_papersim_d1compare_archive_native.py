"""Managed D1 archive proofs on disposable Linux PostgreSQL clusters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

import pytest

from polymarket_alpha_lab.papersim import migrations, snapshots, store_psycopg
from polymarket_alpha_lab.papersim.d1compare import frozenrun
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from tests.native_port_selection import pick_port
from tests.test_papersim_d1compare_frozenrun import (
    CRYPTO_DESIGNATED,
    FROZEN_LIBRARIES,
    MIRROR_ROOT,
    REPO_ROOT,
    SOCCER_DESIGNATED,
    _crypto_manifest,
    clean_object_repo,
    git_bytes,
    link_local_git_objects,
    source_archive,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES") != "1",
    reason="explicit Linux native PostgreSQL proof is opt-in",
)

_MANIFEST_KIND = "d1_frozen_stage_manifest"
_MANIFEST_SOURCE = "d1:frozen-stage-manifest:v2"
_ARCHIVE_KIND = "d1_frozen_library_archive"
_FIXTURE_TIME = datetime(2026, 11, 20, tzinfo=UTC)
_CLI_TIMEOUT = 180
_LEGACY_FIXTURE_DIGESTS = {
    "soccer": "858b7df636952ac19b6d9a19f7b97accb5a58e859a64a839609530f0951e5473",
    "crypto": "06089b03f2219ea2a49dd66bbe27e3584253068d70a772b72ff46b4a20bf4cf6",
}
_LEGACY_FIXTURE_MANIFESTS = {
    ("soccer", "crypto"): "811783b4366fb820a321cac45b16274a4d6024b008d93205741c1b66a77725e5",
    ("crypto",): "f0714e2eb094adcc744db54bf89c2d7d92db22b44f1585200639f10a5cba2864",
}


@dataclass(frozen=True)
class NativeProject:
    postgres: ProjectPostgres
    temporary: Path

    @property
    def root(self) -> Path:
        return self.postgres.layout.root


@contextmanager
def _isolated_project(base: Path):
    prefix = Path(os.environ["POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX"])
    assert prefix.is_absolute() and (prefix / "bin/postgres").is_file()
    with tempfile.TemporaryDirectory(dir=base, prefix="d1-project-") as owned:
        root = Path(owned) / "project"
        root.mkdir()
        temporary = Path(owned) / "temp"
        temporary.mkdir(mode=0o700)
        (root / "pyproject.toml").write_text('[project]\nname="polymarket-alpha-lab"\n', encoding="utf-8")
        shutil.copytree(REPO_ROOT / "database", root / "database")
        shutil.copytree(REPO_ROOT / "supabase/migrations", root / "supabase/migrations")
        link_local_git_objects(root)

        assert import_runtime_directory(root, prefix).split(".")[0] == "18"
        postgres = ProjectPostgres(root)
        assert postgres.initialize(port=pick_port())["status"] == "initialized"
        try:
            assert postgres.up()["started_here"] is True
            with store_psycopg.connect_owner(root) as conn:
                migration_count = len(migrations.papersim_migration_catalog(root))
                assert migrations.apply_pending_migrations(conn, root) == {
                    "applied": migration_count, "pending_before": migration_count,
                }
            yield NativeProject(postgres, temporary)
        finally:
            assert postgres.down()["status"] == "stopped"


@pytest.fixture(scope="module")
def soccer_archive(clean_object_repo: Path) -> bytes:
    return source_archive(clean_object_repo, FROZEN_LIBRARIES["soccer"])


@pytest.fixture(scope="module")
def native_project(tmp_path_factory: pytest.TempPathFactory, soccer_archive: bytes):
    with _isolated_project(tmp_path_factory.mktemp("d1-native")) as project:
        with store_psycopg.connect_managed(project.root) as conn:
            seeded = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                soccer_archive, "fixture_preexisting_snapshot", "fixture:independent-origin", _FIXTURE_TIME,
            ))
        assert seeded["content_sha256"] == FROZEN_LIBRARIES["soccer"].digest
        yield project


def _process_env(project: NativeProject) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC",
        "TMPDIR": str(project.temporary), "PYTHONPATH": str(REPO_ROOT / "src"),
    }


def _cli(project: NativeProject, command: str, staging: Path | None, *args: str,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    staging_args = [] if staging is None else ["--staging-root", str(staging)]
    return subprocess.run(
        [sys.executable, "-B", str(REPO_ROOT / "tools/d1_compare.py"), command,
         "--repo-root", str(project.root), *staging_args, *args],
        cwd=project.root, env={**_process_env(project), **(extra_env or {})},
        capture_output=True, timeout=_CLI_TIMEOUT,
    )


def _receipt(result: subprocess.CompletedProcess[bytes], *, stage_version: int = 2) -> dict:
    if result.returncode != 0 or result.stderr:
        pytest.fail(f"D1 CLI did not return a clean receipt (exit {result.returncode})", pytrace=False)
    assert result.stdout.count(b"\n") == 1 and result.stdout.endswith(b"\n")
    receipt = json.loads(result.stdout)
    assert receipt["stage_version"] == stage_version
    return receipt


def _refused(result: subprocess.CompletedProcess[bytes], *labels: str) -> None:
    if result.returncode != 2 or result.stdout or any(label.encode() not in result.stderr for label in labels):
        pytest.fail(f"D1 CLI did not refuse with {labels} (exit {result.returncode})", pytrace=False)


def _assert_no_archives_on_disk(project: NativeProject, staging: Path) -> None:
    assert not list(staging.rglob("*"))
    assert not list(project.temporary.glob("polymarket-d1-frozen-*/call-*"))


def _inventory(project: NativeProject) -> dict:
    with store_psycopg.connect_managed(project.root) as conn:
        rows = conn.execute(
            "SELECT content_sha256, byte_length, kind, source, fetched_at, schema_version, "
            "paper_only, report_only, readonly FROM papersim.snapshots ORDER BY content_sha256"
        ).fetchall()
    return {digest: tuple(metadata) for digest, *metadata in rows}


def _paper_counts(project: NativeProject) -> tuple:
    with store_psycopg.connect_managed(project.root) as conn:
        return conn.execute(
            "SELECT (SELECT count(*) FROM papersim.scan_runs), "
            "(SELECT count(*) FROM papersim.candidates), (SELECT count(*) FROM papersim.entries), "
            "(SELECT count(*) FROM papersim.market_outcomes), (SELECT count(*) FROM papersim.marks), "
            "(SELECT count(*) FROM papersim.settlements), (SELECT count(*) FROM papersim.bankroll_events)"
        ).fetchone()


def _identity(project: NativeProject) -> tuple:
    with store_psycopg.connect_owner(project.root) as conn:
        return conn.execute(
            "SELECT system_identifier::text, current_setting('data_directory'), "
            "current_database(), pg_postmaster_start_time() FROM pg_control_system()"
        ).fetchone()


def _stage_v1_inputs(project: NativeProject, root: Path, legs: tuple[str, ...]
                     ) -> tuple[Path, dict[str, Path]]:
    manifest = root / "staging_manifest.json"
    sources = {leg: root / "libraries" / FROZEN_LIBRARIES[leg].pin / "src.tar" for leg in legs}
    if old_checkout := os.environ.get("PAL_D1_TEST_STAGE_V1_ROOT"):
        old_root = Path(old_checkout)
        assert git_bytes(old_root, "rev-parse", "HEAD").strip() == (
            b"060ac9f88ba5079e2fba551dc78fe15b41dd3d10"
        )
        result = subprocess.run(
            [sys.executable, "-B", str(old_root / "tools/d1_compare.py"), "stage", "--leg",
             "all" if len(legs) == 2 else legs[0], "--repo-root", str(project.root),
             "--mirror-root", str(old_root / "docs/paper-sim/server-mirror"), "--staging-root", str(root)],
            cwd=old_root, env={**_process_env(project), "PYTHONPATH": str(old_root / "src"),
                               "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "tar.umask",
                               "GIT_CONFIG_VALUE_0": "0022"},
            capture_output=True, timeout=_CLI_TIMEOUT,
        )
        assert result.returncode == 0 and not result.stderr
        return manifest, sources

    entries = {}
    mirror_rows = frozenrun.manifest_rows(MIRROR_ROOT / "MANIFEST.sha256")
    for leg, path in sources.items():
        spec = FROZEN_LIBRARIES[leg]
        archive = git_bytes(project.root, "-c", "tar.umask=0022", "archive", "--format=tar",
                            "--mtime=@1795125600", f"{spec.commit}:src")
        assert len(archive) == spec.length and hashlib.sha256(archive).hexdigest() == _LEGACY_FIXTURE_DIGESTS[leg]
        path.parent.mkdir(parents=True)
        path.write_bytes(archive)
        designated = SOCCER_DESIGNATED if leg == "soccer" else CRYPTO_DESIGNATED
        entries[leg] = {
            "leg": leg,
            "library": {
                "pin": spec.pin, "archive_sha256": _LEGACY_FIXTURE_DIGESTS[leg],
                "archive_size_bytes": spec.length, "tree_path": f"libraries/{spec.pin}/src",
            },
            "staged": [
                {"mirror_path": name, "sha256": mirror_rows[name][0], "size_bytes": mirror_rows[name][1],
                 "path": f"sandbox/paper-sim/{name}", "library_pin": spec.pin}
                for name in designated
            ],
            "bindings": [], "mount_argv": [], "egress_hosts": [], "child_env": {}, "gate_failures": [],
        }
    content = json.dumps({"stage_version": 1, "legs": entries}, indent=1, sort_keys=True).encode("utf-8")
    assert hashlib.sha256(content).hexdigest() == _LEGACY_FIXTURE_MANIFESTS[legs]
    manifest.write_bytes(content)
    return manifest, sources


def _import_cli(project: NativeProject, trusted_sha: str,
                *source_args: str) -> subprocess.CompletedProcess[bytes]:
    return _cli(project, "import-original", None, "--trusted-manifest-sha256", trusted_sha, *source_args)


def test_import_original_restores_real_stage_v1_after_restart(
    tmp_path: Path, clean_object_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _isolated_project(tmp_path) as project:
        before_paper = _paper_counts(project)
        with tempfile.TemporaryDirectory(dir=tmp_path, prefix="old-stage-") as inputs:
            manifest, files = _stage_v1_inputs(project, Path(inputs), ("soccer", "crypto"))
            original_manifest = manifest.read_bytes()
            original_digest = hashlib.sha256(original_manifest).hexdigest()
            originals = {leg: path.read_bytes() for leg, path in files.items()}
            digests = {leg: hashlib.sha256(raw).hexdigest() for leg, raw in originals.items()}
            for leg, counts in (("soccer", (3, 2856)), ("crypto", (3, 2855))):
                with tarfile.open(fileobj=io.BytesIO(originals[leg]), mode="r:") as archive:
                    members = archive.getmembers()
                    assert (sum(member.isdir() for member in members),
                            sum(member.isfile() for member in members)) == counts
                    assert {member.mode for member in members if member.isdir()} == {0o755}
                    assert {member.mode for member in members if member.isfile()} == {0o644}
                    assert members[0].mtime != FROZEN_LIBRARIES[leg].epoch

            arguments = ["--manifest-file", str(manifest),
                         "--archive-file", f"soccer={files['soccer']}", "--archive-file", f"crypto={files['crypto']}"]
            manifest.write_bytes(original_manifest + b"\n")
            _refused(_import_cli(project, original_digest, *arguments),
                     "frozen_archive_original_digest_mismatch", "original manifest")
            manifest.write_bytes(original_manifest)
            with files["crypto"].open("r+b") as corrupted:
                corrupted.write(bytes([originals["crypto"][0] ^ 1]))
            _refused(_import_cli(project, original_digest, *arguments),
                     "frozen_archive_original_digest_mismatch", "crypto")
            with files["crypto"].open("r+b") as repaired:
                repaired.write(originals["crypto"][:1])
            _check_import_rollback(project, original_digest, arguments)
            assert _inventory(project) == {}

            started = datetime.now(UTC)
            imported = _receipt(_import_cli(project, original_digest, *arguments), stage_version=3)
            finished = datetime.now(UTC)
            assert imported["original_manifest"] == {
                "stage_version": 1, "sha256": original_digest, "byte_length": len(original_manifest),
            }
            assert imported["legs"].keys() == originals.keys()
            for leg, spec in FROZEN_LIBRARIES.items():
                assert imported["legs"][leg]["library"] == {
                    "pin": spec.pin, "commit": spec.commit, "tree": spec.tree, "subpath": "src",
                    "format": "tar", "export_version": 1, "tar_umask": "0022",
                    "archive_sha256": digests[leg], "archive_byte_length": len(originals[leg]),
                }
            digest = imported["manifest_sha256"]
            rows = _inventory(project)
            assert set(rows) == {original_digest, digest, *digests.values()}
            assert rows[digest][1:3] == (_MANIFEST_KIND, "d1:frozen-stage-import:v3")
            assert rows[digest][4] == 3
            assert all(started <= metadata[3] <= finished for metadata in rows.values())
            assert _paper_counts(project) == before_paper

            managed = ("--archive-sha256", f"crypto={digests['crypto']}",
                       "--archive-sha256", f"soccer={digests['soccer']}")
            mixed = ("--manifest-file", str(manifest), "--archive-file", f"crypto={files['crypto']}",
                     "--archive-sha256", f"soccer={digests['soccer']}")
            with ThreadPoolExecutor(max_workers=2) as pool:
                reads = [pool.submit(_import_cli, project, original_digest, *source_args)
                         for source_args in (managed, mixed)]
                assert all(_receipt(read.result(), stage_version=3) == imported for read in reads)
            assert _inventory(project) == rows

        staging = tmp_path / "restore"
        staging.mkdir()
        _refused(_cli(project, "restore", staging, "--manifest-sha256", original_digest),
                 "frozen_archive_legacy_evidence_missing", "import-original")
        offline_git = tmp_path / "disabled-v1-git"
        (project.root / ".git").rename(offline_git)
        first_identity = _identity(project)
        assert Path(first_identity[1]) == project.postgres.layout.cluster
        assert project.postgres.down() == {"status": "stopped", "data_preserved": True}
        _refused(_cli(project, "restore", staging, "--manifest-sha256", digest),
                 "frozen_managed_archive_read_failed")
        assert project.postgres.up()["started_here"] is True
        restarted = _identity(project)
        assert restarted[:3] == first_identity[:3] and restarted[3] > first_identity[3]
        assert not Path(inputs).exists() and not (project.root / ".git").exists()
        assert _receipt(_cli(project, "restore", staging, "--manifest-sha256", digest), stage_version=3) == imported

        compared = subprocess.run(
            [sys.executable, "-B", "-c", _COMPARE_RESTORED_BYTES, str(project.root), digest, *originals],
            input=b"".join([original_manifest, *originals.values()]), cwd=project.root, env=_process_env(project),
            capture_output=True, timeout=_CLI_TIMEOUT,
        )
        assert compared.returncode == 0 and not compared.stdout and not compared.stderr

        def unexpected_git(*args, **kwargs):
            pytest.fail("v3 readback entered Git")

        monkeypatch.setattr(frozenrun, "_isolated_git", unexpected_git)
        monkeypatch.setattr(tempfile, "tempdir", str(project.temporary))
        for leg, spec in FROZEN_LIBRARIES.items():
            blob = git_bytes(clean_object_repo, "cat-file", "blob", f"{spec.commit}:src/{spec.module}")
            with frozenrun.plan_chain(leg, mirror_root=MIRROR_ROOT, staging_root=staging,
                                      repo_root=project.root, manifest_sha256=digest) as plan:
                leaf = plan.staging_root
                assert plan.library.archive_sha256 == digests[leg]
                assert plan.bindings[0].hard_coded_path == f"{frozenrun.LIBRARY_HOME}/src"
                assert all(Path(binding.staged_path).is_relative_to(leaf) for binding in plan.bindings)
                assert str(leaf) in " ".join(plan.mount_argv)
                result = plan.run_child([sys.executable, "-IB", "-c", _PROBE_CHILD,
                                         str(leaf / plan.library.tree_path / spec.module),
                                         hashlib.sha256(blob).hexdigest(), *frozenrun.CHILD_ENV_ALLOWLIST],
                                        timeout=20)
                assert result.returncode == 0 and not result.stdout and not result.stderr
                assert str(leaf) not in json.dumps(plan.as_mapping())
            assert not leaf.exists()
        assert _inventory(project) == rows
        assert _paper_counts(project) == before_paper
        _assert_no_archives_on_disk(project, staging)
        print(json.dumps({
            "external_old_stage": bool(os.environ.get("PAL_D1_TEST_STAGE_V1_ROOT")),
            "stage_v1_sha256": original_digest, "issued_v3_sha256": digest,
            "original_manifest_bytes": len(original_manifest), "tar_digests": digests,
            "tar_byte_lengths": {leg: len(raw) for leg, raw in originals.items()},
            "system_identifier": restarted[0], "data_directory": restarted[1], "database": restarted[2],
            "postgres_started_before": first_identity[3].isoformat(),
            "postgres_started_after": restarted[3].isoformat(),
        }, sort_keys=True))


def _check_import_rollback(project: NativeProject, digest: str, arguments: list[str]) -> None:
    with store_psycopg.connect_owner(project.root) as conn:
        conn.execute("CREATE SEQUENCE public.d1_fixture_import_attempt")
        conn.execute("GRANT USAGE ON SEQUENCE public.d1_fixture_import_attempt TO pal_app")
        conn.execute("""
            CREATE FUNCTION public.d1_fixture_block_import() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.source = 'd1:frozen-stage-import:original-tar:leg=crypto' THEN
                    IF (SELECT count(*) FROM papersim.snapshots) = 2 THEN
                        PERFORM nextval('public.d1_fixture_import_attempt');
                    END IF;
                    RAISE EXCEPTION 'fixture_second_import_refusal';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        conn.execute("""
            CREATE TRIGGER d1_fixture_block_import BEFORE INSERT ON papersim.snapshots
            FOR EACH ROW EXECUTE FUNCTION public.d1_fixture_block_import()
        """)
    _refused(_import_cli(project, digest, *arguments), "frozen_managed_archive_write_failed")
    assert _inventory(project) == {}
    with store_psycopg.connect_owner(project.root) as conn:
        assert conn.execute("SELECT is_called FROM public.d1_fixture_import_attempt").fetchone() == (True,)
        conn.execute("DROP TRIGGER d1_fixture_block_import ON papersim.snapshots")
        conn.execute("DROP FUNCTION public.d1_fixture_block_import()")
        conn.execute("DROP SEQUENCE public.d1_fixture_import_attempt")


def _register_fixture_manifest(project: NativeProject, document: dict, *, version: int = 2) -> str:
    content = (json.dumps(document, sort_keys=True, indent=1) if version == 1
               else json.dumps(document, sort_keys=True, separators=(",", ":"))).encode("utf-8")
    with store_psycopg.connect_managed(project.root) as conn:
        row = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content, _MANIFEST_KIND, f"d1:frozen-stage-manifest:v{version}", _FIXTURE_TIME,
            schema_version=version,
        ))
    return row["content_sha256"]


def _assert_library_bindings(receipt: dict, legs: set[str]) -> None:
    assert receipt["legs"].keys() == legs
    script_rows = frozenrun.manifest_rows(MIRROR_ROOT / "MANIFEST.sha256")
    for leg, entry in receipt["legs"].items():
        spec = FROZEN_LIBRARIES[leg]
        assert entry["library"] == {
            "pin": spec.pin, "commit": spec.commit, "tree": spec.tree, "subpath": "src",
            "commit_epoch": spec.epoch, "format": "tar", "export_version": 2, "tar_umask": "0002",
            "archive_sha256": spec.digest, "archive_byte_length": spec.length,
        }
        scripts = SOCCER_DESIGNATED if leg == "soccer" else CRYPTO_DESIGNATED
        assert [row["mirror_path"] for row in entry["scripts"]] == list(scripts)
        for row in entry["scripts"]:
            assert (row["sha256"], row["size_bytes"]) == script_rows[row["mirror_path"]]
        assert all(not Path(binding["source_path"]).is_absolute() for binding in entry["bindings"])
        assert "mount_argv" not in entry


_COMPARE_RESTORED_BYTES = """
from pathlib import Path
import sys
from contextlib import closing
from polymarket_alpha_lab.papersim import snapshots, store_psycopg
from polymarket_alpha_lab.papersim.d1compare import frozenrun

root, digest, *legs = sys.argv[1:]
manifest, archives = frozenrun.read_staged_archives(digest, repo_root=Path(root))
assert archives.keys() == set(legs) == manifest['legs'].keys()
if 'original_manifest' in manifest:
    reference = manifest['original_manifest']
    with closing(store_psycopg.connect_managed(Path(root))) as connection:
        original = snapshots.read_snapshot(connection, reference['sha256'])['content']
    assert sys.stdin.buffer.read(reference['byte_length']) == original
for leg in legs:
    archive = memoryview(archives[leg])
    for start in range(0, len(archive), 65536):
        chunk = archive[start:start + 65536]
        if sys.stdin.buffer.read(len(chunk)) != chunk:
            raise SystemExit('managed readback differs from the original stream')
assert not sys.stdin.buffer.read(1)
"""

_PROBE_CHILD = """
import hashlib
import os
from pathlib import Path
import sys

module, digest, *allowed = sys.argv[1:]
assert set(os.environ) == set(allowed)
assert os.environ['TZ'] == 'UTC' and os.environ['PYTHONHASHSEED'] == '0'
assert hashlib.sha256(Path(module).read_bytes()).hexdigest() == digest
"""


def test_stage_and_restore_survive_a_true_postgres_restart(
    native_project: NativeProject, clean_object_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    soccer_archive: bytes,
) -> None:
    project = native_project
    staging, restored_staging = tmp_path / "publish", tmp_path / "restore"
    staging.mkdir()
    restored_staging.mkdir()
    soccer = FROZEN_LIBRARIES["soccer"]
    crypto = FROZEN_LIBRARIES["crypto"]
    raw = {"soccer": soccer_archive, "crypto": source_archive(clean_object_repo, crypto)}
    before_rows = _inventory(project)
    before_paper = _paper_counts(project)
    assert before_rows[soccer.digest][1:5] == (
        "fixture_preexisting_snapshot", "fixture:independent-origin", _FIXTURE_TIME, 1,
    )

    stage_started = datetime.now(UTC)
    published = _receipt(_cli(project, "stage", staging))
    stage_finished = datetime.now(UTC)
    _assert_library_bindings(published, {"soccer", "crypto"})
    digest = published["manifest_sha256"]
    manifest = {key: value for key, value in published.items() if key != "manifest_sha256"}
    assert str(staging) not in json.dumps(manifest) and str(project.temporary) not in json.dumps(manifest)
    _assert_no_archives_on_disk(project, staging)

    with store_psycopg.connect_managed(project.root) as conn:
        saved_manifest = snapshots.read_snapshot(conn, digest)
        assert json.loads(saved_manifest["content"]) == manifest
        assert hashlib.sha256(saved_manifest["content"]).hexdigest() == digest
        for leg, spec in FROZEN_LIBRARIES.items():
            saved = snapshots.read_snapshot(conn, spec.digest)
            assert saved["byte_length"] == spec.length
            assert hashlib.sha256(saved["content"]).hexdigest() == spec.digest
            if saved["content"] != raw[leg]:
                pytest.fail(f"stored {leg} tar differs from the independent raw Git stdout", pytrace=False)
            del saved
    after_rows = _inventory(project)
    assert set(after_rows) == set(before_rows) | {crypto.digest, digest}
    assert after_rows[soccer.digest] == before_rows[soccer.digest]
    assert after_rows[crypto.digest][1] == _ARCHIVE_KIND
    assert after_rows[crypto.digest][2] == (
        f"d1:frozen-library:leg=crypto:commit={crypto.commit}:tree={crypto.tree}:subpath=src:export=v2"
    )
    assert after_rows[crypto.digest][4:] == (2, True, True, True)
    assert after_rows[digest][1:3] == (_MANIFEST_KIND, _MANIFEST_SOURCE)
    assert after_rows[digest][4:] == (2, True, True, True)
    for saved_digest in set(after_rows) - set(before_rows):
        assert stage_started <= after_rows[saved_digest][3] <= stage_finished
    assert _paper_counts(project) == before_paper

    before_restart = _identity(project)
    assert Path(before_restart[1]) == project.postgres.layout.cluster
    assert project.postgres.down() == {"status": "stopped", "data_preserved": True}
    assert project.postgres.status()["status"] == "stopped"
    _refused(_cli(project, "restore", restored_staging, "--manifest-sha256", digest),
             "frozen_managed_archive_read_failed")
    _assert_no_archives_on_disk(project, restored_staging)
    restarted = project.postgres.up()
    assert restarted["status"] == "running" and restarted["started_here"] is True
    after_restart = _identity(project)
    assert after_restart[:3] == before_restart[:3]
    assert after_restart[3] > before_restart[3]

    # Removing the fixture Git view makes recovery from the local objects impossible.
    git_directory = project.root / ".git"
    offline_git = project.root / "disabled-git-objects"
    git_directory.rename(offline_git)
    try:
        assert _receipt(_cli(project, "restore", restored_staging, "--manifest-sha256", digest)) == published
        compared = subprocess.run(
            [sys.executable, "-B", "-c", _COMPARE_RESTORED_BYTES, str(project.root), digest, *raw],
            input=b"".join(raw.values()), cwd=project.root, env=_process_env(project),
            capture_output=True, timeout=_CLI_TIMEOUT,
        )
        if compared.returncode or compared.stderr or compared.stdout:
            pytest.fail("the new readback process could not verify both original streams", pytrace=False)
        del raw
        monkeypatch.setattr(tempfile, "tempdir", str(project.temporary))
        for leg, spec in FROZEN_LIBRARIES.items():
            blob = git_bytes(clean_object_repo, "cat-file", "blob", f"{spec.commit}:src/{spec.module}")
            with frozenrun.plan_chain(
                leg, mirror_root=MIRROR_ROOT, staging_root=restored_staging,
                repo_root=project.root, manifest_sha256=digest,
                base_env={"PGPASSWORD": "synthetic-fixture", "UNRELATED": "fixture"},
            ) as plan:
                leaf = plan.staging_root
                assert stat.S_IMODE(leaf.stat().st_mode) == 0o700
                assert plan.library.pin == spec.pin
                module = leaf / plan.library.tree_path / spec.module
                assert module.read_bytes() == blob
                result = plan.run_child(
                    [sys.executable, "-IB", "-c", _PROBE_CHILD, str(module),
                     hashlib.sha256(blob).hexdigest(), *frozenrun.CHILD_ENV_ALLOWLIST], timeout=20,
                )
                assert result.returncode == 0 and not result.stdout and not result.stderr
                assert str(leaf) not in json.dumps(plan.as_mapping())
            assert not leaf.exists()
    finally:
        offline_git.rename(git_directory)
    assert _inventory(project) == after_rows
    assert _paper_counts(project) == before_paper
    _assert_no_archives_on_disk(project, restored_staging)


def test_stage_all_rolls_back_both_legs_on_second_leg_or_storage_failure(tmp_path: Path) -> None:
    with _isolated_project(tmp_path) as project:
        staging = tmp_path / "staging"
        staging.mkdir()
        mirror = tmp_path / "broken-mirror"
        for name in (*SOCCER_DESIGNATED, *CRYPTO_DESIGNATED, "MANIFEST.sha256"):
            copy = mirror / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(MIRROR_ROOT / name, copy)
        with (mirror / CRYPTO_DESIGNATED[0]).open("ab") as script:
            script.write(b"\n# fixture changed the second leg\n")
        before_paper = _paper_counts(project)
        assert _inventory(project) == {}
        _refused(_cli(project, "stage", staging, "--leg", "all", "--mirror-root", str(mirror)),
                 "frozen_stage_manifest_mismatch", CRYPTO_DESIGNATED[0])
        assert _inventory(project) == {}
        _assert_no_archives_on_disk(project, staging)

        # The sequence records reaching manifest INSERT with both archive rows
        # already visible inside the publisher's transaction.
        with store_psycopg.connect_owner(project.root) as conn:
            conn.execute("CREATE SEQUENCE public.d1_fixture_manifest_attempt")
            conn.execute("GRANT USAGE ON SEQUENCE public.d1_fixture_manifest_attempt TO pal_app")
            conn.execute("""
                CREATE FUNCTION public.d1_fixture_block_manifest() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.kind = 'd1_frozen_stage_manifest' THEN
                        IF (SELECT count(*) FROM papersim.snapshots
                            WHERE kind = 'd1_frozen_library_archive') = 2 THEN
                            PERFORM nextval('public.d1_fixture_manifest_attempt');
                        END IF;
                        RAISE EXCEPTION 'fixture_storage_refusal';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """)
            conn.execute("""
                CREATE TRIGGER d1_fixture_block_manifest BEFORE INSERT ON papersim.snapshots
                FOR EACH ROW EXECUTE FUNCTION public.d1_fixture_block_manifest()
            """)
        _refused(_cli(project, "stage", staging, "--leg", "all"), "frozen_managed_archive_write_failed")
        with store_psycopg.connect_owner(project.root) as conn:
            assert conn.execute("SELECT is_called FROM public.d1_fixture_manifest_attempt").fetchone() == (True,)
            conn.execute("DROP TRIGGER d1_fixture_block_manifest ON papersim.snapshots")
            conn.execute("DROP FUNCTION public.d1_fixture_block_manifest()")
            conn.execute("DROP SEQUENCE public.d1_fixture_manifest_attempt")
        assert _inventory(project) == {}
        _assert_no_archives_on_disk(project, staging)

        missing = _register_fixture_manifest(project, _crypto_manifest())
        _refused(_cli(project, "restore", staging, "--manifest-sha256", missing),
                 "frozen_archive_snapshot_missing", FROZEN_LIBRARIES["crypto"].digest)
        assert set(_inventory(project)) == {missing}
        retried = _receipt(_cli(project, "stage", staging, "--leg", "all"))
        _assert_library_bindings(retried, {"soccer", "crypto"})
        assert set(_inventory(project)) == {
            missing, retried["manifest_sha256"], *(spec.digest for spec in FROZEN_LIBRARIES.values()),
        }
        assert _paper_counts(project) == before_paper
        _assert_no_archives_on_disk(project, staging)


def test_stage_is_reproducible_with_independent_git_configuration_and_attribute_noise(
    native_project: NativeProject, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = native_project
    monkeypatch.setattr(tempfile, "tempdir", str(project.temporary))
    staging = tmp_path / "staging"
    staging.mkdir()
    baseline = _receipt(_cli(project, "stage", staging, "--leg", "soccer"))
    _assert_library_bindings(baseline, {"soccer"})
    first_rows = _inventory(project)
    spec = FROZEN_LIBRARIES["soccer"]
    injected = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "tar.umask", "GIT_CONFIG_VALUE_0": "0077"}
    noisy = source_archive(project.root, spec, umask=None, extra_env=injected)
    assert len(noisy) == spec.length
    assert hashlib.sha256(noisy).hexdigest() == "61c06ef46bfc904d6dcf9bb692e6d41d8ea4726105518d72c53ef1d974d0a93d"
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.validate_library_archive("soccer", noisy, repo_root=project.root, staging_root=staging)
    assert error.value.code == "frozen_library_archive_mode_mismatch"
    del noisy
    assert _receipt(_cli(project, "stage", staging, "--leg", "soccer", extra_env=injected)) == baseline
    assert _inventory(project) == first_rows

    attributes = project.root / ".git/info/attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("polymarket_alpha_lab/__init__.py export-ignore\n", encoding="utf-8")
    try:
        omitted = source_archive(project.root, spec)
        assert len(omitted) == 95_221_760
        assert hashlib.sha256(omitted).hexdigest() == "07950b2aa00da84d73fe9a2951d4f23336cade9eadda9a8af62d4ab7f61ac454"
        with pytest.raises(frozenrun.FrozenRunError) as error:
            frozenrun.validate_library_archive("soccer", omitted, repo_root=project.root, staging_root=staging)
        assert error.value.code == "frozen_library_archive_members_missing"
        del omitted
        assert _receipt(_cli(project, "stage", staging, "--leg", "soccer")) == baseline
    finally:
        attributes.unlink()
    assert _inventory(project) == first_rows
    assert _receipt(_cli(project, "restore", staging, "--manifest-sha256", baseline["manifest_sha256"])) == baseline
    _assert_no_archives_on_disk(project, staging)


@pytest.fixture(scope="module")
def staged_crypto(native_project: NativeProject, tmp_path_factory: pytest.TempPathFactory) -> str:
    staging = tmp_path_factory.mktemp("d1-crypto-staging")
    receipt = _receipt(_cli(native_project, "stage", staging, "--leg", "crypto"))
    _assert_library_bindings(receipt, {"crypto"})
    _assert_no_archives_on_disk(native_project, staging)
    return receipt["manifest_sha256"]


def test_concurrent_stage_reuses_the_same_first_registration(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path,
) -> None:
    project = native_project
    stages = [tmp_path / "first", tmp_path / "second"]
    for stage in stages:
        stage.mkdir()
    before = _inventory(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(_cli, project, "stage", stage, "--leg", "crypto") for stage in stages]
        receipts = [_receipt(task.result()) for task in tasks]
    assert receipts[0] == receipts[1] and receipts[0]["manifest_sha256"] == staged_crypto
    assert _inventory(project) == before
    for stage in stages:
        _assert_no_archives_on_disk(project, stage)


def test_restore_refuses_stale_invalid_and_missing_manifests(native_project: NativeProject, tmp_path: Path) -> None:
    project = native_project
    schema = _crypto_manifest()
    schema["stage_version"] = 3
    cases = [(schema, "frozen_archive_manifest_schema_invalid")]
    for field, value in (
        ("pin", FROZEN_LIBRARIES["soccer"].pin), ("tree", "0" * 40),
        ("archive_byte_length", FROZEN_LIBRARIES["crypto"].length - 1),
    ):
        incorrect = _crypto_manifest()
        incorrect["legs"]["crypto"]["library"][field] = value
        cases.append((incorrect, "frozen_archive_manifest_binding_invalid"))
    legacy = {"stage_version": 1, "legs": {"crypto": {"library": {"tree_path": "old/src"}}}}
    cases.append((legacy, "frozen_archive_legacy_evidence_missing"))
    for document, code in cases:
        digest = _register_fixture_manifest(project, document)
        _refused(_cli(project, "restore", tmp_path, "--manifest-sha256", digest), code)
    digest = _register_fixture_manifest(project, legacy, version=1)
    _refused(_cli(project, "restore", tmp_path, "--manifest-sha256", digest),
             "frozen_archive_legacy_evidence_missing", "import-original")
    _refused(_import_cli(project, digest, "--archive-sha256", "crypto=" + "0" * 64),
             "frozen_archive_legacy_evidence_missing", "crypto")
    _refused(_cli(project, "restore", tmp_path, "--manifest-sha256", "0" * 64),
             "frozen_archive_snapshot_missing", "manifest")
    _assert_no_archives_on_disk(project, tmp_path)


def test_import_original_preserves_preexisting_stage_v1_bytes_and_metadata(
    native_project: NativeProject, clean_object_repo: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    project = native_project
    spec = FROZEN_LIBRARIES["crypto"]
    old_stage = tmp_path / "old-stage"
    old_manifest, files = _stage_v1_inputs(project, old_stage, ("crypto",))
    original = files["crypto"].read_bytes()
    archive_digest = hashlib.sha256(original).hexdigest()
    assert archive_digest != spec.digest and len(original) == spec.length
    expected = _crypto_manifest()["legs"]["crypto"]
    legacy = json.loads(old_manifest.read_bytes())
    shutil.rmtree(old_stage)
    entry = legacy["legs"]["crypto"]
    library = entry["library"]
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched fixture\n")
    library.update(tree_path=str(sentinel), archive_path=str(sentinel))
    entry["staged"][0]["path"] = str(sentinel)
    entry.update(bindings=[{"staged_path": str(sentinel)}], mount_argv=[str(sentinel)],
                 child_env={"PATH": str(sentinel)}, egress_hosts=[str(sentinel)], gate_failures=[str(sentinel)])
    original_manifest = json.dumps(legacy, sort_keys=True, indent=1).encode("utf-8")
    original_digest = hashlib.sha256(original_manifest).hexdigest()
    manifest = tmp_path / "stage-v1-receipt=fixture.json"
    manifest.write_bytes(original_manifest)
    assert _register_fixture_manifest(project, legacy, version=1) == original_digest
    staging = tmp_path / "restore"
    staging.mkdir()
    managed = ("--archive-sha256", f"crypto={archive_digest}")
    _refused(_import_cli(project, original_digest, *managed), "frozen_archive_snapshot_missing", archive_digest)

    with store_psycopg.connect_managed(project.root) as conn:
        row = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            original, "fixture_preexisting_snapshot", "fixture:unrelated-stage-v1-archive", _FIXTURE_TIME,
            schema_version=7,
        ))
    assert row["inserted"] and row["content_sha256"] == archive_digest
    before = _inventory(project)
    assert before[archive_digest][1:5] == (
        "fixture_preexisting_snapshot", "fixture:unrelated-stage-v1-archive", _FIXTURE_TIME, 7,
    )
    _refused(_cli(project, "restore", staging, "--manifest-sha256", original_digest),
             "frozen_archive_legacy_evidence_missing", "import-original")

    from tools import d1_compare

    def unavailable_readback(connection, digest):
        raise OSError("fixture post-commit readback failed")

    monkeypatch.setattr(tempfile, "tempdir", str(project.temporary))
    with monkeypatch.context() as fault:
        fault.setattr(frozenrun, "_read_registered_staging", unavailable_readback)
        assert d1_compare.main(["import-original", "--repo-root", str(project.root),
                                "--trusted-manifest-sha256", original_digest, *managed]) == 2
    output = capsys.readouterr()
    assert not output.out and "frozen_managed_archive_read_failed" in output.err
    committed = _inventory(project)
    (digest,) = committed.keys() - before.keys()
    assert all(committed[key] == row for key, row in before.items())
    restored = _receipt(_import_cli(project, original_digest, *managed), stage_version=3)
    entry = restored["legs"]["crypto"]
    assert restored["manifest_sha256"] == digest
    assert restored["original_manifest"]["sha256"] == original_digest
    assert entry["library"] == {
        "pin": spec.pin, "commit": spec.commit, "tree": spec.tree, "subpath": "src", "format": "tar",
        "export_version": 1, "tar_umask": "0022", "archive_sha256": archive_digest,
        "archive_byte_length": len(original),
    }
    assert entry["scripts"] == expected["scripts"] and entry["bindings"] == expected["bindings"]
    assert str(sentinel) not in json.dumps(restored)
    assert _inventory(project) == committed
    assert _receipt(_cli(project, "restore", staging, "--manifest-sha256", digest), stage_version=3) == restored

    # A second independently anchored JSON uses the existing tar's first row.
    alternate_manifest = original_manifest + b"\n"
    alternate_digest = hashlib.sha256(alternate_manifest).hexdigest()
    manifest.write_bytes(alternate_manifest)
    alternate = _receipt(_import_cli(project, alternate_digest, "--manifest-file", str(manifest), *managed),
                         stage_version=3)
    assert alternate["manifest_sha256"] != digest and alternate["legs"] == restored["legs"]
    assert _inventory(project)[archive_digest] == before[archive_digest]
    _check_invalid_import_receipt(project, staging, manifest, original_manifest, restored, managed)

    compared = subprocess.run(
        [sys.executable, "-B", "-c", _COMPARE_RESTORED_BYTES, str(project.root), digest, "crypto"],
        cwd=project.root, env=_process_env(project), input=original_manifest + original,
        capture_output=True, timeout=_CLI_TIMEOUT,
    )
    assert compared.returncode == 0 and not compared.stdout and not compared.stderr
    monkeypatch.setattr(tempfile, "tempdir", str(project.temporary))
    with frozenrun.plan_chain("crypto", mirror_root=MIRROR_ROOT, staging_root=staging,
                              repo_root=project.root, manifest_sha256=digest) as plan:
        assert plan.library.archive_sha256 == archive_digest
        assert plan.as_mapping()["bindings"] == expected["bindings"]
        assert str(sentinel) not in " ".join(plan.mount_argv) and str(sentinel) not in json.dumps(plan.child_env)
        assert (plan.staging_root / plan.library.tree_path / spec.module).read_bytes() == git_bytes(
            clean_object_repo, "show", f"{spec.commit}:src/{spec.module}",
        )
    assert sentinel.read_bytes() == b"untouched fixture\n"
    _check_original_readback_failures(project, staging, restored, monkeypatch)

    wrong_v2 = _crypto_manifest()
    wrong_v2["legs"]["crypto"]["library"]["archive_sha256"] = archive_digest
    wrong = _register_fixture_manifest(project, wrong_v2)
    _refused(_cli(project, "restore", staging, "--manifest-sha256", wrong),
             "frozen_archive_manifest_binding_invalid", "crypto")
    _refused(_import_cli(project, wrong, *managed), "frozen_archive_original_manifest_schema_invalid")
    library.update(commit=spec.commit, tree="0" * 40, subpath="src")
    wrong = _register_fixture_manifest(project, legacy, version=1)
    _refused(_import_cli(project, wrong, *managed), "frozen_archive_manifest_binding_invalid", "crypto")
    library.update(tree=spec.tree, tar_umask="0002")
    wrong = _register_fixture_manifest(project, legacy, version=1)
    _refused(_import_cli(project, wrong, *managed), "frozen_library_archive_mode_mismatch", "crypto")
    _assert_no_archives_on_disk(project, staging)


def _check_invalid_import_receipt(project: NativeProject, staging: Path, manifest: Path,
                                  original_manifest: bytes, issued: dict, managed: tuple[str, str]) -> None:
    unregistered = original_manifest + b"\n\n"
    original_digest = hashlib.sha256(unregistered).hexdigest()
    manifest.write_bytes(unregistered)
    receipt = deepcopy(issued)
    del receipt["manifest_sha256"]
    receipt["original_manifest"] = {"stage_version": 1, "sha256": original_digest, "byte_length": len(unregistered)}
    content = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with store_psycopg.connect_managed(project.root) as conn:
        row = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content, _MANIFEST_KIND, "fixture:unissued-receipt", _FIXTURE_TIME, schema_version=3,
        ))
    conflict = row["content_sha256"]
    _refused(_cli(project, "restore", staging, "--manifest-sha256", conflict),
             "frozen_archive_manifest_schema_invalid")
    before = _inventory(project)
    _refused(_import_cli(project, original_digest, "--manifest-file", str(manifest), *managed),
             "frozen_archive_receipt_conflict")
    assert _inventory(project) == before and original_digest not in before

    receipt["original_manifest"] = issued["original_manifest"]
    receipt["legs"]["crypto"]["library"]["tar_umask"] = "0002"
    content = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with store_psycopg.connect_managed(project.root) as conn:
        row = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content, _MANIFEST_KIND, "d1:frozen-stage-import:v3", _FIXTURE_TIME, schema_version=3,
        ))
    _refused(_cli(project, "restore", staging, "--manifest-sha256", row["content_sha256"]),
             "frozen_archive_manifest_binding_invalid")


def _check_original_readback_failures(project: NativeProject, staging: Path, receipt: dict,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_extract(*args, **kwargs):
        pytest.fail("missing or corrupted originals reached extraction")

    original_read = snapshots.read_snapshot
    tests = (
        (receipt["original_manifest"]["sha256"], "missing", "frozen_archive_snapshot_missing"),
        (receipt["legs"]["crypto"]["library"]["archive_sha256"], "missing", "frozen_archive_snapshot_missing"),
        (receipt["original_manifest"]["sha256"], "invalid", "frozen_archive_snapshot_digest_mismatch"),
        (receipt["manifest_sha256"], "invalid", "frozen_archive_snapshot_digest_mismatch"),
    )
    for damaged_digest, condition, code in tests:
        def damaged(conn, digest):
            if digest != damaged_digest:
                return original_read(conn, digest)
            if condition == "missing":
                return None
            row = original_read(conn, digest)
            row["content"] = bytes([row["content"][0] ^ 1]) + row["content"][1:]
            return row

        with monkeypatch.context() as fault:
            fault.setattr(frozenrun, "_extract_library", unexpected_extract)
            fault.setattr(snapshots, "read_snapshot", damaged)
            with pytest.raises(frozenrun.FrozenRunError) as error:
                frozenrun.restore_archives(receipt["manifest_sha256"], repo_root=project.root, staging_root=staging)
            assert error.value.code == code


def test_execution_refuses_a_locally_rehashed_mirror_outside_the_sealed_binding(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(native_project.temporary))
    (relative_path,) = CRYPTO_DESIGNATED
    mirror = tmp_path / "mirror"
    staged = tmp_path / "staging"
    revised = mirror / relative_path
    revised.parent.mkdir(parents=True)
    content = (MIRROR_ROOT / relative_path).read_bytes() + b"\n# fixture revision\n"
    revised.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (mirror / "MANIFEST.sha256").write_text(
        f"{digest}\t{len(content)}\t0\t{relative_path}\n", encoding="utf-8",
    )
    with pytest.raises(frozenrun.FrozenRunError) as error:
        with frozenrun.plan_chain("crypto", mirror_root=mirror, staging_root=staged,
                                  repo_root=native_project.root, manifest_sha256=staged_crypto):
            pytest.fail("rehashed mirror reached the execution scope")
    assert error.value.code == "frozen_archive_mirror_binding_mismatch"
    _assert_no_archives_on_disk(native_project, staged)


@pytest.mark.parametrize(("field", "code"), [
    ("content", "frozen_archive_snapshot_digest_mismatch"),
    ("byte_length", "frozen_archive_snapshot_length_mismatch"),
])
def test_readback_detects_tar_integrity_failures_before_extraction(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    field: str, code: str,
) -> None:
    original_read = snapshots.read_snapshot

    def corrupted(connection, digest):
        row = original_read(connection, digest)
        if digest == FROZEN_LIBRARIES["crypto"].digest:
            if field == "content":
                original = row[field]
                row[field] = bytes([original[0] ^ 1]) + original[1:]
            else:
                row[field] += 1
        return row

    def unexpected_extract(*args, **kwargs):
        pytest.fail("corrupt managed bytes reached tar extraction")

    monkeypatch.setattr(snapshots, "read_snapshot", corrupted)
    monkeypatch.setattr(tarfile.TarFile, "extractall", unexpected_extract)
    monkeypatch.setattr(tempfile, "tempdir", str(native_project.temporary))
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.restore_archives(staged_crypto, repo_root=native_project.root, staging_root=tmp_path)
    assert error.value.code == code and error.value.detail == "crypto"
    _assert_no_archives_on_disk(native_project, tmp_path)


@pytest.mark.parametrize(("exit_case", "exception"), [
    ("empty", None), ("callback_error", RuntimeError), ("interrupt", KeyboardInterrupt),
    ("nonzero_child", subprocess.CalledProcessError), ("timeout", subprocess.TimeoutExpired),
])
def test_execution_scope_releases_inputs_on_all_callback_exits(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    exit_case: str, exception: type[BaseException] | None,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(native_project.temporary))
    seen = []

    def fixture_executor(plan):
        seen.append(plan.staging_root)
        assert (plan.staging_root / plan.library.tree_path / FROZEN_LIBRARIES["crypto"].module).is_file()
        if exit_case == "callback_error":
            raise RuntimeError("fixture callback failed")
        if exit_case == "interrupt":
            raise KeyboardInterrupt
        if exit_case == "nonzero_child":
            plan.run_child([sys.executable, "-IB", "-c", "raise SystemExit(19)"], timeout=5)
        if exit_case == "timeout":
            plan.run_child([sys.executable, "-IB", "-c", "import time; time.sleep(30)"], timeout=0.2)

    options = {
        "mirror_root": MIRROR_ROOT, "staging_root": tmp_path, "repo_root": native_project.root,
        "manifest_sha256": staged_crypto, "_executor": fixture_executor,
        "authorization": frozenrun.DayRunAuthorization.from_mapping({
            gate: "synthetic-fixture" for gate in frozenrun.REQUIRED_RUN_GATES
        }),
    }
    if exception is None:
        assert frozenrun.execute_designated_chain("crypto", **options) is None
    else:
        with pytest.raises(exception) as error:
            frozenrun.execute_designated_chain("crypto", **options)
        if exit_case == "nonzero_child":
            assert error.value.returncode == 19
    assert len(seen) == 1 and not seen[0].exists()
    _assert_no_archives_on_disk(native_project, tmp_path)


def test_partial_extraction_failure_drops_the_work_tree(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(native_project.temporary))
    extract_paths = []

    def partial_extract(self, path, *, filter):
        assert filter == "data"
        destination = Path(path)
        destination.mkdir(parents=True)
        (destination / "partial-fixture.py").touch()
        extract_paths.append(destination)
        raise OSError("fixture interruption during extraction")

    monkeypatch.setattr(tarfile.TarFile, "extractall", partial_extract)
    with pytest.raises(frozenrun.FrozenRunError) as error:
        frozenrun.restore_archives(staged_crypto, repo_root=native_project.root, staging_root=tmp_path)
    assert error.value.code == "frozen_library_extraction_failed"
    assert len(extract_paths) == 1 and not extract_paths[0].exists()
    _assert_no_archives_on_disk(native_project, tmp_path)


def test_run_frozen_refuses_before_storage_or_work_creation(native_project: NativeProject, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    _refused(_cli(native_project, "run-frozen", staging, "--leg", "crypto"), "g7_fix_evidence")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({gate: "synthetic-fixture" for gate in frozenrun.REQUIRED_RUN_GATES}),
                             encoding="utf-8")
    _refused(_cli(native_project, "run-frozen", staging, "--leg", "crypto",
                  "--authorization", str(authorization), "--manifest-sha256", "0" * 64),
             "frozen_run_executor_not_wired")
    _assert_no_archives_on_disk(native_project, staging)


_PAUSE_CLI_EXTRACTION = """
from pathlib import Path
import runpy
import signal
import sys
import tarfile

ready = Path(sys.argv[1])

def extract_partial(self, path, *, filter):
    destination = Path(path)
    destination.mkdir(parents=True)
    (destination / 'partial-fixture.py').touch()
    marker = ready.with_suffix('.pending')
    marker.write_text(str(destination), encoding='utf-8')
    marker.rename(ready)
    signal.pause()

tarfile.TarFile.extractall = extract_partial
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def _ready_marker(path: Path, process: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 45
    while not path.is_file() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.025)
    if not path.is_file():
        pytest.fail(f"D1 fixture process failed to enter its work scope (status {process.poll()})", pytrace=False)
    return path.read_text(encoding="utf-8")


def test_sigterm_unwinds_the_cli_extraction_scope(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path,
) -> None:
    project = native_project
    staging = tmp_path / "staging"
    ready = tmp_path / "extracting"
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", _PAUSE_CLI_EXTRACTION, str(ready),
         str(REPO_ROOT / "tools/d1_compare.py"), "restore", "--repo-root", str(project.root),
         "--staging-root", str(staging), "--manifest-sha256", staged_crypto],
        cwd=project.root, env=_process_env(project), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        destination = Path(_ready_marker(ready, process))
        assert (destination / "partial-fixture.py").is_file()
        process.terminate()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 128 + signal.SIGTERM
        assert not stdout and not stderr
        assert not destination.exists()
        _assert_no_archives_on_disk(project, staging)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


_HOLD_LEASED_WORKER = """
from pathlib import Path
import sys
from polymarket_alpha_lab.papersim.d1compare import frozenrun

root, mirror, staging, digest, module, ready, release, done, child_source = sys.argv[1:]
with frozenrun.plan_chain('crypto', mirror_root=Path(mirror), staging_root=Path(staging),
                          repo_root=Path(root), manifest_sha256=digest) as plan:
    leaf = plan.staging_root
    sample = leaf / plan.library.tree_path / module
    child = plan.run_child([sys.executable, '-IB', '-c', child_source, str(leaf), str(sample),
                            ready, release, done], timeout=150)
    assert not child.stdout and not child.stderr
"""

_LEASED_CHILD = """
import json
import os
from pathlib import Path
import sys
import time

leaf, module, ready, release, done = map(Path, sys.argv[1:])
assert module.is_file()
marker = ready.with_suffix('.pending')
marker.write_text(json.dumps({'work': str(leaf), 'pid': os.getpid()}), encoding='utf-8')
marker.rename(ready)
deadline = time.monotonic() + 150
while not release.is_file() and time.monotonic() < deadline:
    time.sleep(0.025)
assert release.is_file() and module.is_file()
done.touch()
"""


@dataclass(frozen=True)
class HeldWorker:
    process: subprocess.Popen[bytes]
    leaf: Path
    child_fd: int
    release: Path
    done: Path

    def complete_child(self) -> None:
        self.release.touch()
        assert select.select([self.child_fd], [], [], 15)[0]
        assert self.done.is_file()


@contextmanager
def _hold_worker(project: NativeProject, staging: Path, digest: str, latch: Path):
    latch.mkdir()
    ready, release, done = (latch / marker for marker in ("ready", "release", "done"))
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", _HOLD_LEASED_WORKER,
         str(project.root), str(MIRROR_ROOT), str(staging), digest,
         FROZEN_LIBRARIES["crypto"].module, str(ready), str(release), str(done), _LEASED_CHILD],
        cwd=project.root, env=_process_env(project), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    child_fd = None
    try:
        marker = json.loads(_ready_marker(ready, process))
        child_fd = os.pidfd_open(marker["pid"])
        yield HeldWorker(process, Path(marker["work"]), child_fd, release, done)
    finally:
        release.touch()
        if child_fd is not None:
            try:
                if not select.select([child_fd], [], [], 10)[0]:
                    signal.pidfd_send_signal(child_fd, signal.SIGKILL)
            finally:
                os.close(child_fd)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


def test_crash_recovery_reaps_only_unleased_work_after_sigkill(
    native_project: NativeProject, staged_crypto: str, tmp_path: Path,
) -> None:
    project = native_project
    staging = tmp_path / "caller-staging"
    unrelated = staging / "call-00000000"
    unrelated.mkdir(parents=True, mode=0o700)
    sentinel = unrelated / "other-task"
    sentinel.touch()
    before = _inventory(project)
    with (_hold_worker(project, staging, staged_crypto, tmp_path / "crashing-worker") as crashed,
          _hold_worker(project, staging, staged_crypto, tmp_path / "other-worker") as other):
        assert crashed.leaf != other.leaf
        assert crashed.leaf.parent == other.leaf.parent
        assert all(stat.S_IMODE(leaf.stat().st_mode) == 0o700 for leaf in (crashed.leaf, other.leaf))
        crashed.process.kill()
        assert crashed.process.wait(timeout=15) == -signal.SIGKILL
        assert not select.select([crashed.child_fd], [], [], 0)[0]

        receipt = _receipt(_cli(project, "restore", staging, "--manifest-sha256", staged_crypto))
        assert receipt["manifest_sha256"] == staged_crypto
        assert crashed.leaf.is_dir() and other.leaf.is_dir()

        crashed.complete_child()
        assert crashed.leaf.is_dir()
        assert _receipt(_cli(project, "restore", staging, "--manifest-sha256", staged_crypto)) == receipt
        assert not crashed.leaf.exists() and other.leaf.is_dir()
        other.complete_child()
        stdout, stderr = other.process.communicate(timeout=15)
        assert other.process.returncode == 0 and not stdout and not stderr
    assert not other.leaf.exists()
    assert set(staging.rglob("*")) == {unrelated, sentinel}
    assert not list(project.temporary.glob("polymarket-d1-frozen-*/call-*"))
    assert _inventory(project) == before
