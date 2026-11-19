"""Shared frozen pre-FX26 papersim catalog fixture (FX-26, 2026-11-19 fix-list).

The four-entry migration-history proofs keep running against the frozen
pre-FX26 catalog (the frozen M1 pair plus the M2 operations and the
M3/V2 reporting-views migrations) with their original assertions
unchanged; the current full catalog (five entries since the FX-26
candidate event-keys migration) is proven independently in
``tests/test_papersim_fx26_migrations.py`` so no current-version
validation hides behind this historical fixture.

The pinned sha256 values below are the published migration bytes from
``database/papersim/migrations.lock.json`` before the FX-26 entry was
appended (the manifest carried exactly these four entries first). The
writer copies the four files from the live repository, verifies every
byte against the pinned digests and materializes a minimal repo tree
holding exactly the frozen catalog. If a frozen file ever drifts, the
pinned-hash assertion fails loudly instead of silently re-blessing new
bytes.
"""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from tests.papersim_m1_catalog import M1_MIGRATIONS

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIRECTORY = ROOT / 'database' / 'papersim' / 'migrations'
MANIFEST_PATH = ROOT / 'database' / 'papersim' / 'migrations.lock.json'

M2_V2_PAIR = (
    ('20261116000000_papersim_m2_operations.sql',
     '428a8fd5ebf84f945dc4ae78d02f99f61ba39b8743c0bd78b2380e67a013df2d'),
    ('20261116000010_papersim_reporting_views.sql',
     '87b802b547f1ffc1d25fa3c8cb401ef643fc5a34f35e6450e5bc56261af1a293'),
)
#: The complete pre-FX26 catalog: the frozen M1 pair plus M2 and M3/V2.
FX26_FROZEN_CATALOG = M1_MIGRATIONS + M2_V2_PAIR
FX26_FROZEN_COUNT = len(FX26_FROZEN_CATALOG)


def write_frozen_fx26_catalog(destination: Path, *, repo_root: Path = ROOT) -> Path:
    """Materialize the frozen four-entry pre-FX26 catalog tree below destination.

    Returns the tree root. The copied bytes must match the pinned digests
    exactly (the live manifest must still carry the same four entries
    first).
    """
    tree = Path(destination)
    directory = tree / 'database' / 'papersim' / 'migrations'
    directory.mkdir(parents=True)
    entries = []
    for name, pinned in FX26_FROZEN_CATALOG:
        source = Path(repo_root) / 'database' / 'papersim' / 'migrations' / name
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != pinned:
            raise AssertionError('frozen pre-FX26 migration bytes drifted: ' + name)
        (directory / name).write_bytes(raw)
        entries.append({'name': name, 'sha256': pinned, 'transaction_wrapper': True})
    (tree / 'database' / 'papersim' / 'migrations.lock.json').write_text(
        json.dumps({'format': 'papersim-migrations-v1', 'migrations': entries}, indent=2)
        + '\n', encoding='utf-8')
    return tree


@pytest.fixture
def frozen_fx26_catalog(tmp_path) -> Path:
    """A frozen pre-FX26 (four-entry) catalog tree for one test."""
    return write_frozen_fx26_catalog(tmp_path / 'repo')
