"""Collect published results and timestamped pre-match bookmaker prices."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import gzip
import json
from pathlib import Path
import re
from threading import local
from urllib.parse import urlsplit

from lxml import html
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE = "https://www.betexplorer.com"
# The archive HTML timestamps are expressed in fixed UTC+01. Its client
# my_tz_format function adjusts them by timezone_key - 1. JSON-LD instead
# carries an explicit offset and can use +02:00 in the European summer.
ARCHIVE_ZONE = timezone(timedelta(hours=1))
ARCHIVE_TIME_FORMAT = "%d,%m,%Y,%H,%M"
CUTOFF = date(2026, 11, 19)
HOLDOUT = date(2026, 1, 1)
TUNING = {
    "bra": "2024-01-01",
    "uru": "2025-01-01",
    "uzb": "2025-01-01",
    "col": "2025-01-01",
    "asean": "2025-07-01",
}
LINES = (1.5, 2.5, 3.5, 4.5, 5.5)
LEAGUES = {
    "bra": ("brazil", {
        2022: "serie-a-2022", 2023: "serie-a-2023", 2024: "serie-a-betano-2024",
        2025: "serie-a-betano-2025", 2026: "serie-a",
    }),
    "uru": ("uruguay", {
        2023: "primera-division-2023", 2024: "primera-division-2024",
        2025: "liga-auf-uruguaya-2025", 2026: "liga-auf-uruguaya",
    }),
    "uzb": ("uzbekistan", {
        2023: "super-league-2023", 2024: "super-league-2024",
        2025: "super-league-2025", 2026: "super-league",
    }),
    "col": ("colombia", {
        2023: "primera-b-2023", 2024: "primera-b-2024",
        2025: "primera-b-2025", 2026: "primera-b",
    }),
    "asean": ("asia", {
        2024: "asean-club-championship-2024-2025",
        2025: "asean-club-championship-2025-2026", 2026: "asean-club-championship",
    }),
}
ACCEPTED_STAGES = {
    "uru": {"Apertura", "Clausura", "Torneo Intermedio"},
    "col": {"Apertura", "Clausura", "Apertura - Quadrangular", "Clausura - Quadrangular"},
    "uzb": {"Main"},
}
local_storage = local()
EMPTY_PRICE = "--"


def session():
    if not hasattr(local_storage, "session"):
        local_storage.session = requests.Session()
        local_storage.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
        })
        retry = Retry(
            total=1, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=False,
        )
        local_storage.session.mount("https://", HTTPAdapter(max_retries=retry))
    return local_storage.session


def fetch(cache, url, kind):
    key = sha256(url.encode()).hexdigest()[:20]
    path = cache / f"{kind}-{key}.gz"
    if path.exists():
        return gzip.decompress(path.read_bytes())
    headers = {"X-Requested-With": "XMLHttpRequest"} if kind.startswith("market") else {}
    response = session().get(url, headers=headers, timeout=(8, 14))
    response.raise_for_status()
    if urlsplit(url).path != urlsplit(response.url).path:
        raise ValueError(f"unexpected redirect to {response.url}")
    body = response.content
    path.write_bytes(gzip.compress(body, compresslevel=2, mtime=0))
    return body


def fetch_start(cache, route):
    tree = html.fromstring(fetch(cache, BASE + route, "fixture"))
    scripts = tree.xpath("//script[@type='application/ld+json']/text()")
    for script in scripts:
        try:
            value = json.loads(script)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("@type") == "SportsEvent":
            kick = datetime.fromisoformat(value["startDate"]).astimezone(ARCHIVE_ZONE)
            displayed = tree.xpath("string(//*[@id='match-date']/@data-dt)")
            known = datetime.strptime(displayed, ARCHIVE_TIME_FORMAT)
            if kick.replace(tzinfo=None) != known:
                raise ValueError("schema kickoff and archive local kickoff disagree")
            if tree.xpath("//input[@id='isFinished' and @value='1']"):
                return kick
            raise ValueError("match page does not mark the match finished")
    raise ValueError("match page lacks a SportsEvent kickoff")


def stage_pages(league, index_url, tree):
    stage_links = [
        (a.get("href"), a.text_content().strip())
        for a in tree.xpath("//a[starts-with(@href,'?stage=')]")
    ]
    if league == "asean":
        # Qualification and group stages both use the label Main. The final
        # tournament appears after qualification in every source season.
        main = [url for url, name in stage_links if name == "Main"]
        if len(main) != 2:
            raise ValueError(f"unexpected ASEAN stage selector at {index_url}")
        return [(index_url + main[1], "Group")]
    if not stage_links:
        return [(index_url, "Main")]
    return [
        (index_url + url, name)
        for url, name in stage_links
        if name in ACCEPTED_STAGES[league]
    ]


def league_fixtures(cache, league, season):
    country, slugs = LEAGUES[league]
    index_url = f"{BASE}/football/{country}/{slugs[season]}/results/"
    root = html.fromstring(fetch(cache, index_url, "results"))
    pages = stage_pages(league, index_url, root)
    fixtures, exclusions, sources = {}, Counter(), {}
    for source_url, stage in pages:
        tree = root if source_url == index_url else html.fromstring(fetch(cache, source_url, "results"))
        sources[source_url] = 0
        rows = tree.xpath("//tr[td/a[contains(concat(' ',@class,' '),' in-match ')]]")
        for row in rows:
            cells = row.xpath("./td")
            teams = cells[0].xpath("./a")
            if len(cells) != 6 or len(teams) != 1:
                exclusions["unexpected_table_row"] += 1
                continue
            team_names = [" ".join(x.text_content().split()) for x in teams[0].xpath("./span")]
            score = cells[1].text_content().strip()
            raw_date = cells[-1].text_content().strip()
            href = teams[0].get("href")
            match_id = href.strip("/").split("/")[-1]
            scores = re.fullmatch(r"(\d{1,2})\s*:\s*(\d{1,2})", score)
            if not scores:
                exclusions["non_regulation_or_unplayed"] += 1
                continue
            if len(team_names) != 2 or not re.fullmatch(r"[A-Za-z0-9]{8}", match_id):
                exclusions["invalid_fixture_identity"] += 1
                continue
            try:
                match_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
            except ValueError:
                if re.fullmatch(r"\d{2}\.\d{2}\.", raw_date) and (
                    season == CUTOFF.year or league == "asean" and season == CUTOFF.year - 1
                ):
                    match_date = datetime.strptime(f"{raw_date}{CUTOFF.year}", "%d.%m.%Y").date()
                elif raw_date in {"Today", "Yesterday"}:
                    match_date = fetch_start(cache, href).date()
                else:
                    raise
            if match_date >= CUTOFF:
                exclusions["at_or_after_data_cutoff"] += 1
                continue
            if match_id in fixtures:
                exclusions["duplicate_fixture_id"] += 1
                continue
            fixtures[match_id] = {
                "id": match_id, "league": league, "season": season, "phase": stage,
                "day": str(match_date), "home": team_names[0], "away": team_names[1],
                "hg": int(scores[1]), "ag": int(scores[2]), "url": BASE + href,
                "source_url": source_url,
            }
            sources[source_url] += 1
    return list(fixtures.values()), {"exclusions": dict(exclusions), "sources": sources}


def quote_groups(page, kind, kick):
    tree = html.fromstring(json.loads(page)["odds"])
    selections = {}
    counts = Counter()
    cutoff = kick.astimezone(timezone.utc)
    if kind == "1x2":
        positions = ("1", "0", "2")
        market = "1x2"
    else:
        positions = ("1", "2")
    for row in tree.xpath("//tr[@data-bid][@data-originid='1']"):
        odds = row.xpath("./td[@data-odd][@data-created][@data-sc='2']")
        if kind == "ou":
            line_cells = row.xpath("./td[contains(concat(' ',@class,' '),' table-main__doubleparameter ')]")
            if len(line_cells) != 1:
                continue
            try:
                line = float(line_cells[0].text_content().strip())
            except ValueError:
                continue
            if line not in LINES:
                continue
            market = f"ou{line:.1f}"
            scope = f"E-2-2-0-{line:g}-0"
        else:
            scope = "E-1-2-0-0-0"
        outcomes = {}
        for cell in odds:
            if cell.get("data-hcp") != scope:
                continue
            pos = cell.get("data-pos")
            book = cell.get("data-bookie")
            price = cell.get("data-odd")
            if not book or not price or price == EMPTY_PRICE or pos not in positions:
                continue
            stamp = datetime.strptime(cell.get("data-created"), ARCHIVE_TIME_FORMAT)
            value = float(price)
            if not 1 < value < float("inf"):
                continue
            local_stamp = stamp.replace(tzinfo=ARCHIVE_ZONE)
            age = cutoff - local_stamp.astimezone(timezone.utc)
            if not timedelta(0) < age <= timedelta(hours=24):
                continue
            outcomes[pos] = (book, value, local_stamp.isoformat())
        counts[market] += 1
        if set(outcomes) == set(positions) and len({x[0] for x in outcomes.values()}) == 1:
            name = outcomes[positions[0]][0]
            selections.setdefault(market, {})[name] = [
                [outcomes[pos][1], outcomes[pos][2]] for pos in positions
            ]
    return selections, dict(counts)


def match_prices(cache, game, kinds=("1x2", "ou")):
    prices, raw_count, errors = {}, {}, {}
    match_id = game["id"]
    result = {"id": match_id, "league": game["league"]}
    try:
        kick = fetch_start(cache, urlsplit(game["url"]).path)
        if kick.date().isoformat() != game["day"]:
            raise ValueError("match and results page dates disagree")
        if kick.date() >= CUTOFF or kick.date() < HOLDOUT:
            raise ValueError("kickoff lies outside the holdout")
        result["kickoff"] = kick.isoformat()
    except (requests.RequestException, KeyError, ValueError) as exc:
        return {**result, "error": str(exc)}
    for kind in kinds:
        url = f"{BASE}/match-odds/{match_id}/1/{kind}/odds/?lang=en"
        try:
            groups, counts = quote_groups(fetch(cache, url, f"market-{kind}"), kind, kick)
            prices.update(groups)
            raw_count.update(counts)
        except (requests.RequestException, KeyError, ValueError) as exc:
            errors[kind] = str(exc)
    return {**result, "prices": prices, "raw_count": raw_count, "errors": errors}


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--leagues", nargs="+", choices=LEAGUES, default=list(LEAGUES))
    parser.add_argument("--fixtures-only", action="store_true")
    parser.add_argument("--market", nargs="+", choices=("1x2", "ou"), default=["1x2", "ou"])
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        jobs = {
            workers.submit(league_fixtures, args.cache, league, season): (league, season)
            for league in args.leagues
            for season in LEAGUES[league][1]
        }
        fixtures, metadata = [], {}
        for finished in as_completed(jobs):
            league, season = jobs[finished]
            games, provenance = finished.result()
            fixtures.extend(games)
            metadata[f"{league}-{season}"] = provenance
            print(f"fixtures {league}-{season} {len(games)}", flush=True)
        fixtures.sort(key=lambda game: (game["league"], game["day"], game["id"]))
        assert len({(r["league"], r["id"]) for r in fixtures}) == len(fixtures)
        write_jsonl(args.output / "fixtures.jsonl", fixtures)
        (args.output / "fixture_sources.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if args.fixtures_only:
            return
        target = [game for game in fixtures if game["day"] >= HOLDOUT.isoformat()]
        jobs = {
            workers.submit(match_prices, args.cache, game, args.market): game
            for game in target
        }
        # Flush every completed archive fetch to allow resumable collection.
        with (args.output / "prices.jsonl").open("w", encoding="utf-8", buffering=1) as output:
            for index, finished in enumerate(as_completed(jobs), start=1):
                record = finished.result()
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                if index % 50 == 0 or index == len(jobs):
                    print(f"prices {index}/{len(jobs)}", flush=True)


if __name__ == "__main__":
    main()
