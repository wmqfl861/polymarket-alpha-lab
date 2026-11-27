"""D1 candidate-by-candidate comparison: Path L (frozen) vs Path N (new).

The join keys are market identity + side + kind.  Compared fields:
decision, side (in the key), stake/size, gate outcomes, quotes,
snapshot digests and as-of stamps.  Every difference is classified into
the THREE FIXED classes of the accepted plan section 6; the class-3
pre-registered items are frozen here and never expanded silently:

1. ``library_defect`` -- the new library is wrong; fix the new code and
   the day count restarts (the code commit changes).
2. ``frozen_script_legacy_drift`` -- a frozen-script defect or legacy
   ad-hoc drift; the new code is authoritative and the historical
   impact is recorded.
3. ``intentional_rule_difference`` -- a pre-registered intentional
   difference; the owner decides.  Pre-registered items:
   FX-26 event keys, provider-registration scan params, the manual-news
   layer absent in the new code, basketball-not-modeled.

Honesty rules that this module enforces mechanically:

- Manual-news inputs are UNKNOWN/unloadable (the frozen loop's news
  concordance was agent research): news fields become explicit UNKNOWN
  rows with provenance annotations and are NEVER scored as agreement.
- Basketball markets have NO frozen decision logic ("Basketball = NO
  MODEL", round46/boards46.py; NBA was shadow-only, round47): those
  rows are CAPTURED-NOT-COMPARED -- grouped separately, excluded from
  agreement counts, never faked.
- Comparator equality is never fabricated: a field equal on both sides
  only counts when BOTH sides carry a known value; one-sided or absent
  values are ``unknown_left`` / ``unknown_right`` / ``unknown_both``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
from typing import Any

__all__ = [
    "BASKETBALL_SCOPE_KEYS",
    "ComparatorCandidate",
    "DAY_DIFF_VERSION",
    "DIFF_CLASS_FROZEN_DRIFT",
    "DIFF_CLASS_INTENTIONAL",
    "DIFF_CLASS_LIBRARY_DEFECT",
    "DIFF_PRE_REGISTERED_ITEMS",
    "DayDiff",
    "DiffRow",
    "FieldDelta",
    "NEWS_PROVENANCE_ANNOTATION",
    "candidate_key",
    "compare_candidates",
    "diff_day",
    "row_from_mapping",
]

#: The three fixed difference classes (plan section 6).
DIFF_CLASS_LIBRARY_DEFECT = "library_defect"
DIFF_CLASS_FROZEN_DRIFT = "frozen_script_legacy_drift"
DIFF_CLASS_INTENTIONAL = "intentional_rule_difference"

#: Pre-registered class-3 items; extending this tuple is a plan change.
DIFF_PRE_REGISTERED_ITEMS = (
    "fx26_event_keys",
    "provider_registration_scan_params",
    "manual_news_layer_absent",
    "basketball_not_modeled",
)

#: Provenance annotation carried on every manual-news UNKNOWN row.
NEWS_PROVENANCE_ANNOTATION = (
    "manual news-concordance inputs are agent research recorded in the "
    "round docstrings (e.g. round33/raw/write_candidates33.py, round43 "
    "headers); they are unknown/unloadable, marked UNKNOWN and never "
    "scored as agreement"
)

#: League keys with no frozen decision logic (round46 "Basketball = NO
#: MODEL"; round47 NBA shadow-monitoring-only).  Rows in these leagues
#: are captured-not-compared.
BASKETBALL_SCOPE_KEYS = frozenset({"nba"})

DAY_DIFF_VERSION = 1

#: Field names compared candidate-by-candidate (plan section 4).
COMPARED_FIELDS = ("decision", "stake_usd", "quote", "snapshot_digest", "as_of")


class DiffError(ValueError):
    """One comparison refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def normalize_league(raw: Any) -> str | None:
    key = str(raw or "").strip().lower()
    return key or None


def is_basketball(league: Any) -> bool:
    normalized = normalize_league(league)
    return normalized is not None and any(
        normalized == scope or normalized.endswith(f"/{scope}")
        for scope in BASKETBALL_SCOPE_KEYS
    )


