#!/usr/bin/env python3
"""Bundle the minimum frozen inputs needed to reproduce the basketball study offline."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fetch_sources import CLOB, LEAGUES, write_json


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def compact_odds_archive(path):
    package = io.BytesIO()
    members = {}
    with ZipFile(path) as raw, ZipFile(package, 'w') as trimmed:
        for basename in ('games.csv', 'closing_odds.csv'):
            data = raw.read(basename)
            item = ZipInfo(basename, date_time=(2026, 11, 19, 0, 0, 0))
            trimmed.writestr(item, data, compress_type=ZIP_DEFLATED, compresslevel=9)
            members[basename] = {'bytes': len(data), 'sha256': sha256(data)}
    return package.getvalue(), members


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--club-clocks', required=True, type=Path)
    args = parser.parse_args()
    published = json.loads((args.cache / 'source-urls.json').read_text())
    names = set(published) | {'source-urls.json'}
    origin = {f'prices-{sport}-extended.json': f'{CLOB}/batch-prices-history'
              for sport, _ in LEAGUES.values()}
    for sport, _ in LEAGUES.values():
        names.update((f'gamma-{sport}.json', f'gamma-{sport}-queries.json', f'prices-{sport}-extended.json'))
        origin[f'gamma-{sport}.json'] = f'gamma-{sport}-queries.json'
    paths = {name: args.cache / name for name in names}
    paths['clock_inputs/germany_bbl_bcl_verified_clocks.csv'] = args.output / 'germany_bbl_bcl_verified_clocks.csv'
    paths['clock_inputs/club_basketball_verified_clocks.csv'] = args.club_clocks
    origin['clock_inputs/germany_bbl_bcl_verified_clocks.csv'] = 'BetExplorer SportsEvent JSON-LD on match URLs recorded in rows'
    origin['clock_inputs/club_basketball_verified_clocks.csv'] = 'BetExplorer SportsEvent JSON-LD on match URLs recorded in rows'
    sources = {}
    output_path = args.output / 'frozen_inputs.tar.gz'
    with output_path.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as gzipped:
        with tarfile.open(fileobj=gzipped, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
            for name, path in sorted(paths.items()):
                content = path.read_bytes()
                original_sha = sha256(content)
                source = {'source': published.get(name) or origin.get(name, 'capture metadata')}
                if name == 'european-odds.zip':
                    content, members = compact_odds_archive(path)
                    source.update(original_archive_sha256=original_sha, original_members=members)
                item = tarfile.TarInfo(name)
                item.size = len(content)
                item.mtime = 1795046400
                item.mode = 0o644
                archive.addfile(item, io.BytesIO(content))
                sources[name] = {**source, 'bytes': len(content), 'sha256': sha256(content)}
    manifest = {'cutoff_utc': '2026-11-19T00:00:00+00:00',
                'file': output_path.name, 'archive_bytes': output_path.stat().st_size,
                'archive_sha256': sha256(output_path.read_bytes()), 'files': sources}
    write_json(args.output / 'source_manifest.json', manifest)
    print(output_path, manifest['archive_bytes'], manifest['archive_sha256'], len(sources), 'files')


if __name__ == '__main__':
    main()
