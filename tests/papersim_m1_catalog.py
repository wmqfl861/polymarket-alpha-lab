"""Shared frozen M1 papersim catalog fixture (M2 plan section 6.3).

The M1 migration-history proofs keep running against the frozen TWO-entry
M1 catalog (two migrations, nine tables) with their original assertions
unchanged; the current full catalog (grown beyond the frozen pair since
S0 and V2) is proven independently in ``tests/test_papersim_m2_migrations.py``
so no current-version validation hides behind this historical fixture.

The pinned sha256 values below are the published M1 migration bytes from
``database/papersim/migrations.lock.json`` at the M1 exit commit. The
writer copies the two files from the live repository, verifies every byte
against the pinned digests and materializes a minimal repo tree holding
exactly the frozen catalog. If an M1 file ever drifts, the pinned-hash
assertion fails loudly instead of silently re-blessing new bytes.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from hashlib import sha256
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIRECTORY = ROOT / 'database' / 'papersim' / 'migrations'
MANIFEST_PATH = ROOT / 'database' / 'papersim' / 'migrations.lock.json'

M1_MIGRATIONS = (
    ('20261115000000_papersim_bootstrap.sql',
     'ef0efa36b4fee775473611f5b73f1d34fe20cb4f13c26608a2aac562d9dcc74c'),
    ('20261115000100_papersim_v2_core.sql',
     '2d5b4f4407d59189d7cfa896c45884efc0c88a24bc3fee403cd18fb070b94945'),
)
M1_MIGRATION_COUNT = len(M1_MIGRATIONS)


def write_frozen_m1_catalog(destination: Path, *, repo_root: Path = ROOT) -> Path:
    """Materialize the frozen two-entry M1 catalog tree below ``destination``.

    Returns the tree root. The copied M1 bytes must match the pinned
    digests exactly (the manifest of the live repository must still carry
    the same two entries first).
    """
    tree = Path(destination)
    directory = tree / 'database' / 'papersim' / 'migrations'
    directory.mkdir(parents=True)
    entries = []
    for name, pinned in M1_MIGRATIONS:
        source = Path(repo_root) / 'database' / 'papersim' / 'migrations' / name
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != pinned:
            raise AssertionError('frozen M1 migration bytes drifted: ' + name)
        (directory / name).write_bytes(raw)
        entries.append({'name': name, 'sha256': pinned, 'transaction_wrapper': True})
    (tree / 'database' / 'papersim' / 'migrations.lock.json').write_text(
        json.dumps({'format': 'papersim-migrations-v1', 'migrations': entries}, indent=2)
        + '\n', encoding='utf-8')
    return tree


@pytest.fixture
def frozen_m1_catalog(tmp_path) -> Path:
    """A frozen M1 catalog tree for one test (M2 plan section 6.3)."""
    return write_frozen_m1_catalog(tmp_path / 'repo')