@dataclass(frozen=True)
class ComparatorCandidate:
    """One side's normalized view of one candidate.

    Unknown fields are ``None`` (never 0, never ""); gate outcomes map
    gate names to ``True``/``False``/``None`` (unknown).  ``news`` is
    the manual-news verdict text for Path L rows and ``None`` for Path
    N rows (the layer is absent in the new code -- pre-registered).
    """

    market_id: str
    side: str
    kind: str
    league: str | None = None
    question: str | None = None
    decision: str | None = None          # "enter" / "no_entry" / None unknown
    stake_usd: float | None = None
    quote: float | None = None           # entry ask / vwap as decided
    gate_outcomes: Mapping[str, bool | None] = field(default_factory=dict)
    snapshot_digest: str | None = None   # book snapshot sha256
    as_of: str | None = None
    news: str | None = None
    provenance: str = ""

    def key(self) -> tuple[str, str, str]:
        return candidate_key(self)


def candidate_key(row: ComparatorCandidate) -> tuple[str, str, str]:
    """Join key: market identity + side + kind (normalized text)."""
    return (str(row.market_id).strip(), str(row.side).strip().lower(),
            str(row.kind).strip().lower())


def row_from_mapping(raw: Mapping[str, Any], *, provenance: str) -> ComparatorCandidate:
    """Build one comparator row from a plain mapping (strict).

    ``gate_outcomes`` values pass through as True/False/None only; any
    other value is a refusal (null/0/unknown stay distinct).
    """
    if not isinstance(raw, Mapping):
        raise DiffError("diff_row_invalid", "object required")
    gates_raw = raw.get("gate_outcomes")
    if gates_raw is None:
        gates_raw = {}
    if not isinstance(gates_raw, Mapping):
        raise DiffError("diff_row_invalid", "gate_outcomes must be an object")
    gates: dict[str, bool | None] = {}
    for name, value in gates_raw.items():
        if value is None or isinstance(value, bool):
            gates[str(name)] = value
        else:
            raise DiffError("diff_gate_outcome_invalid", f"{name}: {value!r}")

    def _number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None

    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text.strip() else None

    for required in ("market_id", "side", "kind"):
        if _text(raw.get(required)) is None:
            raise DiffError("diff_row_field_invalid", required)
    return ComparatorCandidate(
        market_id=str(raw["market_id"]),
        side=str(raw["side"]),
        kind=str(raw["kind"]),
        league=normalize_league(raw.get("league")),
        question=_text(raw.get("question")),
        decision=_text(raw.get("decision")),
        stake_usd=_number(raw.get("stake_usd")),
        quote=_number(raw.get("quote")),
        gate_outcomes=gates,
        snapshot_digest=_text(raw.get("snapshot_digest")),
        as_of=_text(raw.get("as_of")),
        news=_text(raw.get("news")),
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Field deltas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDelta:
    """One field's comparison outcome; ``status`` never says equal on
    unknown material."""

    field: str
    status: str  # equal | different | unknown_left | unknown_right | unknown_both
    left: Any = None
    right: Any = None

    @property
    def unknown(self) -> bool:
        return self.status.startswith("unknown")


def _field_status(left: Any, right: Any) -> str:
    left_known = left is not None
    right_known = right is not None
    if left_known and right_known:
        return "equal" if left == right else "different"
    if left_known:
        return "unknown_right"
    if right_known:
        return "unknown_left"
    return "unknown_both"


def _gate_deltas(left: Mapping[str, bool | None],
                 right: Mapping[str, bool | None]) -> list[FieldDelta]:
    deltas: list[FieldDelta] = []
    for name in sorted(set(left) | set(right)):
        left_value = left.get(name)
        right_value = right.get(name)
        status = _field_status(left_value, right_value)
        deltas.append(FieldDelta(field=f"gate:{name}", status=status,
                                 left=left_value, right=right_value))
    return deltas


@dataclass(frozen=True)
class DiffRow:
    """One joined candidate pair (or one-sided row) with its deltas."""

    key: tuple[str, str, str]
    left: ComparatorCandidate | None
    right: ComparatorCandidate | None
    deltas: tuple[FieldDelta, ...] = ()
    difference_class: str | None = None
    annotations: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()

    def as_mapping(self) -> dict[str, Any]:
        def _side(row: ComparatorCandidate | None) -> dict[str, Any] | None:
            if row is None:
                return None
            return {
                "market_id": row.market_id, "side": row.side, "kind": row.kind,
                "league": row.league, "decision": row.decision,
                "stake_usd": row.stake_usd, "quote": row.quote,
                "gate_outcomes": dict(row.gate_outcomes),
                "snapshot_digest": row.snapshot_digest, "as_of": row.as_of,
                "news": row.news, "provenance": row.provenance,
            }
        return {
            "key": list(self.key),
            "path_l": _side(self.left),
            "path_n": _side(self.right),
            "deltas": [{"field": delta.field, "status": delta.status,
                        "left": delta.left, "right": delta.right}
                       for delta in self.deltas],
            "difference_class": self.difference_class,
            "annotations": list(self.annotations),
            "unknown_fields": list(self.unknown_fields),
        }


def compare_candidates(left: ComparatorCandidate,
                       right: ComparatorCandidate) -> tuple[FieldDelta, ...]:
    """All field deltas of one joined pair (decision through as-of)."""
    deltas = [_compare_scalar(name, getattr(left, name), getattr(right, name))
              for name in COMPARED_FIELDS]
    news_delta = _compare_scalar("news", left.news, right.news)
    deltas.append(news_delta)
    deltas.extend(_gate_deltas(left.gate_outcomes, right.gate_outcomes))
    return tuple(deltas)


def _compare_scalar(name: str, left: Any, right: Any) -> FieldDelta:
    return FieldDelta(field=name, status=_field_status(left, right),
                      left=left, right=right)


def _classify(deltas: Sequence[FieldDelta], *, left_only: bool,
              right_only: bool) -> str | None:
    """Assign one of the three fixed classes (or None: no difference).

    Only ``different`` statuses are differences: an unknown field is
    annotated, never classified.  Presence mismatches: a frozen-only
    candidate is legacy ad-hoc surface the new pipeline lacks (class
    2); a new-only candidate is a possible new-code defect (class 1).
    Field differences on joined rows default to class 1 (library
    defect -- the new code must explain itself first); the day
    reviewer may reclassify into class 3 ONLY through a pre-registered
    item, never by relaxing a gate.
    """
    if left_only:
        return DIFF_CLASS_FROZEN_DRIFT
    if right_only:
        return DIFF_CLASS_LIBRARY_DEFECT
    if any(delta.status == "different" for delta in deltas):
        return DIFF_CLASS_LIBRARY_DEFECT
    return None


# ---------------------------------------------------------------------------
# Day diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayDiff:
    """The whole day's comparison: rows, classes and honest counts."""

    rows: tuple[DiffRow, ...]
    path_l_only: tuple[DiffRow, ...]
    path_n_only: tuple[DiffRow, ...]
    captured_not_compared: tuple[DiffRow, ...]   # basketball rows
    unknown_news_rows: tuple[DiffRow, ...]
    class_counts: Mapping[str, int]
    agreement_fields: int      # known-equal fields only
    compared_fields: int       # fields with both sides known
    identical_rows: int

    def summary(self) -> dict[str, Any]:
        return {
            "day_diff_version": DAY_DIFF_VERSION,
            "rows": len(self.rows),
            "path_l_only": len(self.path_l_only),
            "path_n_only": len(self.path_n_only),
            "captured_not_compared": len(self.captured_not_compared),
            "unknown_news_rows": len(self.unknown_news_rows),
            "class_counts": dict(self.class_counts),
            "agreement_fields": self.agreement_fields,
            "compared_fields": self.compared_fields,
            "identical_rows": self.identical_rows,
            "pre_registered_intentional_items": list(DIFF_PRE_REGISTERED_ITEMS),
        }

    def as_document(self) -> dict[str, Any]:
        document = self.summary()
        document["rows"] = [row.as_mapping() for row in self.rows]
        document["news_provenance_annotation"] = NEWS_PROVENANCE_ANNOTATION
        return document


def diff_day(path_l: Sequence[ComparatorCandidate],
             path_n: Sequence[ComparatorCandidate]) -> DayDiff:
    """Compare one day's two candidate lists (pure, never inventive).

    Join is by (market_id, side, kind); basketball-league rows on
    either side go to captured-not-compared regardless of matching;
    manual-news fields are UNKNOWN by construction and annotated; the
    agreement counters count only fields known on BOTH sides.
    """
    left_by_key: dict[tuple[str, str, str], ComparatorCandidate] = {}
    for row in path_l:
        if row.key() in left_by_key:
            raise DiffError("diff_duplicate_key",
                            f"path_l: {'|'.join(row.key())}")
        left_by_key[row.key()] = row
    right_by_key: dict[tuple[str, str, str], ComparatorCandidate] = {}
    for row in path_n:
        if row.key() in right_by_key:
            raise DiffError("diff_duplicate_key",
                            f"path_n: {'|'.join(row.key())}")
        right_by_key[row.key()] = row

    rows: list[DiffRow] = []
    left_only: list[DiffRow] = []
    right_only: list[DiffRow] = []
    captured: list[DiffRow] = []
    unknown_news: list[DiffRow] = []
    class_counts: dict[str, int] = {DIFF_CLASS_LIBRARY_DEFECT: 0,
                                    DIFF_CLASS_FROZEN_DRIFT: 0,
                                    DIFF_CLASS_INTENTIONAL: 0}
    agreement = 0
    compared = 0
    identical = 0

    for key in sorted(set(left_by_key) | set(right_by_key)):
        left = left_by_key.get(key)
        right = right_by_key.get(key)
        basketball = (left is not None and is_basketball(left.league)) or \
                     (right is not None and is_basketball(right.league))
        if basketball:
            row = DiffRow(key=key, left=left, right=right,
                          difference_class=None,
                          annotations=(
                              "basketball_not_modeled: captured with terms "
                              "preserved, no frozen decision logic exists "
                              "(round46 boards46 'Basketball = NO MODEL'; "
                              "round47 NBA shadow-only)",
                          ))
            captured.append(row)
            rows.append(row)
            continue
        if left is not None and right is not None:
            deltas = compare_candidates(left, right)
            annotations: tuple[str, ...] = ()
            unknown_fields = tuple(delta.field for delta in deltas
                                   if delta.unknown)
            news_delta = next(delta for delta in deltas if delta.field == "news")
            if news_delta.status in ("unknown_left", "unknown_right",
                                     "unknown_both"):
                annotations = (NEWS_PROVENANCE_ANNOTATION,)
                unknown_news.append(DiffRow(key=key, left=left, right=right,
                                            deltas=deltas,
                                            annotations=annotations,
                                            unknown_fields=unknown_fields))
            known_both = [delta for delta in deltas
                          if not delta.status.startswith("unknown")]
            different = [delta for delta in deltas
                         if delta.status == "different"]
            compared += len(known_both)
            agreement += sum(1 for delta in known_both
                             if delta.status == "equal")
            difference_class = _classify(deltas, left_only=False, right_only=False)
            if difference_class is not None:
                class_counts[difference_class] += 1
            elif known_both:
                # equality is claimed ONLY when both sides carried values
                identical += 1
            else:
                annotations = annotations + (
                    "unverifiable row: no compared field is known on both sides",
                )
            rows.append(DiffRow(key=key, left=left, right=right, deltas=deltas,
                                difference_class=difference_class,
                                annotations=annotations,
                                unknown_fields=unknown_fields))
        elif left is not None:
            row = DiffRow(key=key, left=left, right=None,
                          difference_class=DIFF_CLASS_FROZEN_DRIFT,
                          annotations=(
                              "frozen-only candidate: legacy ad-hoc surface; "
                              "new code authoritative, historical impact "
                              "recorded",
                          ))
            left_only.append(row)
            rows.append(row)
            class_counts[DIFF_CLASS_FROZEN_DRIFT] += 1
        else:
            row = DiffRow(key=key, left=None, right=right,
                          difference_class=DIFF_CLASS_LIBRARY_DEFECT,
                          annotations=(
                              "new-only candidate: possible new-code defect; "
                              "day count restarts if confirmed",
                          ))
            right_only.append(row)
            rows.append(row)
            class_counts[DIFF_CLASS_LIBRARY_DEFECT] += 1

    return DayDiff(
        rows=tuple(rows),
        path_l_only=tuple(left_only),
        path_n_only=tuple(right_only),
        captured_not_compared=tuple(captured),
        unknown_news_rows=tuple(unknown_news),
        class_counts=class_counts,
        agreement_fields=agreement,
        compared_fields=compared,
        identical_rows=identical,
    )
