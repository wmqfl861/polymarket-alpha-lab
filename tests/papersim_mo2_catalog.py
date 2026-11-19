"""Shared frozen pre-MO2 papersim catalog fixture (MO-2, 2026-11-19
audit-optimizer 05-development-plan node MO-2; fix-list rule 0.3).

The five-entry FX-26-era migration-history proofs keep running against
the frozen pre-MO2 catalog (the frozen pre-FX26 four-entry history
plus the FX-26 candidate event-keys migration) with their original
assertions unchanged; the current full catalog (six entries since the
MO-2 model-registry migration) is proven independently in
``tests/test_papersim_mo2_migrations.py`` so no current-version
validation hides behind this historical fixture.

The pinned sha256 values below are the published migration bytes from
``database/papersim/migrations.lock.json`` before the MO-2 entry was
appended (the manifest carried exactly these five entries first). The
writer copies the five files from the live repository, verifies every
byte against the pinned digests and materializes a minimal repo tree
holding exactly the frozen catalog. If a frozen file ever drifts, the
pinned-hash assertion fails loudly instead of silently re-blessing new
bytes.
"""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from tests.papersim_fx26_catalog import FX26_FROZEN_CATALOG

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIRECTORY = ROOT / 'database' / 'papersim' / 'migrations'
MANIFEST_PATH = ROOT / 'database' / 'papersim' / 'migrations.lock.json'

#: The FX-26 migration as published by the merged FX-26 node (its
#: pinned pair is re-pinned here from the manifest history, not
#: imported from the FX-26 test module, so this fixture depends on the
#: shared pre-FX26 fixture only).
FX26_MIGRATION = ('20261119173000_papersim_candidate_event_keys.sql',
                  'e29507cec4a5199d85877a33003b487a8c7bc917c42fc561607dfe5419ca45ce')

#: The complete pre-MO2 catalog: the frozen pre-FX26 four-entry
#: history, FX-26 and nothing else.
MO2_FROZEN_CATALOG = FX26_FROZEN_CATALOG + (FX26_MIGRATION,)
MO2_FROZEN_COUNT = len(MO2_FROZEN_CATALOG)


def write_frozen_mo2_catalog(destination: Path, *, repo_root: Path = ROOT) -> Path:
    """Materialize the frozen five-entry pre-MO2 catalog tree below destination.

    Returns the tree root. The copied bytes must match the pinned
    digests exactly (the live manifest must still carry the same five
    entries first).
    """

    tree = Path(destination)
    directory = tree / 'database' / 'papersim' / 'migrations'
    directory.mkdir(parents=True)
    entries = []
    for name, pinned in MO2_FROZEN_CATALOG:
        source = Path(repo_root) / 'database' / 'papersim' / 'migrations' / name
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != pinned:
            raise AssertionError('frozen pre-MO2 migration bytes drifted: ' + name)
        (directory / name).write_bytes(raw)
        entries.append({'name': name, 'sha256': pinned, 'transaction_wrapper': True})
    (tree / 'database' / 'papersim' / 'migrations.lock.json').write_text(
        json.dumps({'format': 'papersim-migrations-v1', 'migrations': entries}, indent=2)
        + '\n', encoding='utf-8')
    return tree


@pytest.fixture
def frozen_mo2_catalog(tmp_path) -> Path:
    """A frozen pre-MO2 (five-entry) catalog tree for one test."""

    return write_frozen_mo2_catalog(tmp_path / 'repo')
