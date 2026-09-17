"""Text-only reconstruction of pinned existing PR47 edits; no project imports."""
from pathlib import Path
import subprocess, tempfile
BASE='2a0e881a2057ce6cba33c1a6885dcd099d4a5b52'
OLD='f1d9f68d08fdb8a0453319e885c3827296d8d694'
COMMON='4df08873f4b9b8f1378913c0d00ca6bb183e51e2'
def read(ref,path):
    return subprocess.check_output(['git','show',ref+':'+path])
updates={}
for path in ('tests/packaged_research_flow.py', 'tests/test_research_dispatch_cli_admission.py',
             'tests/test_research_dispatch_cli_admission_review.py',
             'src/polymarket_alpha_lab/research_dispatch_cli.py'):
    updates[path]=read(OLD,path)
for path in ('tests/test_project_postgres_distribution_native.py','database/quickstart.md'):
    with tempfile.TemporaryDirectory() as directory:
        files=[Path(directory)/name for name in ('current','common','old')]
        for p,ref in zip(files,(BASE,COMMON,OLD)):
            p.write_bytes(read(ref,path))
        result=subprocess.run(['git','merge-file','-p',*map(str,files)],capture_output=True)
        if result.returncode:
            raise RuntimeError('pinned source merge conflict')
        updates[path]=result.stdout
for path in ('DELIVERY_PLAN.md','docs/research-dispatch.md'):
    common,old,current=(read(ref,path) for ref in (COMMON,OLD,BASE))
    if not old.startswith(common):
        raise RuntimeError('original append boundary changed')
    extra=old[len(common):]
    if path=='DELIVERY_PLAN.md':
        extra=extra.replace(b'## 25.',b'## 28.',1)
    updates[path]=current+extra
for path,raw in updates.items():
    Path(path).write_bytes(raw)
subprocess.run(['git','add','--',*updates],check=True)
