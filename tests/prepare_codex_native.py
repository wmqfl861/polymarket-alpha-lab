"""CI-only public pinned binary download. Never execute it or obtain credentials."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import urllib.request
import zipfile


def main():
    root=Path(os.environ['RUNNER_TEMP'])/'pal-codex-pinned'
    root.mkdir(exist_ok=False)
    target='x86_64-pc-windows-msvc' if os.name=='nt' else 'x86_64-unknown-linux-musl'
    name='codex-'+target+('.exe.zip' if os.name=='nt' else '.tar.gz')
    with urllib.request.urlopen('https://api.github.com/repos/openai/codex/releases/tags/rust-v0.155.1',timeout=60) as response:
        release=json.load(response)
    assert release['id']==391752266 and release['tag_name']=='rust-v0.155.1'
    asset,=[a for a in release['assets'] if a['name']==name]
    pinned = ((573353470, 'ce2269bdb7dfc06bb85c014c9c4e6b1601ffa0d646a2ae5b9b8cc8a427ef61fb')
              if os.name=='nt' else
              (573353448, 'a0ef8b2debc3bf747e07b1a039354de31300ac0dcc2276498ba281470b5d9115'))
    assert (asset['id'], asset['digest']) == (pinned[0], 'sha256:'+pinned[1])
    url=asset['browser_download_url']
    assert url=='https://github.com/openai/codex/releases/download/rust-v0.155.1/'+name
    assert 1<=asset['size']<=200000000 and asset['digest'].startswith('sha256:')
    with urllib.request.urlopen(url,timeout=120) as response:
        data=response.read(200000001)
    assert len(data)==asset['size'] and 'sha256:'+hashlib.sha256(data).hexdigest()==asset['digest']
    expected='codex-'+target+('.exe' if os.name=='nt' else '')
    if os.name=='nt':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.testzip() is None
            member,=[m for m in archive.infolist() if Path(m.filename).name==expected]
            assert not member.is_dir() and member.file_size<=536870912
            binary=archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            member,=[m for m in archive.getmembers() if Path(m.name).name==expected]
            assert member.isfile() and member.size<=536870912
            binary=archive.extractfile(member).read()
    expected_binary=('eba0f32c976667cb9298efafd98513e823eeda7b576a03ec658bb8be8d336316'
                     if os.name=='nt' else '0753dfe1d8b87a52436deb13eb1c549661ef4c84fee2c5aa688385eebeccb761')
    assert hashlib.sha256(binary).hexdigest()==expected_binary
    executable=root/('codex.exe' if os.name=='nt' else 'codex')
    executable.write_bytes(binary);executable.chmod(0o700)
    # Metadata only is uploadable. No native auth/state/DB directory is uploaded.
    proof=dict(release_id=release['id'],version='0.155.1',asset_id=asset['id'],asset_name=name,
               archive_sha256=hashlib.sha256(data).hexdigest(),binary_sha256=hashlib.sha256(binary).hexdigest())
    (Path(os.environ['RUNNER_TEMP'])/'codex-native-asset.json').write_text(json.dumps(proof,indent=2))
    with open(os.environ['GITHUB_ENV'],'a',encoding='utf-8') as environment:
        environment.write('POLYMARKET_ALPHA_LAB_TEST_CODEX_BINARY='+str(executable)+'\n')
    print('Pinned public CLI archive verified; binary not executed by download helper.')


if __name__=='__main__':main()
