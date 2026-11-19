"""Read-only, provenance-preserving historical inputs for WP-P4 shadow research.

Thin wrapper since MO-4 (plan 05, node MO-4): the pure parsing, chronology
and fold logic lives in ``polymarket_alpha_lab.papersim.optimize.dataset``;
this module keeps the on-disk archive reading (``load_history``) and
re-exports every historical public name, so sibling tools and the frozen
research scripts that import ``wp_p4_history`` keep working unchanged.
The re-exports bind the very same objects (``LEAGUES`` is the same mutable
mapping), so monkeypatching through this module still reaches the library
functions.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from polymarket_alpha_lab.papersim.optimize.dataset import (
    ALIASES,
    LEAGUES,
    UTC,
    Fold,
    HistoricalMatch,
    HistoryAudit,
    ScheduleFixture,
    archive_fixture_paths,
    archive_read_paths,
    build_folds,
    canonical_team,
    history_at,
    history_from_archive,
    inventory,
    iso,
    parse_matches,
    parse_schedule,
    production_snapshot,
    to_model_history,
    utc,
)

__all__ = [
    "ALIASES", "UTC", "LEAGUES", "Fold", "HistoricalMatch", "HistoryAudit",
    "ScheduleFixture", "archive_fixture_paths", "archive_read_paths",
    "build_folds", "canonical_team", "history_at", "history_from_archive",
    "inventory", "iso", "load_history", "parse_matches", "parse_schedule",
    "production_snapshot", "to_model_history", "utc",
]


def load_history(root: Path | str) -> HistoryAudit:
    """Read only the declared archive files; never fetch, execute tools, or write."""
    root = Path(root).resolve()

    def load(relative: str) -> bytes:
        path = root / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"archive path escapes declared root: {relative}")
        return resolved.read_bytes()

    files: dict[str, bytes] = {}
    for relative in archive_read_paths():
        if not (root / relative).exists():
            # Absent declared files stay absent: the pure parser fails closed
            # with "declared archive file missing" AFTER the pointer integrity
            # check, preserving the historical validation ordering (meta
            # companions and fixture maps were always optional probes).
            continue
        files[relative] = load(relative)
    for fixture_path in archive_fixture_paths(files):
        if (root / fixture_path).exists():
            files[fixture_path] = load(fixture_path)
    audit = history_from_archive(files)
    # Re-attach filesystem provenance the pure parser cannot know.
    sources = tuple({**source, "resolved_path": str((root / source["path"]).resolve())}
                    for source in audit.sources)
    return replace(audit, sources=sources)
