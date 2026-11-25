"""B1 run-scan orchestration (plan 05 section 2 / DELIVERY_PLAN 80.8).

One scan is: a clean-worktree code-commit check (FX-03: the scan
refuses to run on a dirty worktree and every receipt row records the
verified HEAD), ONE model-spec consult per lineage through the frozen
provider interface (04 section 10 steps 1 and 5 -- roles are frozen for
the whole run), the FX-17 pause admission wiring (``queue_policy`` is
the pure decision layer; this module persists the latch, appends PAUSE
evidence rows and suppresses entries -- never scanning, candidate
recording or marks -- on paused queues), per-market candidate
construction through the frozen entry point
:func:`~polymarket_alpha_lab.papersim.pipeline.decide_entry` with the
FX-26 event-key binding, and one atomic
:func:`~polymarket_alpha_lab.papersim.store_psycopg.record_scan` write
of the run row, every candidate, the entries and their cash legs.

Bit-identity contract (05 section 2, B1 row): with the registry empty
the provider returns each lineage's frozen baseline whose
``model_version()`` IS the pipeline constant, so every candidate row
is byte-identical to the unwired stack.  ``assemble_scan`` proves this
structurally: with ``specs=None`` (the reference path) the ONLY
different code is where ``model_version`` comes from -- both paths
bind through the same ``replay.bind_scan_run`` / ``replay.bind_entry``
/ ``replay.bind_candidate`` calls.  The scan parameters record the
provider digest (canonical, sorted, time-free) and the fallback reason
per 04 section 10 step 1; that registration is the one intentional
difference from the unwired world and is excluded from the candidate
comparison.

Purity profile: ``require_clean_worktree`` runs git; the pause and
assembly helpers are pure over their explicit inputs; ``run_scan`` is
the only stateful driver and takes every external dependency as an
explicit argument (records, provider, clock, connection), so the
offline suite exercises everything except the transactional writes,
which the native lane proves on a disposable cluster.

MO-7 (plan 05 node MO-7, plan review R2 PASS -- wired): design 04
section 10 steps 3 and 4.  The provider consult goes through
``optimize.provider.RegistryProvider`` (seam 1: constructed by the
CLI, same ``SpecProvider`` protocol; an empty or unreadable registry
collapses to the frozen baseline inside the provider, 04 section 11).
The role loop (seam 2) iterates active+baseline+candidates+guard
deduplicated by spec digest: the active role prices and enters exactly
as B1, every OTHER distinct spec produces a pure-gate-only shadow
prediction (``gates.evaluate_candidate`` -- never an entry, never a
cash event, never portfolio state).  The scan parameters (seam 3)
carry the registry-resolved specs' canonical texts plus the shadow
fit-materials inventory (``shadow`` section, present only when at
least one role fitted -- an empty registry keeps the parameters
byte-identical to B1).  Seam 4 is the ``record_shadow_predictions``
writer in ``store_psycopg``; the shadow write rides AFTER the scan's
own atomic write in one separate transaction, and its failure is
recorded without failing the scan (04 section 11: an optimizer fault
never stops the scan).  Replay-side, ``replay.replay_shadow_prediction``
recomputes a stored shadow row from the candidate bundle, the spec and
the pinned snapshots; the entry-bundle codec and
``REPLAY_BUNDLE_VERSION`` are untouched, so the active decisions still
replay bit-exactly.

Timing discipline: shadow counts and seconds are properties of THIS
attempt (the run-scan return dict and the CLI stdout -- the node's
acceptance registration cites them); the SUCCEEDED receipt evidence
stays the scan key only, and the time-free parameters never carry
clock-derived content.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import evaluation, pipeline, queue_policy, store
from polymarket_alpha_lab.papersim.optimize import shadow
from polymarket_alpha_lab.papersim.optimize.provider import LineageSpecs

__all__ = [
    "AdmissionReport",
    "EventKeyMaterials",
    "MarketRecord",
    "PortfolioState",
    "QueueScanConfig",
    "ScanAssembly",
    "ScanConfig",
    "ScanError",
    "ShadowBinding",
    "assemble_scan",
    "bind_event_key",
    "consult_provider",
    "load_pack_records",
    "pause_admissions",
    "provider_params",
    "read_portfolio_state",
    "records_from_pack",
    "require_clean_worktree",
    "role_spec_pairs",
    "run_scan",
]

SCAN_COMMAND = "run-scan"

#: The role vocabulary of one scan (B1 bound only ``("active",)``;
#: MO-7 extended the tuple per the seam note).  ``role_spec_pairs``
#: iterates these roles deduplicated by spec digest -- with the
#: registry empty the deduplicated set collapses to the active spec
#: alone (the baseline IS the active), so nothing shadows.
ACTIVE_ROLES = ("active", "baseline", "candidate", "guard")


class ScanError(ValueError):
    """One scan-level refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# FX-03: clean-worktree commit check
# ---------------------------------------------------------------------------

