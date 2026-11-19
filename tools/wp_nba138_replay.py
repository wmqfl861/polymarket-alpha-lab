#!/usr/bin/env python3
"""MO-5c acceptance replay: default-parameter NBA predictions, frozen.

Report-only, read-only, standard library only. Pairs every game of a
frozen ESPN NBA snapshot (the WP-NBA-1 contract), predicts each one
with ``nba_model.fixture_prediction`` at DEFAULT parameters (the
fixture carries the game's own rest/B2B flags; ``before_date`` is the
game date), and writes a canonical JSON dump of every field of every
prediction (plus totals quotes) to stdout. The MO-5c acceptance is
that this dump is byte-identical before and after the MO-5c
parameterization of ``nba_model`` — run it on both sides and ``cmp``.

Usage (``-I`` ignores PYTHONPATH, so the package root is resolved from
``--src`` — a REPOSITORY ROOT whose ``src/`` provides the package —
or from this file's own location)::

    python3 -I tools/wp_nba138_replay.py SNAPSHOT.json > after.json
    python3 -I /tmp/wp_nba138_replay.py --src /tmp/mo5c-base \\
        SNAPSHOT.json > before.json
    cmp before.json after.json

The expected frozen sample is ``~/paper-sim/nba/snapshot_20261110.json``
(sha256 eca00867..., 30 teams / 154 games / 138 predictable — DELIVERY_PLAN
sections 74 and 79.2). A human summary (counts plus the dump's SHA-256)
goes to stderr; only the canonical dump goes to stdout.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path


def _load_model(src_dir: str | None):
    root = Path(src_dir).resolve() if src_dir else Path(__file__).resolve().parents[1]
    candidate = root / "src"
    if not candidate.is_dir():
        raise SystemExit(f"no src directory under {root}")
    sys.path.insert(0, str(candidate))
    from polymarket_alpha_lab.papersim import nba_model  # noqa: PLC0415

    return nba_model


#: Fixed quote line pinned by the 79.2 replay evidence (225.5 totals).
FIXED_LINE = 225.5


def _pair_games(snapshot: dict) -> dict[tuple[str, str, str], tuple[dict, dict]]:
    """Pair the two team rows of each game, keyed (date, away, home)."""

    paired: dict[tuple[str, str, str], dict] = {}
    for team, block in snapshot["teams"].items():
        for game in block["games"]:
            if game["home"]:
                key = (game["date"], game["opponent"], team)
                row = paired.setdefault(key, {})
                row["home"] = game
            else:
                key = (game["date"], team, game["opponent"])
                row = paired.setdefault(key, {})
                row["away"] = game
    complete = {}
    for key, rows in paired.items():
        if "home" in rows and "away" in rows:
            complete[key] = (rows["home"], rows["away"])
    return complete


def _mid_line(exp_total: float) -> float:
    """The model-median line snapped to the nearest half point."""

    return round(exp_total * 2.0) / 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshot", type=str, help="frozen snapshot JSON path")
    parser.add_argument(
        "--src",
        type=str,
        default=None,
        help="repository root whose src/ provides the package "
        "(default: this file's own repository)",
    )
    args = parser.parse_args()

    nba_model = _load_model(args.src)
    raw = Path(args.snapshot).read_bytes()
    snapshot = json.loads(raw)
    games = _pair_games(snapshot)

    records = []
    predicted = refused = 0
    for (date_s, away, home) in sorted(games):
        home_row, away_row = games[(date_s, away, home)]
        fixture = nba_model.NbaFixture(
            home=home,
            away=away,
            rest_home=home_row.get("rest_days"),
            rest_away=away_row.get("rest_days"),
            b2b_home=bool(home_row.get("b2b", False)),
            b2b_away=bool(away_row.get("b2b", False)),
        )
        reason = nba_model.refusal_reason(snapshot, fixture, before_date=date_s)
        record = {
            "date": date_s,
            "home": home,
            "away": away,
            "status": "refused",
            "refusal_reason": reason,
        }
        if reason is None:
            prediction = nba_model.fixture_prediction(
                snapshot, fixture, before_date=date_s
            )
            if prediction is None:  # pragma: no cover - reason covers it
                raise SystemExit(f"unexplained refusal for {date_s} {away}@{home}")
            record["status"] = "predicted"
            record.pop("refusal_reason")
            record["prediction"] = dataclasses.asdict(prediction)
            lines = {FIXED_LINE}
            mid = _mid_line(prediction.exp_total)
            if mid != FIXED_LINE:
                lines.add(mid)
            record["totals_quotes"] = {
                f"{line:.1f}": dataclasses.asdict(
                    prediction.total_line_prob(line)
                )
                for line in sorted(lines)
            }
            predicted += 1
        else:
            refused += 1
        records.append(record)

    dump = json.dumps(
        {
            "tool": "wp_nba138_replay",
            "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "variants": list(nba_model.VARIANT_NAMES),
            "games": records,
        },
        sort_keys=True,
        indent=None,
    )
    sys.stdout.write(dump + "\n")
    print(
        f"paired={len(games)} predicted={predicted} refused={refused} "
        f"dump_sha256={hashlib.sha256(dump.encode('utf-8')).hexdigest()}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
