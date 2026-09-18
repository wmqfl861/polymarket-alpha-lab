"""Reconstruct only pinned source text; never import or execute project code."""
from pathlib import Path
import subprocess, sys, tempfile
BASE = 'e1e47df60a59207575b7a266902e0a99ced9d283'
OLD = '061a55703197cc526f3d707d9da58b971c582e0b'
COMMON = '3a987bc6c1a5df608ff8f0668b866aaf87842c04'
extra = Path(sys.argv[1])
def read(ref, path):
    return subprocess.check_output(['git', 'show', ref + ':' + path])
assert subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip() == BASE
updates = {}
for path in ('src/polymarket_alpha_lab/project_postgres/server.py',
             'tests/test_project_postgres_distribution_native.py'):
    with tempfile.TemporaryDirectory() as folder:
        files = [Path(folder) / name for name in ('current', 'common', 'old')]
        for file, ref in zip(files, (BASE, COMMON, OLD)):
            file.write_bytes(read(ref, path))
        result = subprocess.run(['git', 'merge-file', '-p', *map(str, files)], capture_output=True)
        if result.returncode:
            raise RuntimeError('unexpected pinned merge conflict: ' + path)
        updates[path] = result.stdout
for path in ('tests/test_project_postgres_session_bundle.py',
             'tests/test_project_postgres_session_bundle_review.py'):
    updates[path] = read(OLD, path)
for path in ('DELIVERY_PLAN.md', 'database/quickstart.md'):
    common, old, current = (read(ref, path) for ref in (COMMON, OLD, BASE))
    assert old.startswith(common), 'unexpected append boundary'
    tail = old[len(common):]
    if path == 'DELIVERY_PLAN.md':
        assert tail.count(b'## 26.') == 1
        tail = tail.replace(b'## 26.', b'## 36.', 1)
    updates[path] = current + tail
for path, name in (
    ('DELIVERY_PLAN.md', 'plan-addendum.txt'),
    ('database/quickstart.md', 'quickstart-addendum.txt'),
    ('tests/test_project_postgres_session_bundle_review.py', 'test-addendum.txt'),
):
    updates[path] += (extra / name).read_bytes()
assert len(updates) == 6
for path, raw in updates.items():
    Path(path).write_bytes(raw)
subprocess.run(['git', 'add', '--', *updates], check=True)
