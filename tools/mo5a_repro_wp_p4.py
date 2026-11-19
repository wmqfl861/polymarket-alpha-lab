"""MO-5a server-side reproduction of the published WP-P4 nested selection.

Runs the club-soccer-recalib family's ACTIVE search space (v1, the WP-P4
3x3 grid in declared order) through the same inner-fold selection
algorithm as ``tools/wp_p4_walkforward.select_config`` and compares the
result against the published evidence JSON:

  /home/ubuntu/paper-sim/review/wp-p4-evidence/wp-p4-walkforward-2026-11-07.json
  (sha256 51d7da0e..., registered in docs/paper-sim/wp-p4-evidence-manifest.json)

Comparison surface (review F3), per published block (65 total):
- fold status: 53 "nested_training_only_selection" (each with the nine
  candidate scores), 12 "fixed_predeclared_fallback_insufficient_inner_folds"
  (empty candidate list, fixed 120/8), and the third declared status
  "fixed_fallback_no_fully_converged_candidate" -- pinned as ABSENT in
  the published data (observed count must be 0);
- the selected (half_life_days, team_prior_matches) per block;
- every candidate score row, bit-exact;
- every fixture's recalibrated 1X2 log loss, bit-exact (==).

Input drift (review F2): the archive under the input root is live; the
script re-hashes every source pinned in the evidence JSON and records
the measured drift count together with the run timestamp. Only the 65
published blocks are compared; blocks rebuilt today but not published
(rounds that completed after 2026-11-07) are listed, not compared.

Read-only: touches the archive and the evidence file only; the report
is written to an explicit caller-selected path outside both.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .wp_p4_history import build_folds, iso, load_history, to_model_history
    from .wp_p4_walkforward import TOTAL_LINES, model_fixture, score_forecast
except ImportError:  # Direct script invocation.
    from wp_p4_history import build_folds, iso, load_history, to_model_history
    from wp_p4_walkforward import TOTAL_LINES, model_fixture, score_forecast

from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
    SEARCH_SPACE_V1,
    SOCCER_RECALIB,
    combination_count,
    space_version,
    spec_for_params,
)

STATUSES = (
    "nested_training_only_selection",
    "fixed_predeclared_fallback_insufficient_inner_folds",
    "fixed_fallback_no_fully_converged_candidate",
)
PINNED_STATUS_COUNTS = {
    "nested_training_only_selection": 53,
    "fixed_predeclared_fallback_insufficient_inner_folds": 12,
    "fixed_fallback_no_fully_converged_candidate": 0,
}
MAX_MISMATCH_SAMPLES = 20


def _logloss(prediction, row) -> float:
    score = score_forecast(
        (prediction.home_win, prediction.draw, prediction.away_win),
        {line: prediction.over_probability(line) for line in TOTAL_LINES},
        row.home_goals, row.away_goals,
    )
    return score["logloss_1x2"]


def family_select_config(folds, fold, cache):
    """select_config semantics with the family space and family.fit/predict.

    Mirrors tools/wp_p4_walkforward.select_config line by line: the same
    inner-fold admission (wholly inside the outer training set, results
    complete before the outer cutoff), the same eligibility rule (a
    candidate is eligible only if every inner fit converged), the same
    120/8 fallback below two inner folds and the same deterministic
    grid-order tie-breaking.  Every fit and every prediction goes
    through the family, never through recalib directly.
    """
    if fold.completion_lag_hours != 3.0:
        raise ValueError("family spec fixes completion_lag_hours at 3.0")
    outer_ids = {m.match_id for m in fold.train}
    lag = timedelta(hours=fold.completion_lag_hours)
    inner = [f for f in folds if f.league == fold.league and f.cutoff < fold.cutoff
             and f.test and {m.match_id for m in (*f.train, *f.test)} <= outer_ids
             and all(m.kickoff + lag < fold.cutoff for m in f.test)]
    common = {
        "objective": "mean_inner_1x2_logloss", "tie_break": "declared_grid_order",
        "inner_blocks": [f.block_id for f in inner],
        "inner_fixture_ids": [m.match_id for f in inner for m in f.test],
        "inner_fold_count": len(inner), "inner_fixture_count": sum(len(f.test) for f in inner),
        "selected_at_event_cutoff": iso(fold.cutoff),
        "latest_inner_result_kickoff": iso(max(m.kickoff for f in inner for m in f.test)) if inner else None,
        "selection_is_retrospective_event_time_replay": True,
    }
    if len(inner) < 2:
        spec = spec_for_params({})
        return spec, {**common, "status": STATUSES[1], "config": dict(spec.params),
                      "candidate_scores": []}
    scores = []
    for half_life in SEARCH_SPACE_V1[0].choices:
        for prior in SEARCH_SPACE_V1[1].choices:
            spec = spec_for_params({
                "half_life_days": half_life, "team_prior_matches": prior})
            losses, nonconverged = [], []
            for inner_fold in inner:
                key = (inner_fold.block_id, iso(inner_fold.cutoff),
                       tuple(m.match_id for m in inner_fold.train),
                       half_life, prior, fold.completion_lag_hours)
                if key not in cache:
                    fitted = SOCCER_RECALIB.fit(
                        to_model_history(inner_fold), spec, as_of=inner_fold.cutoff)
                    cache[key] = {
                        "losses": tuple(_logloss(SOCCER_RECALIB.predict(
                            fitted, model_fixture(m)), m) for m in inner_fold.test),
                        "converged": fitted.estimator.league_parameters[
                            inner_fold.league].converged,
                    }
                losses.extend(cache[key]["losses"])
                if not cache[key]["converged"]:
                    nonconverged.append(inner_fold.block_id)
            scores.append({"half_life_days": half_life, "team_prior_matches": prior,
                           "mean_inner_1x2_logloss": statistics.fmean(losses),
                           "n": len(losses), "eligible": not nonconverged,
                           "nonconverged_inner_blocks": nonconverged})
    eligible = [score for score in scores if score["eligible"]]
    if not eligible:
        spec = spec_for_params({})
        return spec, {**common, "status": STATUSES[2], "config": dict(spec.params),
                      "candidate_scores": scores}
    winner = min(eligible, key=lambda s: s["mean_inner_1x2_logloss"])
    spec = spec_for_params({"half_life_days": winner["half_life_days"],
                            "team_prior_matches": winner["team_prior_matches"]})
    return spec, {**common, "status": STATUSES[0], "config": dict(spec.params),
                  "candidate_scores": scores}


def drift_check(root: Path, sources) -> dict:
    items, drifted = [], 0
    for source in sources:
        path = root / source["path"]
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            status = "match" if digest == source["sha256"] else "sha-differs"
        except FileNotFoundError:
            status = "missing"
        if status != "match":
            drifted += 1
        items.append({"path": source["path"], "status": status})
    return {"checked": len(items), "drifted": drifted, "items": items}


def run(input_root: Path, evidence: dict, *, league: str | None = None) -> dict:
    audit = load_history(input_root)
    folds, _ = build_folds(audit)
    if league is not None:
        folds = tuple(f for f in folds if f.league == league)
    by_block = {f.block_id: f for f in folds}
    published = {c["block_id"]: c for c in evidence["fold_configs"]}
    fixture_rows = {}
    for row in evidence["fixture_predictions"]:
        fixture_rows.setdefault(row["block_id"], []).append(row)
    if league is not None:
        published = {b: c for b, c in published.items() if b.split(":")[0] == league}

    status_counts = {name: 0 for name in STATUSES}
    selection_mismatches, score_mismatches, fixture_mismatches = [], [], []
    selected_distribution: dict[tuple[float, float], int] = {}
    fixtures_compared = fixtures_exact = 0
    cache: dict = {}
    blocks = sorted(published, key=lambda b: by_block[b].cutoff if b in by_block
                    else datetime.max.replace(tzinfo=timezone.utc))
    for number, block in enumerate(blocks, 1):
        published_config = published[block]
        fold = by_block.get(block)
        if fold is None:
            selection_mismatches.append({"block": block, "reason": "not_rebuilt"})
            continue
        spec, selection = family_select_config(folds, fold, cache)
        status = selection["status"]
        status_counts[status] += 1
        ours = (spec.params["half_life_days"], spec.params["team_prior_matches"])
        theirs = (published_config["config"]["half_life_days"],
                  published_config["config"]["team_prior_matches"])
        selected_distribution[ours] = selected_distribution.get(ours, 0) + 1
        if status != published_config["status"] or ours != theirs:
            selection_mismatches.append({"block": block, "status_ours": status,
                                         "status_published": published_config["status"],
                                         "selected_ours": ours, "selected_published": theirs})
        if selection["candidate_scores"] != published_config["candidate_scores"]:
            score_mismatches.append({"block": block,
                                     "rows_ours": len(selection["candidate_scores"]),
                                     "rows_published": len(published_config["candidate_scores"])})
        fitted = SOCCER_RECALIB.fit(to_model_history(fold), spec, as_of=fold.cutoff)
        evidence_rows = {row["match_id"]: row for row in fixture_rows.get(block, [])}
        for row in fold.test:
            published_row = evidence_rows.get(row.match_id)
            if published_row is None:
                fixture_mismatches.append({"block": block, "match_id": row.match_id,
                                           "reason": "no_published_row"})
                continue
            prediction = SOCCER_RECALIB.predict(fitted, model_fixture(row))
            if prediction is None:
                fixture_mismatches.append({"block": block, "match_id": row.match_id,
                                           "reason": "family_refused"})
                continue
            ours_loss = _logloss(prediction, row)
            fixtures_compared += 1
            if ours_loss != published_row["forecasts"]["recalibrated"]["logloss_1x2"]:
                fixture_mismatches.append({
                    "block": block, "match_id": row.match_id, "reason": "logloss_diff",
                    "ours": ours_loss,
                    "published": published_row["forecasts"]["recalibrated"]["logloss_1x2"]})
                if len(fixture_mismatches) > MAX_MISMATCH_SAMPLES:
                    break
            else:
                fixtures_exact += 1
        print(f"[{number}/{len(blocks)}] {block}: status={status} "
              f"selected={ours} mismatches={len(fixture_mismatches)}", flush=True)
        if len(fixture_mismatches) > MAX_MISMATCH_SAMPLES:
            break

    published_distribution: dict[tuple[float, float], int] = {}
    for config in published.values():
        key = (config["config"]["half_life_days"], config["config"]["team_prior_matches"])
        published_distribution[key] = published_distribution.get(key, 0) + 1
    return {
        "status_counts_ours": status_counts,
        "status_counts_pinned": dict(PINNED_STATUS_COUNTS),
        "third_status_observed": status_counts[STATUSES[2]] > 0,
        "selected_distribution_ours": {f"{k[0]:g}|{k[1]:g}": v
                                       for k, v in sorted(selected_distribution.items())},
        "selected_distribution_published": {f"{k[0]:g}|{k[1]:g}": v
                                            for k, v in sorted(published_distribution.items())},
        "selection_mismatches": selection_mismatches,
        "candidate_score_mismatches": score_mismatches,
        "fixtures": {"compared": fixtures_compared, "bit_exact": fixtures_exact,
                     "mismatches": fixture_mismatches[:MAX_MISMATCH_SAMPLES]},
        "blocks_published": len(published),
        "blocks_rebuilt_not_published": sorted(set(by_block) - set(published)),
        "blocks_published_not_rebuilt": sorted(set(published) - set(by_block)),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--league", default=None,
                        help="optional league filter for batched runs")
    args = parser.parse_args(argv)
    root, evidence_path = args.input_root.resolve(), args.evidence.resolve()
    output = args.output_json.resolve()
    if output.is_relative_to(root):
        parser.error("the output report must live outside the read-only input root")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc)
    print(f"MO-5a reproduction started {generated_at.isoformat()}", flush=True)
    report = {
        "study": "MO-5a club-soccer-recalib family vs published WP-P4 nested selection",
        "generated_at": generated_at.isoformat(),
        "input_root": str(root),
        "evidence": {"path": str(evidence_path),
                     "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest()},
        "search_space": {
            "version": space_version(SEARCH_SPACE_V1),
            "dimensions": {defn.name: list(defn.choices) for defn in SEARCH_SPACE_V1},
            "combinations": combination_count(SEARCH_SPACE_V1),
        },
        "input_drift": drift_check(root, evidence["sources"]),
        "league_filter": args.league,
        "readonly": True, "paper_only": True, "report_only": True,
    }
    report.update(run(root, evidence, league=args.league))
    report["verdict"] = (
        "reproduced" if (
            not report["selection_mismatches"]
            and not report["candidate_score_mismatches"]
            and report["fixtures"]["compared"] == report["fixtures"]["bit_exact"]
            and report["fixtures"]["mismatches"] == []
            and not report["blocks_published_not_rebuilt"]
            and report["status_counts_ours"] == dict(PINNED_STATUS_COUNTS)
            and report["third_status_observed"] is False
        ) else "MISMATCH"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                      encoding="utf-8")
    print(f"verdict={report['verdict']} -> {output}", flush=True)
    return 0 if report["verdict"] == "reproduced" else 1


if __name__ == "__main__":
    raise SystemExit(main())
