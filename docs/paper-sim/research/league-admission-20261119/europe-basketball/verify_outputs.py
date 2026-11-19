#!/usr/bin/env python3
"""Check the frozen files, timing invariants, and a fresh offline replay."""

import argparse
import csv
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import tarfile

from fetch_sources import instant, write_json


PREDICTION_FILES = (
    'game_predictions.csv', 'prediction_metrics.json', 'polymarket_market_replay.csv',
    'market_metrics.json', 'sportsbook_closing_moneyline_comparison.csv',
    'sportsbook_france_pro_b_timed_totals.csv',
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def csv_records(path):
    with path.open(encoding='utf8') as file:
        return list(csv.DictReader(file))


def check_frozen_archive(output):
    manifest = json.loads((output / 'source_manifest.json').read_text())
    frozen = output / manifest['file']
    assert frozen.stat().st_size == manifest['archive_bytes']
    assert digest(frozen) == manifest['archive_sha256']
    with tarfile.open(frozen) as archive:
        members = archive.getmembers()
        assert sorted(member.name for member in members) == sorted(manifest['files'])
        for member in members:
            details = manifest['files'][member.name]
            assert member.isfile() and member.size == details['bytes']
            assert sha256(archive.extractfile(member).read()).hexdigest() == details['sha256']
    return len(members)


def check_rows(output):
    predictions = csv_records(output / 'game_predictions.csv')
    by_key = {(row['league'], row['game_key']): row for row in predictions}
    assert len(predictions) == len(by_key)
    for row in predictions:
        last = instant(row['last_result_available_utc'])
        cutoff = instant(row['prediction_time_utc'])
        assert not last or last <= cutoff
    markets = csv_records(output / 'polymarket_market_replay.csv')
    matched = [row for row in markets if row['status'] == 'matched price history']
    assert len({(r['league'], r['market'], r['game_key']) for r in matched}) == len(matched)
    for row in matched:
        assert row['rule_explicitly_includes_overtime'] == '1' and row['settlement_agrees_with_scoreboard'] == '1'
        times = [instant(row[key]) for key in ('source_tip_utc', 'tip_utc', 'event_tip_utc')]
        anchor = instant(row['anchor_utc'])
        assert row['independent_tip_source'] and all(times)
        assert max(times) - min(times) <= timedelta(minutes=15)
        assert anchor == min(times) - timedelta(hours=1)
        assert anchor < instant(row['event_closed_utc'])
        a, b = [instant(row[key]) for key in ('selected_price_utc', 'other_price_utc')]
        for timestamp, value in ((a, row['age_a_seconds']), (b, row['age_b_seconds'])):
            age = anchor - timestamp
            assert timedelta(0) <= age <= timedelta(minutes=15)
            assert age.total_seconds() == int(value)
        assert abs(a - b) <= timedelta(minutes=5)
        actual = float(row['selected_price']) / (float(row['selected_price']) + float(row['other_price']))
        assert abs(actual - float(row['market_p'])) < 1e-12
        assert instant(by_key[row['league'], row['game_key']]['prediction_time_utc']) <= anchor
    assert not any(row['league'] == 'EuroLeague' and row['market'] == 'moneyline' for row in matched)
    italy = [row for row in markets if row['league'] == 'Italy Serie A'
             and row['date'] == '2026-05-10' and row['market'] == 'moneyline']
    assert len(italy) == 7
    spain = [row for row in markets if row['market_slug'] == 'bkligend-bas2-gra-2026-05-09']
    assert len(spain) == 1
    for row in [*italy, *spain]:
        assert row['status'] == 'source tip times differ by more than 15 min'
        assert float(row['scoreboard_tip_delta_minutes']) == 180
    return {'game_rows': len(predictions), 'market_rows': len(markets), 'strict_contracts': len(matched),
            'strict_independent_matches': len({(r['league'], r['game_key']) for r in matched}),
            'excluded_italy_clock_conflicts_may_10': len(italy), 'excluded_zaragoza_granada_clock_conflicts': len(spain)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('reproduced', type=Path)
    args = parser.parse_args()
    count = check_frozen_archive(args.output)
    records = check_rows(args.output)
    reproduced = {}
    for name in PREDICTION_FILES:
        baseline = digest(args.output / name)
        assert baseline == digest(args.reproduced / name), name
        reproduced[name] = baseline
    write_json(args.output / 'verification.json', {'frozen_input_files_verified': count, **records,
                                                  'offline_reproduction_sha256': reproduced})
    print(count, records, 'offline hashes match')


if __name__ == '__main__':
    main()
