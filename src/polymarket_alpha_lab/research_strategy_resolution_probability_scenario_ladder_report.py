"""Pure report for resolution probability scenario ladders."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from typing import Any


__all__ = (
    "DEFAULT_RESEARCH_STRATEGY_RESOLUTION_PROBABILITY_SCENARIO_LADDER_REPORT_CONFIG_VERSION",
    "RESOLUTION_PROBABILITY_SCENARIO_LADDER_STATUSES",
    "ResearchStrategyResolutionProbabilityScenarioLadderConfig",
    "ResearchStrategyResolutionProbabilityScenarioLadderInput",
    "ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount",
    "ResearchStrategyResolutionProbabilityScenarioLadderReport",
    "ResearchStrategyResolutionProbabilityScenarioLadderRow",
    "build_research_strategy_resolution_probability_scenario_ladder_report",
    "research_strategy_resolution_probability_scenario_ladder_report_digest",
    "research_strategy_resolution_probability_scenario_ladder_report_payload",
    "validate_research_strategy_resolution_probability_scenario_ladder_report_payload",
)


DEFAULT_RESEARCH_STRATEGY_RESOLUTION_PROBABILITY_SCENARIO_LADDER_REPORT_CONFIG_VERSION = (
    "research-strategy-resolution-probability-scenario-ladder-report-v0"
)
RESOLUTION_PROBABILITY_SCENARIO_LADDER_STATUSES = ("pass", "watch", "block")
ZERO = Decimal("0.000000")
ONE = Decimal("1.000000")
RATIO_QUANTUM = Decimal("0.000001")
HEX_DIGITS = frozenset("0123456789abcdef")
COMPONENT_REASON_PRIORITY = (
    "base_evidence_strength_block",
    "cost_drag_block",
    "liquidity_reliability_block",
    "resolution_ambiguity_block",
    "specialist_confidence_block",
    "base_evidence_strength_watch",
    "cost_drag_watch",
    "liquidity_reliability_watch",
    "resolution_ambiguity_watch",
    "specialist_confidence_watch",
)
SURFACE_FRAGMENTS = (
    "raw",
    "candidate" + "_" + "id",
    "mar" + "ket" + "_" + "id",
    "mar" + "ket" + "_" + "slug",
    "slug",
    "quest" + "ion",
    "u" + "rl",
    "te" + "xt",
    "d" + "sn",
    "ta" + "ble",
    "tok" + "en",
    "wal" + "let",
    "or" + "der",
    "tra" + "de",
    "li" + "ve",
    "secret",
    "credential",
    "http",
    "://",
)


@dataclass(frozen=True)
class ResearchStrategyResolutionProbabilityScenarioLadderConfig:
    config_version: str = (
        DEFAULT_RESEARCH_STRATEGY_RESOLUTION_PROBABILITY_SCENARIO_LADDER_REPORT_CONFIG_VERSION
    )
    min_pass_base_evidence_strength: Decimal = Decimal("0.700000")
    min_watch_base_evidence_strength: Decimal = Decimal("0.450000")
    min_pass_liquidity_reliability: Decimal = Decimal("0.650000")
    min_watch_liquidity_reliability: Decimal = Decimal("0.400000")
    min_pass_specialist_confidence: Decimal = Decimal("0.700000")
    min_watch_specialist_confidence: Decimal = Decimal("0.450000")
    max_pass_cost_drag: Decimal = Decimal("0.120000")
    max_watch_cost_drag: Decimal = Decimal("0.300000")
    max_pass_resolution_ambiguity: Decimal = Decimal("0.150000")
    max_watch_resolution_ambiguity: Decimal = Decimal("0.350000")
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self) -> None:
        _require_exact_type(
            self,
            ResearchStrategyResolutionProbabilityScenarioLadderConfig,
            "config",
        )
        _require_public_label("config_version", self.config_version)
        if (
            self.config_version
            != DEFAULT_RESEARCH_STRATEGY_RESOLUTION_PROBABILITY_SCENARIO_LADDER_REPORT_CONFIG_VERSION
        ):
            raise ValueError("config_version must be supported")
        for field_name in (
            "min_pass_base_evidence_strength",
            "min_watch_base_evidence_strength",
            "min_pass_liquidity_reliability",
            "min_watch_liquidity_reliability",
            "min_pass_specialist_confidence",
            "min_watch_specialist_confidence",
            "max_pass_cost_drag",
            "max_watch_cost_drag",
            "max_pass_resolution_ambiguity",
            "max_watch_resolution_ambiguity",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_ratio_decimal(field_name, getattr(self, field_name)),
            )
        if self.min_watch_base_evidence_strength > self.min_pass_base_evidence_strength:
            raise ValueError(
                "min_watch_base_evidence_strength must not exceed "
                "min_pass_base_evidence_strength",
            )
        if self.min_watch_liquidity_reliability > self.min_pass_liquidity_reliability:
            raise ValueError(
                "min_watch_liquidity_reliability must not exceed "
                "min_pass_liquidity_reliability",
            )
        if self.min_watch_specialist_confidence > self.min_pass_specialist_confidence:
            raise ValueError(
                "min_watch_specialist_confidence must not exceed "
                "min_pass_specialist_confidence",
            )
        if self.max_pass_cost_drag > self.max_watch_cost_drag:
            raise ValueError("max_pass_cost_drag must not exceed max_watch_cost_drag")
        if self.max_pass_resolution_ambiguity > self.max_watch_resolution_ambiguity:
            raise ValueError(
                "max_pass_resolution_ambiguity must not exceed "
                "max_watch_resolution_ambiguity",
            )
        _require_hard_flags("config", self)


@dataclass(frozen=True)
class ResearchStrategyResolutionProbabilityScenarioLadderInput:
    event_key: str
    downside_evidence_strength: Decimal
    base_evidence_strength: Decimal
    upside_evidence_strength: Decimal
    cost_drag: Decimal
    liquidity_reliability: Decimal
    resolution_ambiguity: Decimal
    specialist_confidence: Decimal
    reason_codes: tuple[str, ...] = ()
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self) -> None:
        _require_exact_type(
            self,
            ResearchStrategyResolutionProbabilityScenarioLadderInput,
            "input",
        )
        _require_public_label("event_key", self.event_key)
        for field_name in (
            "downside_evidence_strength",
            "base_evidence_strength",
            "upside_evidence_strength",
            "cost_drag",
            "liquidity_reliability",
            "resolution_ambiguity",
            "specialist_confidence",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_ratio_decimal(field_name, getattr(self, field_name)),
            )
        if not (
            self.downside_evidence_strength
            <= self.base_evidence_strength
            <= self.upside_evidence_strength
        ):
            raise ValueError("scenario ladder evidence must be downside <= base <= upside")
        object.__setattr__(
            self,
            "reason_codes",
            _normalize_reason_codes("reason_codes", self.reason_codes, allow_empty=True),
        )
        _require_hard_flags("input", self)


@dataclass(frozen=True)
class ResearchStrategyResolutionProbabilityScenarioLadderRow:
    event_key: str
    downside_evidence_strength: Decimal
    base_evidence_strength: Decimal
    upside_evidence_strength: Decimal
    cost_drag: Decimal
    liquidity_reliability: Decimal
    resolution_ambiguity: Decimal
    specialist_confidence: Decimal
    reliability_discount: Decimal
    downside_probability: Decimal
    base_probability: Decimal
    upside_probability: Decimal
    probability_span: Decimal
    readiness_score: Decimal
    status: str
    reason_codes: tuple[str, ...]
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self) -> None:
        _require_exact_type(
            self,
            ResearchStrategyResolutionProbabilityScenarioLadderRow,
            "row",
        )
        _require_public_label("event_key", self.event_key)
        for field_name in (
            "downside_evidence_strength",
            "base_evidence_strength",
            "upside_evidence_strength",
            "cost_drag",
            "liquidity_reliability",
            "resolution_ambiguity",
            "specialist_confidence",
            "reliability_discount",
            "downside_probability",
            "base_probability",
            "upside_probability",
            "probability_span",
            "readiness_score",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_ratio_decimal(field_name, getattr(self, field_name)),
            )
        if not (
            self.downside_evidence_strength
            <= self.base_evidence_strength
            <= self.upside_evidence_strength
        ):
            raise ValueError("scenario ladder evidence must be downside <= base <= upside")
        _require_status("status", self.status)
        object.__setattr__(
            self,
            "reason_codes",
            _normalize_reason_codes("reason_codes", self.reason_codes, allow_empty=False),
        )
        _require_hard_flags("row", self)
        _validate_row_consistency(self)


@dataclass(frozen=True)
class ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount:
    reason_code: str
    count: Decimal
    row_ratio: Decimal
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self) -> None:
        _require_exact_type(
            self,
            ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount,
            "reason_code_count",
        )
        _require_reason_code("reason_code", self.reason_code)
        object.__setattr__(
            self,
            "count",
            _require_positive_whole_decimal("count", self.count),
        )
        object.__setattr__(
            self,
            "row_ratio",
            _require_ratio_decimal("row_ratio", self.row_ratio),
        )
        _require_hard_flags("reason_code_count", self)


@dataclass(frozen=True)
class ResearchStrategyResolutionProbabilityScenarioLadderReport:
    generated_at: datetime
    config_version: str
    input_count: Decimal
    pass_count: Decimal
    watch_count: Decimal
    block_count: Decimal
    average_readiness_score: Decimal | None
    min_base_evidence_strength: Decimal
    max_cost_drag: Decimal
    min_liquidity_reliability: Decimal
    max_resolution_ambiguity: Decimal
    min_specialist_confidence: Decimal
    status: str
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...]
    reason_code_counts: tuple[
        ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount,
        ...,
    ]
    reason_codes: tuple[str, ...]
    derived_validation_digest: str = ""
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self) -> None:
        _require_exact_type(
            self,
            ResearchStrategyResolutionProbabilityScenarioLadderReport,
            "report",
        )
        object.__setattr__(self, "generated_at", _as_utc("generated_at", self.generated_at))
        _require_public_label("config_version", self.config_version)
        for field_name in ("input_count", "pass_count", "watch_count", "block_count"):
            object.__setattr__(
                self,
                field_name,
                _require_nonnegative_whole_decimal(field_name, getattr(self, field_name)),
            )
        object.__setattr__(
            self,
            "average_readiness_score",
            _require_optional_ratio_decimal(
                "average_readiness_score",
                self.average_readiness_score,
            ),
        )
        for field_name in (
            "min_base_evidence_strength",
            "max_cost_drag",
            "min_liquidity_reliability",
            "max_resolution_ambiguity",
            "min_specialist_confidence",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_ratio_decimal(field_name, getattr(self, field_name)),
            )
        _require_status("status", self.status)
        object.__setattr__(self, "rows", _normalize_rows(self.rows))
        object.__setattr__(
            self,
            "reason_code_counts",
            _normalize_reason_code_counts(self.reason_code_counts),
        )
        object.__setattr__(
            self,
            "reason_codes",
            _normalize_reason_codes("reason_codes", self.reason_codes, allow_empty=False),
        )
        _require_hard_flags("report", self)
        _validate_report_consistency(self)
        expected_digest = _report_unsigned_digest(self)
        if self.derived_validation_digest == "":
            object.__setattr__(self, "derived_validation_digest", expected_digest)
        elif self.derived_validation_digest != expected_digest:
            raise ValueError("derived_validation_digest must match report payload")
        _require_digest("derived_validation_digest", self.derived_validation_digest)


def build_research_strategy_resolution_probability_scenario_ladder_report(
    inputs: Iterable[object],
    *,
    config: ResearchStrategyResolutionProbabilityScenarioLadderConfig,
    generated_at: datetime,
) -> ResearchStrategyResolutionProbabilityScenarioLadderReport:
    if type(config) is not ResearchStrategyResolutionProbabilityScenarioLadderConfig:
        raise ValueError(
            "config must be a ResearchStrategyResolutionProbabilityScenarioLadderConfig",
        )
    _require_hard_flags("config", config)
    generated_at_utc = _as_utc("generated_at", generated_at)
    input_items = _normalize_inputs(inputs)
    event_keys = [item.event_key for item in input_items]
    if len(event_keys) != len(set(event_keys)):
        raise ValueError("event_key values must be unique")
    rows = tuple(
        _row_from_input(item, config=config)
        for item in sorted(input_items, key=lambda item: item.event_key)
    )
    reason_codes = _summary_reason_codes(rows)
    return ResearchStrategyResolutionProbabilityScenarioLadderReport(
        generated_at=generated_at_utc,
        config_version=config.config_version,
        input_count=_decimal_count(len(rows)),
        pass_count=_decimal_count(_status_count(rows, "pass")),
        watch_count=_decimal_count(_status_count(rows, "watch")),
        block_count=_decimal_count(_status_count(rows, "block")),
        average_readiness_score=_average_row_value(rows, "readiness_score"),
        min_base_evidence_strength=_minimum_row_value(rows, "base_evidence_strength"),
        max_cost_drag=_maximum_row_value(rows, "cost_drag"),
        min_liquidity_reliability=_minimum_row_value(rows, "liquidity_reliability"),
        max_resolution_ambiguity=_maximum_row_value(rows, "resolution_ambiguity"),
        min_specialist_confidence=_minimum_row_value(rows, "specialist_confidence"),
        status=_summary_status(reason_codes),
        rows=rows,
        reason_code_counts=_reason_code_counts(rows, reason_codes),
        reason_codes=reason_codes,
    )


def research_strategy_resolution_probability_scenario_ladder_report_payload(
    report: ResearchStrategyResolutionProbabilityScenarioLadderReport,
) -> dict[str, Any]:
    if type(report) is not ResearchStrategyResolutionProbabilityScenarioLadderReport:
        raise ValueError(
            "report must be a ResearchStrategyResolutionProbabilityScenarioLadderReport",
        )
    _require_hard_flags("report", report)
    payload = _payload_value(report)
    if type(payload) is not dict:
        raise ValueError("report payload must be a JSON object")
    validate_research_strategy_resolution_probability_scenario_ladder_report_payload(
        payload,
    )
    return payload


def research_strategy_resolution_probability_scenario_ladder_report_digest(
    report: ResearchStrategyResolutionProbabilityScenarioLadderReport,
) -> str:
    payload = research_strategy_resolution_probability_scenario_ladder_report_payload(
        report,
    )
    digest = payload["derived_validation_digest"]
    if type(digest) is not str:
        raise ValueError("derived_validation_digest must be a string")
    return digest


def validate_research_strategy_resolution_probability_scenario_ladder_report_payload(
    payload: dict[str, Any],
) -> bool:
    if type(payload) is not dict:
        raise ValueError("payload must be a JSON object")
    _reject_unsafe_public_payload(payload)
    _reject_public_payload_values("payload", payload)
    for field_name in ("paper_only", "report_only", "readonly"):
        if payload.get(field_name) is not True:
            raise ValueError(f"{field_name} must be True")
    digest = payload.get("derived_validation_digest")
    _require_digest("derived_validation_digest", digest)
    unsigned = dict(payload)
    unsigned.pop("derived_validation_digest")
    encoded = json.dumps(
        unsigned,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError("derived_validation_digest must match payload")
    return True


def _row_from_input(
    item: ResearchStrategyResolutionProbabilityScenarioLadderInput,
    *,
    config: ResearchStrategyResolutionProbabilityScenarioLadderConfig,
) -> ResearchStrategyResolutionProbabilityScenarioLadderRow:
    reliability_discount = _reliability_discount(item)
    discount_multiplier = _quantize(ONE - reliability_discount)
    downside_probability = _quantize(item.downside_evidence_strength * discount_multiplier)
    base_probability = _quantize(item.base_evidence_strength * discount_multiplier)
    upside_probability = _quantize(item.upside_evidence_strength * discount_multiplier)
    probability_span = _quantize(upside_probability - downside_probability)
    readiness_score = _readiness_score(item)
    status = _row_status(item, config=config)
    return ResearchStrategyResolutionProbabilityScenarioLadderRow(
        event_key=item.event_key,
        downside_evidence_strength=item.downside_evidence_strength,
        base_evidence_strength=item.base_evidence_strength,
        upside_evidence_strength=item.upside_evidence_strength,
        cost_drag=item.cost_drag,
        liquidity_reliability=item.liquidity_reliability,
        resolution_ambiguity=item.resolution_ambiguity,
        specialist_confidence=item.specialist_confidence,
        reliability_discount=reliability_discount,
        downside_probability=downside_probability,
        base_probability=base_probability,
        upside_probability=upside_probability,
        probability_span=probability_span,
        readiness_score=readiness_score,
        status=status,
        reason_codes=_row_reason_codes(item, status=status, config=config),
    )


def _reliability_discount(
    item: ResearchStrategyResolutionProbabilityScenarioLadderInput
    | ResearchStrategyResolutionProbabilityScenarioLadderRow,
) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        discount = (
            item.cost_drag
            + item.resolution_ambiguity
            + (ONE - item.liquidity_reliability)
            + (ONE - item.specialist_confidence)
        ) / Decimal("4")
    return _quantize(discount)


def _readiness_score(
    item: ResearchStrategyResolutionProbabilityScenarioLadderInput
    | ResearchStrategyResolutionProbabilityScenarioLadderRow,
) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        score = (
            item.base_evidence_strength
            + (ONE - item.cost_drag)
            + item.liquidity_reliability
            + (ONE - item.resolution_ambiguity)
            + item.specialist_confidence
        ) / Decimal("5")
    return _quantize(score)


def _row_status(
    item: ResearchStrategyResolutionProbabilityScenarioLadderInput,
    *,
    config: ResearchStrategyResolutionProbabilityScenarioLadderConfig,
) -> str:
    if (
        item.base_evidence_strength < config.min_watch_base_evidence_strength
        or item.cost_drag > config.max_watch_cost_drag
        or item.liquidity_reliability < config.min_watch_liquidity_reliability
        or item.resolution_ambiguity > config.max_watch_resolution_ambiguity
        or item.specialist_confidence < config.min_watch_specialist_confidence
    ):
        return "block"
    if (
        item.base_evidence_strength < config.min_pass_base_evidence_strength
        or item.cost_drag > config.max_pass_cost_drag
        or item.liquidity_reliability < config.min_pass_liquidity_reliability
        or item.resolution_ambiguity > config.max_pass_resolution_ambiguity
        or item.specialist_confidence < config.min_pass_specialist_confidence
    ):
        return "watch"
    return "pass"


def _row_reason_codes(
    item: ResearchStrategyResolutionProbabilityScenarioLadderInput,
    *,
    status: str,
    config: ResearchStrategyResolutionProbabilityScenarioLadderConfig,
) -> tuple[str, ...]:
    codes = {
        f"resolution_probability_scenario_ladder_{status}",
        f"base_evidence_strength_{_floor_status(
            item.base_evidence_strength,
            config.min_pass_base_evidence_strength,
            config.min_watch_base_evidence_strength,
        )}",
        f"cost_drag_{_ceiling_status(
            item.cost_drag,
            config.max_pass_cost_drag,
            config.max_watch_cost_drag,
        )}",
        f"liquidity_reliability_{_floor_status(
            item.liquidity_reliability,
            config.min_pass_liquidity_reliability,
            config.min_watch_liquidity_reliability,
        )}",
        f"resolution_ambiguity_{_ceiling_status(
            item.resolution_ambiguity,
            config.max_pass_resolution_ambiguity,
            config.max_watch_resolution_ambiguity,
        )}",
        f"specialist_confidence_{_floor_status(
            item.specialist_confidence,
            config.min_pass_specialist_confidence,
            config.min_watch_specialist_confidence,
        )}",
    }
    for code in item.reason_codes:
        codes.add(f"input_{code}")
    return tuple(sorted(codes))


def _floor_status(value: Decimal, pass_floor: Decimal, watch_floor: Decimal) -> str:
    if value < watch_floor:
        return "block"
    if value < pass_floor:
        return "watch"
    return "pass"


def _ceiling_status(value: Decimal, pass_ceiling: Decimal, watch_ceiling: Decimal) -> str:
    if value > watch_ceiling:
        return "block"
    if value > pass_ceiling:
        return "watch"
    return "pass"


def _normalize_inputs(
    inputs: Iterable[object],
) -> tuple[ResearchStrategyResolutionProbabilityScenarioLadderInput, ...]:
    if isinstance(inputs, (str, bytes)):
        raise ValueError("inputs must be an iterable")
    try:
        values = tuple(inputs)
    except TypeError as exc:
        raise ValueError("inputs must be an iterable") from exc
    normalized: list[ResearchStrategyResolutionProbabilityScenarioLadderInput] = []
    for value in values:
        if type(value) is not ResearchStrategyResolutionProbabilityScenarioLadderInput:
            raise ValueError(
                "inputs must contain ResearchStrategyResolutionProbabilityScenarioLadderInput",
            )
        _require_hard_flags("input", value)
        normalized.append(value)
    return tuple(normalized)


def _summary_reason_codes(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
) -> tuple[str, ...]:
    if not rows:
        return ("no_resolution_probability_events",)
    if all(row.status == "pass" for row in rows):
        return ("resolution_probability_scenario_ladder_pass",)
    codes: list[str] = []
    if any(row.status == "block" for row in rows):
        codes.append("resolution_probability_scenario_ladder_block")
    elif any(row.status == "watch" for row in rows):
        codes.append("resolution_probability_scenario_ladder_watch")
    row_codes = {code for row in rows for code in row.reason_codes}
    for code in COMPONENT_REASON_PRIORITY:
        if code in row_codes:
            codes.append(code)
    return tuple(codes)


def _summary_status(reason_codes: tuple[str, ...]) -> str:
    if reason_codes == ("no_resolution_probability_events",):
        return "block"
    if "resolution_probability_scenario_ladder_block" in reason_codes:
        return "block"
    if "resolution_probability_scenario_ladder_watch" in reason_codes:
        return "watch"
    return "pass"


def _reason_code_counts(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
    reason_codes: tuple[str, ...],
) -> tuple[ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount, ...]:
    if not rows:
        return (
            ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount(
                reason_code=reason_codes[0],
                count=ONE,
                row_ratio=ZERO,
            ),
        )
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(row.reason_codes)
    row_count = Decimal(len(rows))
    return tuple(
        ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount(
            reason_code=reason_code,
            count=_decimal_count(count),
            row_ratio=_quantize(Decimal(count) / row_count),
        )
        for reason_code, count in sorted(counter.items(), key=lambda item: item[0])
    )


def _average_row_value(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
    field_name: str,
) -> Decimal | None:
    if not rows:
        return None
    return _quantize(sum((getattr(row, field_name) for row in rows), ZERO) / Decimal(len(rows)))


def _minimum_row_value(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
    field_name: str,
) -> Decimal:
    if not rows:
        return ZERO
    return min(getattr(row, field_name) for row in rows)


def _maximum_row_value(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
    field_name: str,
) -> Decimal:
    if not rows:
        return ZERO
    return max(getattr(row, field_name) for row in rows)


def _status_count(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
    status: str,
) -> int:
    return sum(1 for row in rows if row.status == status)


def _normalize_rows(
    rows: tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...],
) -> tuple[ResearchStrategyResolutionProbabilityScenarioLadderRow, ...]:
    if type(rows) is not tuple:
        raise ValueError("rows must be a tuple")
    for row in rows:
        if type(row) is not ResearchStrategyResolutionProbabilityScenarioLadderRow:
            raise ValueError(
                "rows must contain ResearchStrategyResolutionProbabilityScenarioLadderRow",
            )
        _require_hard_flags("row", row)
    sorted_rows = tuple(sorted(rows, key=lambda row: row.event_key))
    if rows != sorted_rows:
        raise ValueError("rows must be sorted by event_key")
    return rows


def _normalize_reason_code_counts(
    counts: tuple[
        ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount,
        ...,
    ],
) -> tuple[ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount, ...]:
    if type(counts) is not tuple:
        raise ValueError("reason_code_counts must be a tuple")
    for count in counts:
        if type(count) is not ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount:
            raise ValueError(
                "reason_code_counts must contain "
                "ResearchStrategyResolutionProbabilityScenarioLadderReasonCodeCount",
            )
        _require_hard_flags("reason_code_count", count)
    sorted_counts = tuple(sorted(counts, key=lambda count: count.reason_code))
    if counts != sorted_counts:
        raise ValueError("reason_code_counts must be sorted by reason_code")
    return counts


def _validate_row_consistency(
    row: ResearchStrategyResolutionProbabilityScenarioLadderRow,
) -> None:
    if row.reliability_discount != _reliability_discount(row):
        raise ValueError("reliability_discount must match components")
    discount_multiplier = _quantize(ONE - row.reliability_discount)
    if row.downside_probability != _quantize(
        row.downside_evidence_strength * discount_multiplier,
    ):
        raise ValueError("downside_probability must match components")
    if row.base_probability != _quantize(row.base_evidence_strength * discount_multiplier):
        raise ValueError("base_probability must match components")
    if row.upside_probability != _quantize(row.upside_evidence_strength * discount_multiplier):
        raise ValueError("upside_probability must match components")
    if row.probability_span != _quantize(row.upside_probability - row.downside_probability):
        raise ValueError("probability_span must match components")
    if row.readiness_score != _readiness_score(row):
        raise ValueError("readiness_score must match components")
    expected_status_code = f"resolution_probability_scenario_ladder_{row.status}"
    if expected_status_code not in row.reason_codes:
        raise ValueError("status must match reason_codes")
    if row.status == "pass" and any(
        code.endswith(("_watch", "_block")) for code in row.reason_codes
    ):
        raise ValueError("status must match component reason_codes")
    if row.status == "watch" and any(
        code.endswith("_block") for code in row.reason_codes
    ):
        raise ValueError("status must match component reason_codes")


def _validate_report_consistency(
    report: ResearchStrategyResolutionProbabilityScenarioLadderReport,
) -> None:
    if report.input_count != _decimal_count(len(report.rows)):
        raise ValueError("input_count must match rows")
    if report.pass_count != _decimal_count(_status_count(report.rows, "pass")):
        raise ValueError("pass_count must match rows")
    if report.watch_count != _decimal_count(_status_count(report.rows, "watch")):
        raise ValueError("watch_count must match rows")
    if report.block_count != _decimal_count(_status_count(report.rows, "block")):
        raise ValueError("block_count must match rows")
    if report.average_readiness_score != _average_row_value(
        report.rows,
        "readiness_score",
    ):
        raise ValueError("average_readiness_score must match rows")
    if report.min_base_evidence_strength != _minimum_row_value(
        report.rows,
        "base_evidence_strength",
    ):
        raise ValueError("min_base_evidence_strength must match rows")
    if report.max_cost_drag != _maximum_row_value(report.rows, "cost_drag"):
        raise ValueError("max_cost_drag must match rows")
    if report.min_liquidity_reliability != _minimum_row_value(
        report.rows,
        "liquidity_reliability",
    ):
        raise ValueError("min_liquidity_reliability must match rows")
    if report.max_resolution_ambiguity != _maximum_row_value(
        report.rows,
        "resolution_ambiguity",
    ):
        raise ValueError("max_resolution_ambiguity must match rows")
    if report.min_specialist_confidence != _minimum_row_value(
        report.rows,
        "specialist_confidence",
    ):
        raise ValueError("min_specialist_confidence must match rows")
    if report.reason_codes != _summary_reason_codes(report.rows):
        raise ValueError("reason_codes must match rows")
    if report.status != _summary_status(report.reason_codes):
        raise ValueError("status must match reason_codes")
    if report.reason_code_counts != _reason_code_counts(report.rows, report.reason_codes):
        raise ValueError("reason_code_counts must match rows")


def _payload_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _payload_value(asdict(value))
    if isinstance(value, tuple):
        return [_payload_value(item) for item in value]
    if isinstance(value, list):
        return [_payload_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _payload_value(item) for key, item in value.items()}
    return value


def _report_unsigned_digest(
    report: ResearchStrategyResolutionProbabilityScenarioLadderReport,
) -> str:
    payload = _payload_value(report)
    if type(payload) is not dict:
        raise ValueError("report payload must be a JSON object")
    payload.pop("derived_validation_digest", None)
    _reject_unsafe_public_payload(payload)
    _reject_public_payload_values("payload", payload)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_utc(field_name: str, value: datetime) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_exact_type(value: object, expected_type: type[object], label: str) -> None:
    if type(value) is not expected_type:
        raise ValueError(f"{label} must be exactly {expected_type.__name__}")


def _require_decimal(field_name: str, value: object) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    try:
        return +value
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be finite") from exc


def _require_ratio_decimal(field_name: str, value: object) -> Decimal:
    normalized = _require_decimal(field_name, value)
    if normalized < ZERO or normalized > ONE:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return _quantize(normalized)


def _require_optional_ratio_decimal(
    field_name: str,
    value: object,
) -> Decimal | None:
    if value is None:
        return None
    return _require_ratio_decimal(field_name, value)


def _require_positive_whole_decimal(field_name: str, value: object) -> Decimal:
    normalized = _require_decimal(field_name, value)
    if normalized <= ZERO:
        raise ValueError(f"{field_name} must be positive")
    if normalized != normalized.to_integral_value():
        raise ValueError(f"{field_name} must be a whole count")
    return _quantize(normalized)


def _require_nonnegative_whole_decimal(field_name: str, value: object) -> Decimal:
    normalized = _require_decimal(field_name, value)
    if normalized < ZERO:
        raise ValueError(f"{field_name} must be nonnegative")
    if normalized != normalized.to_integral_value():
        raise ValueError(f"{field_name} must be a whole count")
    return _quantize(normalized)


def _decimal_count(value: int) -> Decimal:
    if type(value) is not int:
        raise ValueError("count input must be an int")
    if value < 0:
        raise ValueError("count input must be nonnegative")
    return _quantize(Decimal(value))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(RATIO_QUANTUM)


def _require_public_label(field_name: str, value: object) -> None:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a nonempty public label")
    if any(fragment in value.lower() for fragment in SURFACE_FRAGMENTS):
        raise ValueError(f"{field_name} has unsafe value")


def _require_reason_code(field_name: str, value: object) -> None:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{field_name} must contain canonical reason codes")
    if value.lower() != value:
        raise ValueError(f"{field_name} must contain lowercase reason codes")
    if " " in value:
        raise ValueError(f"{field_name} must contain compact reason codes")
    if any(fragment in value for fragment in SURFACE_FRAGMENTS):
        raise ValueError(f"{field_name} has unsafe value")


def _normalize_reason_codes(
    field_name: str,
    values: object,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    normalized: list[str] = []
    for value in values:
        _require_reason_code(field_name, value)
        if value not in normalized:
            normalized.append(value)
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must be nonempty")
    return tuple(normalized)


def _require_status(field_name: str, value: object) -> None:
    if type(value) is not str or value not in RESOLUTION_PROBABILITY_SCENARIO_LADDER_STATUSES:
        raise ValueError(
            f"{field_name} must be one of "
            f"{RESOLUTION_PROBABILITY_SCENARIO_LADDER_STATUSES}",
        )


def _require_hard_flags(label: str, value: object) -> None:
    for field_name in ("paper_only", "report_only", "readonly"):
        if getattr(value, field_name, None) is not True:
            raise ValueError(f"{field_name} must be True for {label}")


def _require_digest(field_name: str, value: object) -> None:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    if len(value) != 64 or any(character not in HEX_DIGITS for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _reject_unsafe_public_payload(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("payload keys must be strings")
            if any(fragment in key.lower() for fragment in SURFACE_FRAGMENTS):
                raise ValueError("unsafe field in public payload")
            _reject_unsafe_public_payload(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_unsafe_public_payload(item)
        return
    if type(value) is str and any(fragment in value.lower() for fragment in SURFACE_FRAGMENTS):
        raise ValueError("unsafe value in public payload")


def _reject_public_payload_values(label: str, value: object, path: str = "") -> None:
    if type(value) is bool or value is None:
        return
    if type(value) is int:
        raise ValueError(f"{path or label} must use Decimal-derived string values")
    if type(value) is float:
        raise ValueError(f"{path or label} must use Decimal-derived string values")
    if isinstance(value, Decimal):
        raise ValueError(f"{path or label} must use Decimal-derived string values")
    if type(value) is str:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = key if not path else f"{path}.{key}"
            _reject_public_payload_values(label, item, item_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]" if path else f"{label}[{index}]"
            _reject_public_payload_values(label, item, item_path)
        return
    raise ValueError(f"{path or label} must be JSON serializable")
