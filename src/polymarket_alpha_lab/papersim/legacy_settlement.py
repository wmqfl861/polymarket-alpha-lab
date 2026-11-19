"""Attribution of market-keyed settlements in the legacy paper ledger."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from . import ledger, store

_CENT = Decimal("0.01")
_HALF_CENT = _CENT / 2
_DECISION_REFERENCE = re.compile(r"#([0-9]+)\b")
_POSITION_NOUN = r"(?:decisions?|entry|entries|positions?)"
_DECISION_LIST = (
    r"#[0-9]+\b(?:\s*(?:,\s*(?:and\s+)?|[+&/]|and\b)\s*"
    rf"(?:{_POSITION_NOUN}\s+)?#[0-9]+\b)*"
)
_EXCLUSIVE = r"(?:(?:only|solely|exclusively)\s+)?"
_BE = r"(?:is|are|was|were|(?:has|have|had)\s+been)"
_BE_WITH_NEGATION = (
    r"(?:is|are|was|were|has|have|had)(?P<negated>n['’]t|\s+(?:not|never))?"
    r"\s+(?:been\s+)?"
)
_CLOSED = r"(?:settled|voided|refunded|exited)"
_DECISION_TARGET = (
    rf"{_EXCLUSIVE}(?:the\s+)?(?:{_POSITION_NOUN}\s*:?\s*)?"
    rf"(?P<decisions>{_DECISION_LIST})"
)
# In free-form notes, IDs can be the target of a settlement claim or the
# subject of a closure statement. Capture those IDs separately from references
# to other decisions in the surrounding prose.
_SETTLEMENT_ATTRIBUTION_PATTERNS = (
    re.compile(
        rf"\b(?:(?P<negated>(?:not|never|\w+n['’]t)\s+))?"
        rf"(?:settles?|{_CLOSED}|covers?|"
        rf"(?:apply|applies|applied|attributed|belongs)\s+{_EXCLUSIVE}to|"
        rf"only\s+{_POSITION_NOUN}\s+(?:that\s+{_BE}\s+)?{_CLOSED}\s+{_BE})\s+"
        rf"{_DECISION_TARGET}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:settlement|resolution|payout)\s+(?:{_BE_WITH_NEGATION})?"
        rf"{_EXCLUSIVE}(?:for|of)\s+{_DECISION_TARGET}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_EXCLUSIVE}{_POSITION_NOUN}\s+(?P<decisions>{_DECISION_LIST})"
        rf"(?:\s+(?:only|alone))?\s+{_BE_WITH_NEGATION}{_EXCLUSIVE}{_CLOSED}\b",
        re.IGNORECASE,
    ),
)
_REUSED_RESOLUTION = re.compile(
    r"\b(?:re[- ]?entr(?:y|ies)|re[- ]?enter(?:ed)?|overwrit(?:e|ten|ing)|overwrote)\b",
    re.IGNORECASE,
)
_DISTINCT_CLOSE = re.compile(r"\b(?:voided|exited|refunded)\b", re.IGNORECASE)


class LegacyResolutionError(ValueError):
    """A stable diagnostic carrying only the affected record identifiers."""

    def __init__(
        self, code: str, market_id: str, decisions: Sequence[Mapping[str, Any]]
    ) -> None:
        self.code = code
        ids = sorted(
            (str(row.get("id")) for row in decisions),
            key=lambda value: (len(value), value),
        )
        super().__init__(f"{code}: market_id={market_id!r} decision_ids={ids!r}")


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if parsed.utcoffset() is None:
        raise ValueError("legacy timestamp lacks a timezone")
    return parsed


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def attribute_resolution(
    market_id: str,
    decisions: Sequence[Mapping[str, Any]],
    resolution: Mapping[str, Any],
) -> dict[str, Decimal] | None:
    """Validate the row's scope and allocate a verified pooled P&L.

    A single decision keeps its recorded result when explicit attribution
    agrees, including old exports that omitted timestamps. A shared
    resolution needs chronological, directional and financial evidence
    covering all entries.
    """
    try:
        resolved_at = _timestamp(resolution.get("ts"))
        entered_at = [_timestamp(row.get("ts")) for row in decisions]
    except ValueError:
        raise LegacyResolutionError(
            "papersim_legacy_resolution_timestamp_invalid", market_id, decisions
        ) from None
    if resolved_at is not None and any(
        moment is not None and resolved_at < moment for moment in entered_at
    ):
        raise LegacyResolutionError(
            "papersim_legacy_resolution_precedes_entry", market_id, decisions
        )
    evidence = str(resolution.get("evidence") or "")
    decision_ids = {str(row.get("id")) for row in decisions}
    claims = (
        claim
        for pattern in _SETTLEMENT_ATTRIBUTION_PATTERNS
        for claim in pattern.finditer(evidence)
    )
    negated_spans = []
    for claim in claims:
        claimed_ids = set(_DECISION_REFERENCE.findall(claim["decisions"]))
        if claim["negated"]:
            conflicts = not claimed_ids.isdisjoint(decision_ids)
            negated_spans.append(claim.span())
        else:
            conflicts = claimed_ids != decision_ids
        if conflicts:
            raise LegacyResolutionError(
                "papersim_legacy_resolution_evidence_conflict", market_id, decisions
            )
    if len(decisions) < 2:
        return None
    if resolved_at is None or None in entered_at:
        raise LegacyResolutionError(
            "papersim_legacy_resolution_timestamp_missing", market_id, decisions
        )

    rows = {str(row.get("id")): row for row in decisions}
    if len(rows) != len(decisions) or not all(
        key.isascii() and key.isdecimal() for key in rows
    ):
        raise LegacyResolutionError(
            "papersim_legacy_resolution_decision_ids_invalid", market_id, decisions
        )

    outcome = str(resolution.get("outcome") or "").strip()
    # Exclusions of unrelated entries carry no positive scope information for
    # the pooled row's free-form references or closure description.
    for start, end in negated_spans:
        evidence = evidence[:start] + " " * (end - start) + evidence[end:]
    references = set(_DECISION_REFERENCE.findall(evidence))
    if (
        (references and not rows.keys() <= references)
        or _REUSED_RESOLUTION.search(evidence)
        or (outcome != ledger.VOIDED_OUTCOME and _DISTINCT_CLOSE.search(evidence))
    ):
        raise LegacyResolutionError(
            "papersim_legacy_resolution_evidence_conflict", market_id, decisions
        )
    if outcome == ledger.EXITED_OUTCOME:
        raise LegacyResolutionError(
            "papersim_legacy_resolution_exit_attribution_unverified", market_id, decisions
        )

    sides = {str(row.get("side") or "").strip() for row in decisions}
    if outcome != ledger.VOIDED_OUTCOME and (
        len(sides) != 1 or not sides <= set(store.SIDE_TOKENS)
    ):
        raise LegacyResolutionError(
            "papersim_legacy_resolution_direction_conflict", market_id, decisions
        )

    ordered_ids = sorted(rows, key=int)
    stakes = [_decimal(rows[key].get("stake")) for key in ordered_ids]
    shares = [_decimal(rows[key].get("shares")) for key in ordered_ids]
    prices = [_decimal(rows[key].get("price")) for key in ordered_ids]
    pnl = _decimal(resolution.get("pnl"))
    payoff = _decimal(resolution.get("payoff"))
    amounts_issue = LegacyResolutionError(
        "papersim_legacy_resolution_amounts_unverified", market_id, decisions
    )
    if (
        pnl is None or payoff is None
        or any(amount is None or amount <= 0 for amount in stakes + shares)
        or any(price is None or not 0 < price < 1 for price in prices)
    ):
        raise amounts_issue
    # A recorded share quantity can round to the nearest whole share or
    # nearest fractional share. Trailing zeroes do not change that bound.
    share_tolerances = [
        Decimal("0.5").scaleb(min(shares_i.normalize().as_tuple().exponent, 0))
        for shares_i in shares
    ]
    if any(
        abs(shares_i - stake / price) > tolerance
        for shares_i, stake, price, tolerance in zip(
            shares, stakes, prices, share_tolerances, strict=True
        )
    ):
        raise amounts_issue
    total_stake = sum(stakes)
    if outcome == ledger.VOIDED_OUTCOME:
        if pnl != 0 or payoff != total_stake:
            raise amounts_issue
        return dict.fromkeys(ordered_ids, Decimal("0.00"))

    if outcome in ledger.NATURAL_OUTCOMES:
        hit = store.natural_alignment(next(iter(sides)), outcome)
    elif outcome in ledger.SETTLED_LABEL_OUTCOMES:
        hit = outcome == ledger.WIN_OUTCOME
    else:
        raise LegacyResolutionError(
            "papersim_legacy_resolution_outcome_unmapped", market_id, decisions
        )
    if not hit:
        if payoff != 0 or pnl != -total_stake:
            raise amounts_issue
        return {key: -stake for key, stake in zip(ordered_ids, stakes, strict=True)}

    # Legacy payoff is either the dollar proceeds or the unit payout (1).
    total_shares = sum(shares)
    proceeds = total_stake + pnl
    if (
        pnl <= 0
        or abs(proceeds - total_shares) > sum(share_tolerances) + _HALF_CENT
        or payoff not in (Decimal(1), proceeds)
    ):
        raise amounts_issue

    payouts = {
        key: (proceeds * amount / total_shares).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        for key, amount in zip(ordered_ids[:-1], shares[:-1], strict=True)
    }
    payouts[ordered_ids[-1]] = proceeds - sum(payouts.values())
    allocations = {
        key: payouts[key] - stake for key, stake in zip(ordered_ids, stakes, strict=True)
    }
    if any(amount < 0 for amount in allocations.values()):
        raise amounts_issue
    return allocations
