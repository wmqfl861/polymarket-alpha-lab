"""Pinned-source, no-model/no-database engineering preview. Public GETs opt-in."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

COMMIT = 'cb8b276858c0c1c68de782aa18b930c1ed812a31'
TREE = '981ccef702f78cffc3bc947509e3adca8d2abc5f'
MAX_BODY = 4 * 1024 * 1024
TEAMS = (('crypto_btc', 'Bitcoin', r'\b(bitcoin|btc)\b'),
         ('crypto_eth', 'Ethereum', r'\b(ethereum|eth)\b'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=20)
    if result.returncode:
        raise ValueError('source_git_check_failed')
    return result.stdout.strip()


def candidates(body: object, pattern: str, now: datetime) -> list[tuple[datetime, str, str]]:
    if type(body) is not dict or type(body.get('events')) is not list:
        raise ValueError('discovery_shape_invalid')
    result = []
    for event in body['events']:
        if type(event) is not dict or type(event.get('markets')) is not list:
            continue
        for market in event['markets']:
            try:
                labels = market['outcomes']
                if type(labels) is str:
                    labels = json.loads(labels)
                end = datetime.fromisoformat(market['endDate'].replace('Z', '+00:00'))
                question = market['question']
                if (market.get('active') is not True or market.get('closed') is not False
                        or type(labels) is not list or len(labels) != 2 or set(labels) != {'Yes', 'No'}
                        or type(question) is not str or not re.search(pattern, question, re.I)
                        or not re.search(r'above|below|price|reach|hit|\$', question, re.I)
                        or end.tzinfo is None
                        or not now + timedelta(hours=2) < end < now + timedelta(days=365)):
                    continue
                slug, cid = market['slug'], market['conditionId']
                if (type(slug) is not str or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug)
                        or len(slug) > 200 or type(cid) is not str
                        or not re.fullmatch(r'0x[0-9a-f]{64}', cid)):
                    continue
                result.append((end.astimezone(UTC), slug, cid))
            except (ValueError, KeyError, TypeError, OverflowError):
                continue
    return sorted(set(result))


def inspect_team(root: Path, team: str, term: str, pattern: str) -> dict:
    now = datetime.now(UTC)
    item = dict(team_id=team, discovery_at=now.isoformat(), model_called=False,
                database_written=False, discovery_requests=1, preview_invocations=0)
    url = 'https://gamma-api.polymarket.com/public-search?' + urlencode(dict(
        q=term, events_status='active', limit_per_type=10, page=1,
        keep_closed_markets=0, search_profiles='false', search_tags='false'))
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        request = Request(url, headers={'User-Agent': 'polymarket-alpha-lab-local-preview/1'})
        with opener.open(request, timeout=20) as response:
            if response.geturl() != url:
                raise ValueError('unexpected_origin')
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError('discovery_too_large')
        item['discovery_sha256'] = sha256(raw).hexdigest()
        selected = candidates(json.loads(raw.decode('utf-8')), pattern, now)
    except Exception as error:
        return dict(item, status='failed', reason_code='public_discovery_failed', error_type=type(error).__name__)
    item['eligible_in_bounded_search'] = len(selected)
    if not selected:
        return dict(item, status='not_run', reason_code='no_supported_candidate_in_bounded_search')
    end, slug, cid = selected[0]
    cutoff = datetime.now(UTC) + timedelta(minutes=30)
    record_id = 'local-preview-' + team + '-' + uuid.uuid4().hex
    item.update(condition_id=cid, market_slug=slug, scheduled_end_at=end.isoformat(),
                temporary_forecast_cutoff=cutoff.isoformat(), preview_invocations=1)
    command = [sys.executable, '-I', str(root / 'scripts/preview_crypto_research.py'),
        '--record-id', record_id, '--team', team, '--condition-id', cid,
        '--market-slug', slug, '--forecast-cutoff', cutoff.isoformat(),
        '--model', 'operator-model-not-selected', '--allow-public-fetch']
    try:
        # Engineering-only subprocess limit: no DB or model operation is involved.
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=120)
        item['preview_exit_code'] = result.returncode
        report = json.loads(result.stdout)
        if (type(report) is not dict or report.get('model_called') is not False
                or report.get('database_written') is not False):
            raise ValueError('preview_report_invalid')
        if result.returncode == 0:
            if (report.get('status') != 'prepared' or report.get('readiness_only') is not True
                    or report.get('condition_id') != cid or report.get('market_slug') != slug
                    or report.get('team_id') != team or report.get('record_id') != record_id
                    or any(report.get(flag) is not True for flag in ('paper_only', 'report_only', 'readonly'))
                    or 'probability_yes' in report):
                raise ValueError('preview_scope_or_status_invalid')
            return dict(item, status='prepared', preview=report)
        reason = report.get('reason_code', '')
        if type(reason) is not str or not re.fullmatch(r'[a-z0-9_]{1,160}', reason):
            reason = 'preview_cli_failed'
        return dict(item, status='blocked' if report.get('status') == 'blocked' else 'failed', reason_code=reason)
    except subprocess.TimeoutExpired:
        return dict(item, status='failed', reason_code='preview_subprocess_timeout')
    except Exception as error:
        return dict(item, status='failed', reason_code='preview_subprocess_failed', error_type=type(error).__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--allow-public-fetch', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_public_fetch:
        print(json.dumps(dict(status='disabled', model_called=False, database_written=False)))
        return 0
    root = args.source_root.resolve()
    try:
        if (not (root / 'scripts/preview_crypto_research.py').is_file()
                or (root / '.local').exists() or (root / 'PROJECT-BUNDLE.json').exists()
                or Path(sys.prefix).resolve() != (root / '.venv').resolve()
                or git_value(root, 'rev-parse', 'HEAD') != COMMIT
                or git_value(root, 'rev-parse', 'HEAD^{tree}') != TREE
                or git_value(root, 'status', '--porcelain')):
            raise ValueError('fresh_pinned_source_required')
    except Exception:
        print(json.dumps(dict(status='failed', reason_code='source_preflight_failed',
                              model_called=False, database_written=False)))
        return 1
    records = [inspect_team(root, *team) for team in TEAMS]
    clean = not git_value(root, 'status', '--porcelain') and not (root / '.local').exists()
    output = dict(implementation_commit=COMMIT, implementation_tree=TREE,
        synthetic_data=False, model_called=False, database_written=False,
        maximum_public_gets=8, discovery_requests=sum(r['discovery_requests'] for r in records),
        preview_invocations=sum(r['preview_invocations'] for r in records),
        source_unchanged=clean, results=records)
    print(json.dumps(output, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if clean and all(r['status'] == 'prepared' for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
