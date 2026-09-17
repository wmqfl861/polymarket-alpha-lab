"""List claimed research tasks without knowing an ID or changing any task.

No live worker checks, retry, model, public fetch, scoring or business writes.
The complete bounded inventory uses the managed project-private PostgreSQL.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution_confirmation_cli import _emit
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from polymarket_alpha_lab.research_execution_inventory import MAX_CLAIMS, ResearchExecutionInventory

_BLOCKS = frozenset(("research_execution_inventory_limit", "research_execution_inventory_future_record"))


def main(argv: list[str] | None = None, *, default_root: Path) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--max-records", type=int, default=MAX_CLAIMS)
    args = parser.parse_args(argv)
    if not 1 <= args.max_records <= MAX_CLAIMS:
        parser.error("max-records must be 1..1000; no truncated inventory is returned")
    result = dict(lookup_scope="all_visible_execution_claims_and_matching_attempts",
        public_network_called=False, live_model_called=False, business_writes_performed=False,
        paper_only=True, report_only=True, readonly=True)
    try:
        with ProjectPostgres(args.root).session() as session:
            inventory = session.execution_inventory(max_records=args.max_records)
            if type(inventory) is not ResearchExecutionInventory:
                raise ValueError("research_execution_inventory_invalid")
            body = inventory.to_dict()
        result.update(status="listed", inventory=body)
        code = 0
    except (Exception, SystemExit) as error:
        reason = (error.args[0] if type(error) is ResearchCaptureConflict
                  and len(error.args) == 1 and type(error.args[0]) is str else None)
        known = reason in _BLOCKS
        result.update(status="blocked" if known else "failed", inventory=None,
            reason_code=reason if known else "research_execution_inventory_failed")
        code = 1
    return _emit(result, code)


__all__ = ("main",)