def require_clean_worktree(repo_root, *, git_bin: str = "git") -> str:
    """Return the verified HEAD commit; refuse ANY dirty worktree state.

    ``git status --porcelain`` output of any kind (staged, unstaged or
    untracked) refuses the scan with ``papersim_scan_worktree_dirty``:
    a scan receipt must record a commit that exists, and operator
    override is deliberately absent (commit first, then scan).
    """
    repo = Path(repo_root)

    def _git(*args: str) -> str:
        try:
            completed = subprocess.run(
                [git_bin, "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ScanError("papersim_scan_git_error", repr(error)) from error
        if completed.returncode != 0:
            raise ScanError("papersim_scan_git_error", completed.stderr.strip()[:200])
        return completed.stdout

    head = _git("rev-parse", "HEAD").strip()
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ScanError("papersim_scan_head_invalid", head[:80])
    porcelain = _git("status", "--porcelain")
    if porcelain.strip():
        first = porcelain.strip().splitlines()[0][:200]
        raise ScanError("papersim_scan_worktree_dirty", first)
    return head


# ---------------------------------------------------------------------------
# Configuration and the prepared-market pack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueScanConfig:
    """One scanned queue: its pause identity and its market pack.

    ``queue`` is the FULL queue identity used for the FX-17 latch (the
    same id ``queue_policy.queue_pause_input`` derives); market records
    group under it.  ``league_scope`` is ``DOMESTIC`` or
    ``CROSS_LEAGUE`` (queue_policy scopes); ``model_key``/``market_type``
    select the queue's metrics in ``evaluation.evaluate_queues`` output.
    """

    queue: str = ""
    lineage: str = ""
    league_scope: str = queue_policy.SCOPE_DOMESTIC
    data_source_version: str = ""
    model_key: str = ""
    market_type: str = ""
    pack: str = ""

    def validate(self) -> "QueueScanConfig":
        for name, value in (("queue", self.queue), ("lineage", self.lineage),
                            ("data_source_version", self.data_source_version)):
            if type(value) is not str or not value.strip():
                raise ScanError("papersim_scan_queue_field_invalid", name)
        if self.league_scope not in (queue_policy.SCOPE_DOMESTIC,
                                     queue_policy.SCOPE_CROSS_LEAGUE):
            raise ScanError("papersim_scan_league_scope_invalid", self.league_scope)
        if self.pack and type(self.pack) is not str:
            raise ScanError("papersim_scan_pack_invalid")
        return self


@dataclass(frozen=True)
class ScanConfig:
    """The scan-wide configuration (one run-scan invocation)."""

    team: str = ""
    operator: str = "unattended"
    queues: tuple[QueueScanConfig, ...] = ()

    def validate(self) -> "ScanConfig":
        if type(self.team) is not str or not self.team.strip():
            raise ScanError("papersim_scan_team_invalid")
        if type(self.operator) is not str or not self.operator.strip():
            raise ScanError("papersim_scan_operator_invalid")
        for queue in self.queues:
            queue.validate()
        return self


def config_from_mapping(document: Mapping[str, Any]) -> ScanConfig:
    """Parse one scan config JSON document (validated, no file IO)."""
    if not isinstance(document, Mapping):
        raise ScanError("papersim_scan_config_invalid", "document must be an object")
    queues = []
    for raw in document.get("queues", ()):
        if not isinstance(raw, Mapping):
            raise ScanError("papersim_scan_config_invalid", "queue entries must be objects")
        known = {"queue", "lineage", "league_scope", "data_source_version",
                 "model_key", "market_type", "pack"}
        unknown = set(raw) - known
        if unknown:
            raise ScanError("papersim_scan_config_invalid", ",".join(sorted(unknown)))
        queues.append(QueueScanConfig(
            queue=str(raw.get("queue", "")),
            lineage=str(raw.get("lineage", "")),
            league_scope=str(raw.get("league_scope", queue_policy.SCOPE_DOMESTIC)),
            data_source_version=str(raw.get("data_source_version", "")),
            model_key=str(raw.get("model_key", "")),
            market_type=str(raw.get("market_type", "")),
            pack=str(raw.get("pack", "")),
        ))
    return ScanConfig(
        team=str(document.get("team", "")),
        operator=str(document.get("operator", "unattended")),
        queues=tuple(queues),
    ).validate()


@dataclass(frozen=True)
class EventKeyMaterials:
    """The FX-26 event-key inputs of one market (D8 binding layer).

    Materials come from the adapters: ``game_start_at`` (parsed from
    Gamma at ``adapters/polymarket.py``) plus the fixture identity for
    sports, the asset plus the market end date for crypto.  The Gamma
    EVENT id is not exposed by the adapter yet, so that branch stays
    empty and ``store.candidate_event_key`` falls back to the canonical
    league|home|away|day string -- or ``None`` when the inputs are
    incomplete; the key is never fabricated here.
    """

    market_class: str
    gamma_event_id: str | None = None
    league: str | None = None
    home: str | None = None
    away: str | None = None
    kickoff_day: str | None = None
    asset: str | None = None
    expiry_at: datetime.datetime | None = None
    market_ref: str | None = None
    event_start_at: datetime.datetime | None = None


@dataclass(frozen=True)
class MarketRecord:
    """One prepared market: binding data plus a replay-bundle request.

    B1's market SOURCE is the prepared pack (the adapter stand-in also
    used by the tests): each record carries the frozen
    ``replay_bundle_payload`` text of its ``EntryRequest`` (decoded via
    ``replay.request_from_replay_bundle`` -- the existing codec, no
    second serialization) or a construction-failure bundle.  Portfolio
    state (bankroll/cash/team exposure/held sides) is NOT baked into
    the pack: the orchestrator injects the live state into the decoded
    request with ``dataclasses.replace`` before ``decide_entry``, and
    advances it in memory after each accepted entry.
    """

    queue: str
    candidate_key: str
    team: str
    side: str
    market_kind: str
    lineage: str
    model_name: str
    evaluated_at: datetime.datetime
    market_ref_kind: str = "market_id"
    condition_id: str | None = None
    market_id: str | None = None
    market_slug: str | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    market_type: str | None = None
    median_probability: float | None = None
    variant_quotes: str | list = "[]"
    snapshot_refs: tuple[str, ...] | list = ()
    book_snapshot_sha256: str | None = None
    decision_snapshot_sha256: str | None = None
    source_kind: str = "clob_book"
    source_ref: str | None = None
    events: EventKeyMaterials | None = None
    request_bundle: str | None = None
    construction: Mapping[str, Any] | None = None
    prepared_provenance: Mapping[str, Any] | None = None


def _optional_datetime(value: Any, name: str) -> datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if type(value) is str:
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError as error:
            raise ScanError("papersim_scan_pack_invalid", f"{name}: bad iso datetime") from error
        return parsed
    raise ScanError("papersim_scan_pack_invalid", f"{name}: datetime required")


def _required_datetime(value: Any, name: str) -> datetime.datetime:
    parsed = _optional_datetime(value, name)
    if parsed is None:
        raise ScanError("papersim_scan_pack_invalid", f"{name}: datetime required")
    return parsed


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ScanError("papersim_scan_pack_invalid", f"{name}: text required")
    return value


def _required_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ScanError("papersim_scan_pack_invalid", f"{name}: non-empty text required")
    return value


def _events_from_mapping(raw: Any) -> EventKeyMaterials | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ScanError("papersim_scan_pack_invalid", "events must be an object")
    return EventKeyMaterials(
        market_class=_required_text(raw.get("market_class"), "events.market_class"),
        gamma_event_id=_optional_text(raw.get("gamma_event_id"), "events.gamma_event_id"),
        league=_optional_text(raw.get("league"), "events.league"),
        home=_optional_text(raw.get("home"), "events.home"),
        away=_optional_text(raw.get("away"), "events.away"),
        kickoff_day=_optional_text(raw.get("kickoff_day"), "events.kickoff_day"),
        asset=_optional_text(raw.get("asset"), "events.asset"),
        expiry_at=_optional_datetime(raw.get("expiry_at"), "events.expiry_at"),
        market_ref=_optional_text(raw.get("market_ref"), "events.market_ref"),
        event_start_at=_optional_datetime(raw.get("event_start_at"), "events.event_start_at"),
    )


def _record_from_mapping(raw: Any, *, managed_preparation: bool = False) -> MarketRecord:
    if not isinstance(raw, Mapping):
        raise ScanError("papersim_scan_pack_invalid", "records must be objects")
    construction = raw.get("construction")
    if construction is not None and not isinstance(construction, Mapping):
        raise ScanError("papersim_scan_pack_invalid", "construction must be an object")
    request_bundle = _optional_text(raw.get("request_bundle"), "request_bundle")
    if request_bundle is None and construction is None:
        raise ScanError("papersim_scan_pack_invalid",
                        "one of request_bundle/construction is required")
    if request_bundle is not None and construction is not None:
        raise ScanError("papersim_scan_pack_invalid",
                        "request_bundle and construction are mutually exclusive")
    refs = raw.get("snapshot_refs", ())
    if not isinstance(refs, (list, tuple)):
        raise ScanError("papersim_scan_pack_invalid", "snapshot_refs must be a list")
    return MarketRecord(
        queue=_required_text(raw.get("queue"), "queue"),
        candidate_key=_required_text(raw.get("candidate_key"), "candidate_key"),
        team=_required_text(raw.get("team"), "team"),
        side=_required_text(raw.get("side"), "side"),
        market_kind=_required_text(raw.get("market_kind"), "market_kind"),
        lineage=_required_text(raw.get("lineage"), "lineage"),
        model_name=_required_text(raw.get("model_name"), "model_name"),
        evaluated_at=_required_datetime(raw.get("evaluated_at"), "evaluated_at"),
        market_ref_kind=_required_text(raw.get("market_ref_kind", "market_id"),
                                       "market_ref_kind"),
        condition_id=_optional_text(raw.get("condition_id"), "condition_id"),
        market_id=_optional_text(raw.get("market_id"), "market_id"),
        market_slug=_optional_text(raw.get("market_slug"), "market_slug"),
        yes_token_id=_optional_text(raw.get("yes_token_id"), "yes_token_id"),
        no_token_id=_optional_text(raw.get("no_token_id"), "no_token_id"),
        market_type=_optional_text(raw.get("market_type"), "market_type"),
        median_probability=raw.get("median_probability"),
        variant_quotes=raw.get("variant_quotes", "[]"),
        snapshot_refs=tuple(str(ref) for ref in refs),
        book_snapshot_sha256=_optional_text(raw.get("book_snapshot_sha256"),
                                            "book_snapshot_sha256"),
        decision_snapshot_sha256=_optional_text(raw.get("decision_snapshot_sha256"),
                                                "decision_snapshot_sha256"),
        source_kind=_required_text(raw.get("source_kind", "clob_book"), "source_kind"),
        source_ref=_optional_text(raw.get("source_ref"), "source_ref"),
        events=_events_from_mapping(raw.get("events")),
        request_bundle=request_bundle,
        construction=construction,
        prepared_provenance=raw.get("prepared_provenance") if managed_preparation else None,
    )


def records_from_pack(payload, *, managed_preparation: bool = False) -> tuple[MarketRecord, ...]:
    """Parse one pack (JSON text/bytes or an already-loaded list)."""
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            document = json.loads(payload)
        except ValueError as error:
            raise ScanError("papersim_scan_pack_invalid", "not valid JSON") from error
    elif isinstance(payload, Sequence):
        document = payload
    else:
        raise ScanError("papersim_scan_pack_invalid", "pack must be JSON text or a list")
    if not isinstance(document, list):
        raise ScanError("papersim_scan_pack_invalid", "pack must be a JSON array")
    return tuple(_record_from_mapping(raw, managed_preparation=managed_preparation) for raw in document)


def load_pack_records(config: ScanConfig, *, base_dir) -> tuple[MarketRecord, ...]:
    """Load every queue's pack file relative to ``base_dir`` (no globs)."""
    records: list[MarketRecord] = []
    for queue in config.queues:
        if not queue.pack:
            continue
        path = Path(base_dir) / queue.pack
        try:
            records.extend(records_from_pack(path.read_text(encoding="utf-8")))
        except OSError as error:
            raise ScanError("papersim_scan_pack_unreadable", str(path)) from error
    return tuple(records)


# ---------------------------------------------------------------------------
# FX-17 pause admission wiring
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissionReport:
    """The pause decisions of one scan plus the queues they gate."""

    decisions: tuple[queue_policy.PauseDecision, ...] = ()
    paused_queues: frozenset[str] = frozenset()
    escalations: tuple[queue_policy.PauseDecision, ...] = ()


def pause_admissions(evaluation_candidates, queue_configs, latched) -> AdmissionReport:
    """Evaluate the FX-17 rules for every configured queue (pure).

    ``latched`` is ``store_psycopg.latched_pauses`` output (queue_id ->
    (review, stop) flags folded from the append-only control rows); the
    metric inputs come from ``evaluation.evaluate_queues`` over the
    caller-injected historical candidates (the CLI reuses the SAME
    loader as the evaluate command -- one metric implementation, no
    drift).  A configured queue without stored metrics evaluates with
    UNKNOWN metrics, which never trips a pause by itself.
    """
    report = evaluation.evaluate_queues(tuple(evaluation_candidates))
    by_key: dict[tuple[str, str], Any] = {}
    for queue_evaluation in report.queues:
        key = (queue_evaluation.queue.model_key, queue_evaluation.queue.market_type)
        by_key[key] = queue_evaluation
    inputs = []
    for config in queue_configs:
        flags = latched.get(config.queue, (False, False))
        queue_evaluation = by_key.get((config.model_key, config.market_type)) \
            if config.model_key and config.market_type else None
        if queue_evaluation is not None:
            inputs.append(queue_policy.queue_pause_input(
                queue_evaluation,
                league_scope=config.league_scope,
                data_source_version=config.data_source_version,
                queue_id=config.queue,
                pause_review_latched=flags[0],
                auto_stop_latched=flags[1],
            ))
        else:
            inputs.append(queue_policy.QueuePauseInput(
                queue_id=config.queue,
                league_scope=config.league_scope,
                model_key=config.model_key or "unknown",
                market_type=config.market_type or "unknown",
                gate_version=pipeline.GATE_VERSION,
                data_source_version=config.data_source_version,
                entered=0,
                settled=0,
                clv_upper_bound=None,
                max_drawdown=None,
                net_pnl=None,
                pause_review_latched=flags[0],
                auto_stop_latched=flags[1],
            ))
    decisions = tuple(queue_policy.evaluate_pause(inputs))
    return AdmissionReport(
        decisions=decisions,
        paused_queues=frozenset(d.queue_id for d in decisions if d.paused),
        escalations=tuple(d for d in decisions
                          if d.action != queue_policy.ACTION_CONTINUE),
    )


# ---------------------------------------------------------------------------
# FX-26 event-key binding (D8)
# ---------------------------------------------------------------------------

def bind_event_key(events: EventKeyMaterials | None) -> tuple[str | None,
                                                               datetime.datetime | None]:
    """The pure match key and start time of one market's event (FX-26).

    Delegates wholly to ``store.candidate_event_key``: insufficient
    materials yield ``(None, None)`` -- no key is fabricated here and
    the evaluation loader then keeps the frozen market-key clustering.
    """
    if events is None:
        return (None, None)
    key = store.candidate_event_key(
        market_class=events.market_class,
        gamma_event_id=events.gamma_event_id,
        league=events.league,
        home=events.home,
        away=events.away,
        kickoff_day=events.kickoff_day,
        asset=events.asset,
        expiry_at=events.expiry_at,
        market_ref=events.market_ref,
    )
    return (key, events.event_start_at)


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioState:
    """The decision-time portfolio state injected into every request.

    ``reserved`` is zero in the v2 ledger (cash legs cover everything);
    ``team_committed`` maps team name to open stake; ``held_sides``
    carries the direction-bearing side strings of open entries for the
    frozen correlation rule.
    """

    bankroll: float = 0.0
    cash: float = 0.0
    reserved: float = 0.0
    team_committed: Mapping[str, float] = field(default_factory=dict)
    held_sides: tuple[str, ...] = ()
    held_by_event: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def with_entry(self, *, team: str, side: str, stake: float, fee: float,
                   event_scope: str | None = None) -> "PortfolioState":
        committed = dict(self.team_committed)
        committed[team] = committed.get(team, 0.0) + float(stake)
        held_by_event = dict(self.held_by_event)
        if event_scope is not None:
            held_by_event[event_scope] = (*held_by_event.get(event_scope, ()), side)
        return PortfolioState(
            bankroll=self.bankroll,
            cash=self.cash - float(stake) - float(fee),
            reserved=self.reserved,
            team_committed=committed,
            held_sides=self.held_sides + (side,),
            held_by_event=held_by_event,
        )


def read_portfolio_state(conn, *, prepared: bool = False) -> PortfolioState:
    """Read the current bankroll/cash/open-exposure state (one snapshot).

    Same SQL shapes as ``store_psycopg.bankroll_daily_check``: cash is
    the bankroll-events sum, open principal the unsettled entries'
    stakes, and the per-team exposure and held sides join the open
    entries with their candidates.
    """
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT coalesce(sum(amount), 0) FROM papersim.bankroll_events')
        cash = float(cursor.fetchone()[0])
        cursor.execute('SELECT coalesce(sum(e.stake), 0) FROM papersim.entries e '
                       'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s '
                       'WHERE s.entry_id = e.entry_id)')
        deployed = float(cursor.fetchone()[0])
        cursor.execute('SELECT c.team, sum(e.stake) FROM papersim.entries e '
                       'JOIN papersim.candidates c ON c.candidate_id = e.candidate_id '
                       'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s '
                       'WHERE s.entry_id = e.entry_id) GROUP BY c.team')
        committed = {str(team): float(total) for team, total in cursor.fetchall()}
        cursor.execute('SELECT c.side FROM papersim.entries e '
                       'JOIN papersim.candidates c ON c.candidate_id = e.candidate_id '
                       'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s '
                       'WHERE s.entry_id = e.entry_id) ORDER BY e.entry_id')
        held = tuple(str(side) for (side,) in cursor.fetchall())
        held_by_event: dict[str, list[str]] = {}
        if prepared:
            cursor.execute('SELECT c.event_key, c.market_id, c.input_payload, c.side '
                           'FROM papersim.entries e JOIN papersim.candidates c USING (candidate_id) '
                           'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s '
                           'WHERE s.entry_id = e.entry_id) ORDER BY e.entry_id')
            for event_key, market_id, payload, side in cursor.fetchall():
                try:
                    model_side = replay.request_from_replay_bundle(payload).side
                except replay.PapersimReplayError:
                    model_side = side
                scope = event_key or f"market:{market_id}"
                held_by_event.setdefault(scope, []).append(model_side)
    return PortfolioState(bankroll=cash + deployed, cash=cash, reserved=0.0,
                          team_committed=committed, held_sides=held,
                          held_by_event={key: tuple(sides) for key, sides in held_by_event.items()})


# ---------------------------------------------------------------------------
# The provider consult (04 section 10 step 1/5)
# ---------------------------------------------------------------------------

def consult_provider(provider, records: Iterable[MarketRecord]) -> dict[str, LineageSpecs]:
    """ONE consult per lineage, frozen for the whole scan.

    MO-7 SEAM (B1 plan D7, first wiring point): when the registry
    exists this is where MO-7's ``optimize.provider.RegistryProvider``
    replaces the frozen-baseline provider (same ``SpecProvider``
    protocol); every lineage still resolves exactly once per scan --
    a role change never takes effect mid-scan (04 section 10 step 5).
    """
    specs: dict[str, LineageSpecs] = {}
    for lineage in sorted({record.lineage for record in records}):
        try:
            specs[lineage] = provider.specs_for_lineage(lineage)
        except ValueError as error:
            raise ScanError("papersim_provider_lineage_error", str(error)) from error
    return specs


def provider_params(specs: Mapping[str, LineageSpecs]) -> dict:
    """The canonical, time-free provider digest for ``scan_runs.params``.

    Sorted keys and spec digests only (``ModelSpec.spec_sha256`` is
    itself canonical JSON: sorted keys, decimal-string numbers), plus
    the fallback-reason enum.  No clock-derived content, so the same
    inputs give byte-identical params text and the same ``scan_key``
    material (``store.scan_key`` hashes the params text).  MO-7 SEAM
    (third wiring point): the non-active roles' digests are already
    carried here so MO-7's shadow statistics can cite them.

    MO-7 (review decision 3, conditional): lineages the REGISTRY
    resolved (``fallback_reason is None``) additionally carry each
    role's canonical spec text (``ModelSpec.canonical_json`` -- the
    digest AND the parameters of design 04 section 10 step 4) and the
    consulted registry lineages with their own actives (the MO-8
    visibility note).  Fallback lineages keep the exact B1 shape, so
    an empty registry yields byte-identical parameters text.
    """
    entries: dict[str, dict] = {}
    for lineage in sorted(specs):
        roles = specs[lineage]
        entry = {
            "active_spec_sha256": roles.active.spec_sha256(),
            "baseline_spec_sha256": roles.baseline.spec_sha256(),
            "candidates": tuple(spec.spec_sha256() for spec in roles.candidates),
            "guard": (None if roles.guard is None
                      else roles.guard.spec_sha256()),
            "fallback_reason": roles.fallback_reason,
        }
        if roles.fallback_reason is None:
            entry["canonical"] = {
                "active": roles.active.canonical_json(),
                "baseline": roles.baseline.canonical_json(),
                "candidates": tuple(spec.canonical_json() for spec in roles.candidates),
                "guard": (None if roles.guard is None
                          else roles.guard.canonical_json()),
            }
            if roles.registry is not None:
                entry["registry_lineages"] = tuple(
                    {"lineage": registry_lineage, "active": active,
                     "candidates": tuple(candidates)}
                    for registry_lineage, active, candidates in roles.registry)
        entries[lineage] = entry
    return {"provider": {"specs": entries}}


# ---------------------------------------------------------------------------
# MO-7 shadow predictions (04 section 10 step 3; plan review R2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowBinding:
    """One pending shadow prediction, pre-insert (candidate_id unresolved).

    Everything the ``papersim.shadow_predictions`` row carries except
    the database identity: the writer resolves ``candidate_key``
    through ``record_scan``'s returned candidate ids.  ``predicted_at``
    is the scan-recorded evaluation time (never the writing clock).
    """

    candidate_key: str
    lineage: str
    spec_sha256: str
    role_at_prediction: str
    probability: float | None
    variant_quotes: tuple[float, ...] | None
    refusal_reason: str | None
    gate_pass: bool
    gate_reason: str | None
    hypothetical_fill_price: float | None
    predicted_at: datetime.datetime

    def __post_init__(self) -> None:
        if self.role_at_prediction not in ("BASELINE", "CANDIDATE", "GUARD"):
            raise ValueError("papersim_shadow_role_invalid")
        if self.refusal_reason is not None:
            if self.probability is not None or self.variant_quotes is not None:
                raise ValueError("papersim_shadow_refusal_conflict")
        elif self.probability is None or self.variant_quotes is None:
            raise ValueError("papersim_shadow_quote_missing")
        if type(self.gate_pass) is not bool:
            raise ValueError("papersim_shadow_gate_invalid")
        if not isinstance(self.predicted_at, datetime.datetime) \
                or self.predicted_at.tzinfo is None:
            raise ValueError("papersim_shadow_time_invalid")


def role_spec_pairs(specs: LineageSpecs) -> tuple[tuple[str, Any], ...]:
    """The ``(role, spec)`` iteration of one lineage's roles (04 s10 steps 2-3).

    Fixed order active, baseline, candidates, guard, deduplicated by
    spec digest keeping the FIRST role: with the registry empty (and
    throughout MO-7) the active IS the production baseline, so the
    pair collapses to the active alone and nothing shadows (04
    section 3: the baseline shadows only when it is not the active).
    After a handover the guard can BE the baseline spec; the first
    role then labels the pair ``baseline`` and the shadow-prediction
    key (candidate row + spec + prediction time, no role) stays
    collision-free.
    """

    pairs: list[tuple[str, Any]] = [("active", specs.active),
                                    ("baseline", specs.baseline)]
    pairs.extend(("candidate", spec) for spec in specs.candidates)
    if specs.guard is not None:
        pairs.append(("guard", specs.guard))
    seen: set[str] = set()
    ordered: list[tuple[str, Any]] = []
    for role, spec in pairs:
        if role not in ACTIVE_ROLES:
            raise ValueError(f"papersim_shadow_role_unknown:{role}")
        digest = spec.spec_sha256()
        if digest in seen:
            continue
        seen.add(digest)
        ordered.append((role, spec))
    return tuple(ordered)


#: A shadow quote whose fee rate the active decision could not resolve
#: cannot be judged by the pure gate; every check fails explicitly.
_GATE_FEE_UNRESOLVED = "fee_unresolved"

#: A family raising unexpectedly while predicting one market is an
#: optimizer fault: recorded as a refusal, never a scan stopper.
_QUOTE_ERROR = "quote_error"


def _pure_gate(quote: shadow.ShadowQuote, request: Any, fee_rate) -> Any:
    """The pure gate judgement of one shadow quote (04 s10 step 3).

    ``gates.evaluate_candidate`` over the SAME raw book, news and
    liquidity the active decision used, with ``under_risk=False`` and
    the active decision's own resolved fee rate -- no bankroll, no
    team caps, no portfolio state.  Returns ``None`` when the fee rate
    is unresolved (the caller records the failure explicitly).
    """

    from polymarket_alpha_lab.papersim import gates

    if fee_rate is None:
        return None
    return gates.evaluate_candidate(
        {"model_median": quote.model_median,
         "qs_variants": list(quote.qs_variants)},
        list(request.book), gates.SIDE_ASK,
        gamma_liquidity=request.gamma_liquidity, news=request.news,
        under_risk=False, fee_rate=float(fee_rate))


class _ShadowRunner:
    """The per-scan shadow driver (pure over its explicit inputs).

    Fits each distinct spec once (cached by digest; roles are frozen
    for the run), predicts per market, judges through :func:`_pure_gate`
    and returns one :class:`ShadowBinding` per (record, spec).  The
    wall-clock budget drops NEW shadow work when exceeded -- the
    active path is never touched (plan 05 section 7: drop the shadow
    first, never the active).  Counts and seconds are attempt
    properties reported through the run-scan return value, never the
    time-free scan parameters.
    """

    def __init__(self, source, records, *, budget_seconds=None, clock=None) -> None:
        self.source = source
        self.records_by_lineage: dict[str, list[MarketRecord]] = {}
        for record in records:
            self.records_by_lineage.setdefault(record.lineage, []).append(record)
        self.budget_seconds = budget_seconds
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self.fitted: dict[str, object] = {}
        self.materials: dict[str, dict[str, dict]] = {}
        self.stats = {"fits": 0, "predicts": 0, "refusals": 0,
                      "skipped_no_source": 0, "skipped_budget": 0}
        self.budget_exceeded = False
        self.seconds = 0.0

    def _over_budget(self) -> bool:
        if self.budget_exceeded:
            return True
        if self.budget_seconds is None:
            return False
        if self._clock() - self._started <= self.budget_seconds:
            return False
        self.budget_exceeded = True
        return True

    def finish(self) -> None:
        self.seconds = self._clock() - self._started

    def bind(self, lineage: str, role: str, spec: Any, record: MarketRecord,
             request: Any, fee_rate) -> ShadowBinding | None:
        if self.source is None:
            self.stats["skipped_no_source"] += 1
            return None
        if self._over_budget():
            self.stats["skipped_budget"] += 1
            return None
        digest = spec.spec_sha256()
        if digest not in self.fitted:
            fitted = self.source.fitted_for(
                lineage, spec, tuple(self.records_by_lineage.get(lineage, ())))
            self.fitted[digest] = fitted
            if fitted is not None:
                self.stats["fits"] += 1
                self.materials.setdefault(lineage, {})[digest] = \
                    fitted.materials.params_entry()
        fitted = self.fitted[digest]
        if fitted is None:
            quote = shadow.ShadowQuote(None, None, shadow.REFUSAL_FIT_MATERIALS_MISSING)
        else:
            try:
                quote = self.source.quote_for(fitted, record, request)
            except (TypeError, ValueError):
                quote = shadow.ShadowQuote(None, None, _QUOTE_ERROR)
            self.stats["predicts"] += 1
        if quote.refusal_reason is not None:
            self.stats["refusals"] += 1
            return ShadowBinding(
                candidate_key=record.candidate_key, lineage=lineage,
                spec_sha256=digest, role_at_prediction=role.upper(),
                probability=None, variant_quotes=None,
                refusal_reason=quote.refusal_reason, gate_pass=False,
                gate_reason=None, hypothetical_fill_price=None,
                predicted_at=record.evaluated_at)
        report = _pure_gate(quote, request, fee_rate)
        if report is None:
            gate_pass, gate_reason = False, _GATE_FEE_UNRESOLVED
            fill = None
        else:
            gate_pass = bool(report.decision.passed)
            gate_reason = ",".join(report.decision.failed) or None
            fill = None if report.entry_price is None else float(report.entry_price)
        return ShadowBinding(
            candidate_key=record.candidate_key, lineage=lineage,
            spec_sha256=digest, role_at_prediction=role.upper(),
            probability=quote.model_median, variant_quotes=quote.qs_variants,
            refusal_reason=None, gate_pass=gate_pass, gate_reason=gate_reason,
            hypothetical_fill_price=fill, predicted_at=record.evaluated_at)


# ---------------------------------------------------------------------------
# Assembly (the bit-identical core)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanAssembly:
    """One complete, validated scan submission plus its by-products."""

    submission: store.ScanSubmission
    model_versions: tuple[str, ...] = ()
    suppressed_entries: tuple[str, ...] = ()
    admission: AdmissionReport | None = None
    shadows: tuple[ShadowBinding, ...] = ()
    shadow_stats: Mapping[str, int] = field(default_factory=dict)
    shadow_seconds: float = 0.0


def assemble_scan(config: ScanConfig, records: Sequence[MarketRecord], *,
                  admissions: AdmissionReport,
                  portfolio: PortfolioState,
                  started_at: datetime.datetime,
                  finished_at: datetime.datetime,
                  code_commit: str,
                  specs: Mapping[str, LineageSpecs] | None = None,
                  operation_key: str | None = None,
                  shadow_source=None,
                  shadow_budget_seconds: float | None = None,
                  preparation: Mapping[str, Any] | None = None) -> ScanAssembly:
    """Bind every record through the frozen stack into one submission.

    Both binding paths (``specs`` given = provider-wired; ``specs=None``
    = the unwired reference) go through the SAME ``replay.bind_*``
    calls; the only divergence is where ``model_version`` comes from
    (``specs[lineage].active.model_version()`` vs the decision's own
    constant).  Accepted candidates on paused queues bind WITHOUT their
    entry (scanning, candidate recording and marks continue; only the
    entry and its cash leg are suppressed) -- the suppression reason is
    recorded in the scan parameters' admission section.

    MO-7: ``shadow_source`` computes one pure-gate-only prediction per
    NON-ACTIVE role spec (04 section 10 step 3).  The bindings ride on
    ``ScanAssembly.shadows`` (never an entry, never a cash event); the
    parameters gain the shadow fit-materials inventory only when at
    least one role fitted (an empty registry keeps the parameters
    byte-identical to B1), and the counts/seconds are attempt
    properties returned to the caller.
    """
    config.validate()
    params: dict[str, Any] = {}
    if specs is not None:
        params.update(provider_params(specs))
    if preparation is not None:
        params["preparation"] = preparation
    params["admission"] = {"paused_queues": sorted(admissions.paused_queues)}
    candidates: list[store.CandidateSubmission] = []
    model_versions: list[str] = []
    suppressed: list[str] = []
    shadows: list[ShadowBinding] = []
    shadow_runner = _ShadowRunner(shadow_source, records,
                                  budget_seconds=shadow_budget_seconds)
    for record in records:
        version = None
        if record.prepared_provenance is not None:
            active = specs[record.lineage].active
            version = active.model_version()
            if (record.prepared_provenance.get("model_spec_sha256"),
                    record.prepared_provenance.get("model_version")) != (active.spec_sha256(), version):
                raise ScanError("papersim_prepare_scan_spec_conflict")
        if record.construction is not None:
            candidates.append(_bind_construction(record, model_version=version))
            continue
        request = replay.request_from_replay_bundle(record.request_bundle)
        event_key, event_start_at = bind_event_key(record.events)
        event_scope = (event_key or f"market:{record.market_id}") if preparation is not None else None
        request = dataclasses.replace(
            request,
            bankroll=portfolio.bankroll,
            cash=portfolio.cash,
            reserved=portfolio.reserved,
            held_sides=(portfolio.held_by_event.get(event_scope, ()) if event_scope else portfolio.held_sides),
            team=record.team,
            team_committed=portfolio.team_committed.get(record.team, 0.0),
        )
        decision = pipeline.decide_entry(request)
        # MO-7 (B1 plan D7, second wiring point -- wired): the role
        # loop over active+baseline+candidates+guard, deduplicated by
        # spec digest.  The active role below prices and enters exactly
        # as B1; every OTHER distinct spec produces a pure-gate-only
        # shadow prediction (never an entry, never a cash event).  With
        # the registry empty the deduplicated set collapses to the
        # active spec alone, so nothing is shadowed here.
        role_pairs = (role_spec_pairs(specs[record.lineage]) if specs is not None
                      else (("active", None),))
        for role, spec in role_pairs:
            if role != "active":
                binding = shadow_runner.bind(record.lineage, role, spec,
                                             record, request, decision.fee_rate)
                if binding is not None:
                    shadows.append(binding)
                continue
            if specs is not None:
                model_version = specs[record.lineage].active.model_version()
            else:
                model_version = decision.model_version
            entry = None
            if decision.accepted and record.queue not in admissions.paused_queues:
                entry = replay.bind_entry(decision, entered_at=(
                    max(started_at, record.evaluated_at)
                    if record.prepared_provenance is not None else record.evaluated_at))
            elif decision.accepted:
                suppressed.append(record.candidate_key)
            candidates.append(replay.bind_candidate(
                request,
                decision,
                team=record.team,
                candidate_key=record.candidate_key,
                side=record.side,
                market_kind=record.market_kind,
                evaluated_at=record.evaluated_at,
                market_ref_kind=record.market_ref_kind,
                market_id=record.market_id,
                market_slug=record.market_slug,
                condition_id=record.condition_id,
                yes_token_id=record.yes_token_id,
                no_token_id=record.no_token_id,
                market_type=record.market_type,
                model_name=record.model_name,
                model_version=model_version,
                median_probability=record.median_probability,
                variant_quotes=record.variant_quotes,
                snapshot_refs=record.snapshot_refs,
                book_snapshot_sha256=record.book_snapshot_sha256,
                decision_snapshot_sha256=record.decision_snapshot_sha256,
                source_kind=record.source_kind,
                source_ref=record.source_ref,
                event_key=event_key,
                event_start_at=event_start_at,
                entry=entry,
                prepared_buy_token=request.buy_token if record.prepared_provenance is not None else None,
            ))
            model_versions.append(model_version)
            if entry is not None:
                portfolio = portfolio.with_entry(
                    team=record.team,
                    side=request.side,
                    stake=float(entry.stake),
                    fee=float(entry.entry_fee),
                    event_scope=event_scope,
                )
    shadow_runner.finish()
    if shadow_runner.materials:
        params["shadow"] = {"materials": {
            lineage: {digest: shadow_runner.materials[lineage][digest]
                      for digest in sorted(shadow_runner.materials[lineage])}
            for lineage in sorted(shadow_runner.materials)}}
    scan_row = replay.bind_scan_run(
        team=config.team,
        started_at=started_at,
        finished_at=finished_at,
        code_commit=code_commit,
        params=json.dumps(params, sort_keys=True, separators=(",", ":")),
        operator=config.operator,
    )
    if operation_key is not None:
        scan_row = dataclasses.replace(scan_row, operation_key=operation_key)
    submission = store.ScanSubmission(scan=scan_row, candidates=tuple(candidates))
    submission.validate()
    return ScanAssembly(submission=submission, model_versions=tuple(model_versions),
                        suppressed_entries=tuple(suppressed), admission=admissions,
                        shadows=tuple(shadows),
                        shadow_stats=dict(shadow_runner.stats),
                        shadow_seconds=shadow_runner.seconds)


def _bind_construction(record: MarketRecord, *, model_version=None) -> store.CandidateSubmission:
    construction = record.construction or {}
    constructor = str(construction.get("constructor", replay.CONSTRUCTOR_SOCCER))
    event_key, event_start_at = bind_event_key(record.events)
    if constructor == replay.CONSTRUCTOR_MANAGED_PREPARATION:
        if record.prepared_provenance is None or model_version is None:
            raise ScanError("papersim_prepare_scan_failure_unverified")
        return replay.bind_preparation_failure(
            team=record.team, candidate_key=record.candidate_key, evaluated_at=record.evaluated_at,
            side=record.side, market_kind=record.market_kind, market_id=record.market_id,
            market_slug=record.market_slug, condition_id=record.condition_id,
            yes_token_id=record.yes_token_id, no_token_id=record.no_token_id,
            market_type=record.market_type, model_name=record.model_name, model_version=model_version,
            snapshot_refs=record.snapshot_refs, event_key=event_key, event_start_at=event_start_at,
            witness=construction["witness"],
        )
    return replay.bind_construction_failure(
        team=record.team,
        candidate_key=record.candidate_key,
        evaluated_at=record.evaluated_at,
        inputs=dict(construction.get("inputs", {})),
        side=record.side,
        market_kind=record.market_kind,
        market_id=record.market_id,
        market_slug=record.market_slug,
        condition_id=record.condition_id,
        market_type=record.market_type,
        model_name=record.model_name,
        snapshot_refs=record.snapshot_refs,
        constructor=constructor,
        **({"model_version": model_version, "yes_token_id": record.yes_token_id,
            "no_token_id": record.no_token_id, "event_key": event_key, "event_start_at": event_start_at}
           if model_version is not None else {}),
    )


# ---------------------------------------------------------------------------
# The stateful driver
# ---------------------------------------------------------------------------

def run_scan(config: ScanConfig, records: Sequence[MarketRecord], *,
             conn,
             repo_root,
             provider,
             now_utc: Callable[[], datetime.datetime],
             evaluation_candidates: Iterable[Any] = (),
             operation_key: str | None = None,
             attempt: int = 1,
             git_bin: str = "git",
             shadow_source=None,
             shadow_budget_seconds: float | None = None) -> dict:
    """Run one complete scan against the store (receipts + one atomic write).

    Order: clean-worktree commit check (FX-03, refuses before ANY
    write), STARTED receipt, latch read + pause evaluation + PAUSE
    evidence rows for fresh escalations, portfolio snapshot, assembly,
    ``record_scan`` (one transaction), SUCCEEDED receipt with the scan
    id.  A failure appends a FAILED receipt and re-raises; the scan
    write itself is all-or-nothing, so no half scan ever survives.
    """
    from polymarket_alpha_lab.papersim import store_psycopg

    config.validate()
    code_commit = require_clean_worktree(repo_root, git_bin=git_bin)
    started_at = now_utc()
    receipt_key = operation_key or (
        f"{SCAN_COMMAND}|{config.team}|{started_at.astimezone(datetime.UTC):%Y%m%dT%H%M%SZ}|b1"
    )

    def _receipt(phase: str, *, scan_id: int | None = None, error_code: str | None = None,
                 output_evidence: Sequence[str] = ()) -> None:
        store_psycopg.record_command_run_event(conn, store.CommandRunEventSubmission(
            operation_key=receipt_key,
            attempt=attempt,
            phase=phase,
            command=SCAN_COMMAND,
            occurred_at=now_utc(),
            code_commit=code_commit,
            output_evidence=list(output_evidence),
            error_code=error_code,
            scan_id=scan_id,
        ))

    _receipt("STARTED")
    try:
        latched = store_psycopg.latched_pauses(conn)
        admissions = pause_admissions(evaluation_candidates, config.queues, latched)
        for decision in admissions.escalations:
            rule = (store.QUEUE_CONTROL_RULE_FX17 if decision.review_reasons
                    else store.QUEUE_CONTROL_RULE_BLUEPRINT)
            store_psycopg.record_queue_control_event(conn, store.QueueControlSubmission(
                queue_id=decision.queue_id,
                control_kind="PAUSE",
                rule=rule,
                rule_version=queue_policy.QUEUE_POLICY_RULES_VERSION,
                metrics={
                    "action": decision.action,
                    "review_reasons": list(decision.review_reasons),
                    "stop_reasons": list(decision.stop_reasons),
                    "keeps_latched_pause": decision.keeps_latched_pause,
                },
                observed_at=started_at,
            ))
        portfolio = read_portfolio_state(conn)
        specs = consult_provider(provider, records)
        assembly = assemble_scan(
            config,
            records,
            admissions=admissions,
            portfolio=portfolio,
            started_at=started_at,
            finished_at=now_utc(),
            code_commit=code_commit,
            specs=specs,
            operation_key=receipt_key,
            shadow_source=shadow_source,
            shadow_budget_seconds=shadow_budget_seconds,
        )
        written = store_psycopg.record_scan(conn, assembly.submission)
        # MO-7 (seam 4, plan review decision 2): the shadow write rides
        # AFTER the scan's own atomic write, in ONE separate
        # transaction; a failure is recorded without failing the scan
        # (04 section 11: an optimizer fault never stops the scan).
        shadow_report = {"shadow_rows": 0, "shadow_inserted": 0,
                         "shadow_error": None}
        if assembly.shadows:
            try:
                written_shadows = store_psycopg.record_shadow_predictions(
                    conn, assembly.shadows, written["candidate_ids"])
                shadow_report = {"shadow_rows": written_shadows["rows"],
                                 "shadow_inserted": written_shadows["inserted"],
                                 "shadow_error": None}
            except Exception as error:  # noqa: BLE001 - recorded, never a scan stopper
                shadow_report = {
                    "shadow_rows": 0, "shadow_inserted": 0,
                    "shadow_error": str(getattr(error, "code", type(error).__name__))}
        # The SUCCEEDED receipt stays idempotent under its scheduling
        # key: the evidence carries the scan key only ("inserted" -- and
        # the shadow counts below -- are properties of THIS attempt's
        # return value, not of the row).
        _receipt("SUCCEEDED", scan_id=written["scan_id"],
                 output_evidence=(written["scan_key"],))
        return {
            "scan_id": written["scan_id"],
            "scan_key": written["scan_key"],
            "inserted": written["inserted"],
            "candidate_ids": written["candidate_ids"],
            "bankroll_event_keys": written["bankroll_event_keys"],
            "paused_queues": sorted(admissions.paused_queues),
            "suppressed_entries": list(assembly.suppressed_entries),
            "escalations": [decision.queue_id for decision in admissions.escalations],
            "model_versions": sorted(set(assembly.model_versions)),
            "code_commit": code_commit,
            "shadow_rows": shadow_report["shadow_rows"],
            "shadow_inserted": shadow_report["shadow_inserted"],
            "shadow_error": shadow_report["shadow_error"],
            "shadow_stats": dict(assembly.shadow_stats),
            "shadow_seconds": assembly.shadow_seconds,
        }
    except Exception as error:  # noqa: BLE001 - the receipt is the record
        try:
            _receipt("FAILED", error_code=str(getattr(error, "code", type(error).__name__)))
        finally:
            raise
