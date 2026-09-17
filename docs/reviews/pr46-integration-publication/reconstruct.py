"""Assemble seven pinned test/doc files; no application import or execution."""
from pathlib import Path
import subprocess
BASE='41cc131d154a8a134d46608f5c5da1ca1e4ddaa6'
OLD='14c1bea70cfce3f3ae2dff40f93f5868f439933c'
COMMON='4df08873f4b9b8f1378913c0d00ca6bb183e51e2'
def read(ref,path):
    return subprocess.check_output(['git','show',ref+':'+path]).decode('utf-8')
updates={}
for path in ('tests/packaged_recovery_flow.py','tests/test_packaged_recovery_flow.py',
             'tests/test_packaged_recovery_flow_review.py'):
    updates[path]=read(OLD,path)
path='database/quickstart.md'
common,old,current=(read(ref,path) for ref in (COMMON,OLD,BASE))
assert old.startswith(common)
updates[path]=current+old[len(common):]
path='DELIVERY_PLAN.md'
old,current=read(OLD,path),read(BASE,path)
assert old.count('\n\n## 25. WP-06')==1
updates[path]=current+old[old.index('\n\n## 25. WP-06'):].replace('## 25.','## 29.',1)
path='tests/packaged_research_flow.py'
current=read(BASE,path)
needle='same_confirmation_replayed=True, admission_receipts=len(admitted))'
assert current.count(needle)==1
updates[path]=current.replace(needle,"same_confirmation_replayed=True, admission_receipts=len(admitted),\n            historical_at=after['history']['generated_at'])")
path='tests/test_project_postgres_distribution_native.py'
old,current=read(OLD,path),read(BASE,path)
start=old.index('        # Extend this same disposable kit')
end=old.index('        # A changed immutable package',start)
needle='        # A changed immutable package blocks without modifying stored research.'
assert current.count(needle)==1
updates[path]=current.replace(needle,old[start:end]+needle)
for path,text in updates.items():
    Path(path).write_bytes(text.encode('utf-8'))
subprocess.run(['git','add','--',*updates],check=True)
