"""Compare a frozen pre-match soccer totals quote with executable CLOB asks.

Pass one JSON object to ``python -m polymarket_alpha_lab.papersim.adapters.soccer_totals``
on stdin or as a file argument. This input uses normalized evidence and raw CLOB
ask books. Source references identify the underlying snapshots or rule text.

* decision_at is the UTC as-of time. notional_usd defaults to 20 before fees
  and must be at least one cent. Use ISO timestamps with Z or +00:00.
* fixture_binding supplies kickoff_at, source_reference, and polymarket and
  external objects. Each object supplies source_id, fixture_id, home_team_id
  and away_team_id as source-native identifiers. Use source_id "polymarket"
  for the Polymarket fixture. Both source sections also supply their own
  fixture with these four identifiers and kickoff_at.
* polymarket.market supplies source_reference, fetched_at, fixture_id,
  kickoff_at, market_id, condition_id, kind, period, line, outcomes and
  token_ids. Outcomes and token IDs preserve the corresponding Gamma list
  order. The Gamma capture must show active=true, closed=false, and
  accepting_orders=true (Gamma acceptingOrders). fetched_at is the Gamma
  capture time. Use null for unavailable status fields or capture time.
  Optional fees_enabled and fee_schedule carry Gamma's fee metadata.
  polymarket.books supplies raw CLOB market (condition ID), asset_id,
  timestamp (upstream epoch milliseconds), min_order_size and asks, plus
  source_reference and fetched_at for each book.
* external.quotes supplies two rows with side, source_id, fixture_id, provider_id,
  market_id, kind, period, line, american_odds, source_reference, fetched_at,
  and observed_at (the provider's quote timestamp, or null). Both sides must
  come from one provider, market and capture. Use kind "total_goals", period
  "regulation_90_plus_stoppage", and side "Over" or "Under" on either venue.
* Each venue's rules object supplies source_reference, fetched_at, fixture_id,
  market_id, condition_id (Polymarket) or provider_id (external), and period,
  canceled, postponed, start_deadline_at, abandoned_determined,
  abandoned_resolution_order, abandonment_deadline_at, abandoned_no_result,
  missing_result, result_deadline_seconds and result_sources. Order the
  interim abandonment outcomes and result source IDs by settlement priority.
  The abandonment outcomes are official_ruling, declared_score and completed_game.
  Give payouts as split_50_50, void, or settle_determined (the last applies
  to abandoned_determined). Deadlines are UTC timestamps or "no_deadline".
  result_deadline_seconds measures from the fixture's conclusion. Capture
  each documented rule or use null for unavailable evidence. Include any
  source eligibility time limits in the ordered result source identifiers.

Gamma status, quotes and books must be no more than five minutes old at
decision_at. A rejected report may retain indicative differences when
identity and prices are known. Differences use a single estimated taker fee
at the rounded walk_book VWAP, consistent with the paper gate's pricing
convention.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from polymarket_alpha_lab.papersim.adapters.contracts import PAPER_ONLY, READONLY, REPORT_ONLY
from polymarket_alpha_lab.papersim.adapters.polymarket import BookLevel
from polymarket_alpha_lab.papersim.fees import fee_per_share, resolve_taker_fee_rate
from polymarket_alpha_lab.papersim.gates import walk_book
from polymarket_alpha_lab.team_research_agent_types import strict_json

_SIDES = ("Over", "Under")
_PERIOD = "regulation_90_plus_stoppage"
_MAX_SNAPSHOT_AGE = timedelta(minutes=5)
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
                            r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|\+00:00)")


class _Rejected(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Rejected(reason)


def _object(value: object, reason: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _Rejected(reason)
    return value


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isprintable():
        raise _Rejected(reason)
    return value


def _decimal(value: object, reason: str) -> Decimal:
    if type(value) not in (int, float, str, Decimal):
        raise _Rejected(reason)
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) is None:
        raise _Rejected(reason)
    try:
        number = Decimal(repr(value) if type(value) is float else value)
    except InvalidOperation:
        raise _Rejected(reason) from None
    _require(number.is_finite(), reason)
    return number


def _finite_float(value: object, reason: str) -> float:
    result = float(_decimal(value, reason))
    _require(math.isfinite(result), reason)
    return result


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise _Rejected(reason)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise _Rejected(reason) from None


def _book_time(value: object) -> datetime:
    if type(value) not in (int, str) or re.fullmatch(r"[0-9]{1,14}", str(value)) is None:
        raise _Rejected("book_observed_at_invalid")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))


def _fetched_at(row: Mapping, cutoff: datetime, label: str, *, recent: bool) -> datetime:
    fetched_at = _utc(row.get("fetched_at"), f"{label}_fetched_at_invalid")
    _require(fetched_at < cutoff, f"{label}_fetched_at_or_after_decision")
    if recent:
        _require(cutoff - fetched_at <= _MAX_SNAPSHOT_AGE, f"{label}_capture_stale")
    return fetched_at


def _check_observation(at: datetime, fetched_at: datetime, cutoff: datetime, label: str) -> None:
    _require(at <= fetched_at, f"{label}_observation_after_capture")
    _require(cutoff - at <= _MAX_SNAPSHOT_AGE, f"{label}_observation_stale")


def _line(row: Mapping, label: str) -> Decimal:
    _require(row.get("kind") == "total_goals", f"{label}_kind_unsupported")
    _require(row.get("period") == _PERIOD, f"{label}_period_unsupported")
    line = _decimal(row.get("line"), f"{label}_line_invalid")
    # Inspect the fractional digits directly to avoid context-dependent rounding.
    _, digits, exponent = line.as_tuple()
    decimal_point = len(digits) + exponent
    _require(line > 0 and 0 <= decimal_point < len(digits)
             and digits[decimal_point] == 5 and not any(digits[decimal_point + 1:]),
             f"{label}_line_not_half_goal")
    return line


def _fixture_identity(value: object) -> dict[str, str]:
    fixture = _object(value, "fixture_identity_missing")
    identity = {key: _text(fixture.get(key), f"fixture_{key}_invalid") for key in
                ("source_id", "fixture_id", "home_team_id", "away_team_id")}
    _require(identity["home_team_id"] != identity["away_team_id"], "fixture_teams_not_distinct")
    return identity


def _fixture_binding(binding: object, polymarket: Mapping, external: Mapping,
                     cutoff: datetime) -> tuple[dict, datetime]:
    binding = _object(binding, "fixture_binding_missing")
    kickoff = _utc(binding.get("kickoff_at"), "fixture_binding_kickoff_invalid")
    _require(cutoff < kickoff, "decision_at_or_after_kickoff")
    result = {"source_reference": _text(binding.get("source_reference"), "fixture_binding_source_missing"),
              "kickoff_at": kickoff.isoformat()}
    for name, venue in (("polymarket", polymarket), ("external", external)):
        fixture = _object(venue.get("fixture"), f"{name}_fixture_missing")
        identity = _fixture_identity(fixture)
        _require(identity == _fixture_identity(binding.get(name)), f"{name}_fixture_binding_conflict")
        _require(_utc(fixture.get("kickoff_at"), f"{name}_kickoff_invalid") == kickoff,
                 f"{name}_kickoff_conflict")
        result[name] = identity
    _require(result["polymarket"]["source_id"] == "polymarket" and
             result["external"]["source_id"] != "polymarket", "fixture_sources_invalid")
    return result, kickoff


def _token_map(market: Mapping) -> dict[str, str]:
    labels = market.get("outcomes")
    tokens = market.get("token_ids")
    _require(isinstance(labels, list) and len(labels) == 2 and
             all(side in labels for side in _SIDES), "polymarket_outcomes_unsupported")
    _require(isinstance(tokens, list) and len(tokens) == 2 and
             all(isinstance(token, str) and re.fullmatch(r"[0-9]{1,78}", token) for token in tokens)
             and tokens[0] != tokens[1], "polymarket_token_mapping_invalid")
    return dict(zip(labels, tokens, strict=True))


def _market_availability(market: Mapping, fetched: datetime | None, cutoff: datetime,
                         reasons: list[str]) -> dict[str, bool | None]:
    if fetched is None:
        reasons.append("polymarket_market_fetched_at_missing")
    elif cutoff - fetched > _MAX_SNAPSHOT_AGE:
        reasons.append("polymarket_market_capture_stale")

    availability = {}
    for field, expected, unavailable in (
        ("active", True, "inactive"), ("closed", False, "closed"),
        ("accepting_orders", True, "not_accepting_orders"),
    ):
        value = market.get(field)
        availability[field] = value if type(value) is bool else None
        if value is None:
            reasons.append(f"polymarket_market_{field}_missing")
        elif type(value) is not bool:
            reasons.append(f"polymarket_market_{field}_invalid")
        elif value is not expected:
            reasons.append(f"polymarket_market_{unavailable}")
    return availability


def _fee_rate(market: Mapping) -> float:
    enabled = market.get("fees_enabled")
    schedule = market.get("fee_schedule")
    _require(enabled is None or type(enabled) is bool, "polymarket_fee_schedule_invalid")
    market_rate = None
    if schedule is not None:
        schedule = _object(schedule, "polymarket_fee_schedule_invalid")
        exponent = _decimal(schedule.get("exponent"), "polymarket_fee_exponent_unsupported")
        _require(exponent == 1 and schedule.get("takerOnly") is True,
                 "polymarket_fee_schedule_unsupported")
        market_rate = _finite_float(schedule.get("rate"), "polymarket_fee_rate_invalid")
    if enabled is False:
        _require(market_rate in (None, 0), "polymarket_fee_schedule_conflict")
        market_rate = 0.0
    try:
        return resolve_taker_fee_rate("sports", market_rate=market_rate)
    except ValueError:
        raise _Rejected("polymarket_fee_schedule_conflict") from None


def _rule_deadline(value: object, reason: str) -> str:
    return value if value == "no_deadline" else _utc(value, reason).isoformat()


def _rules(value: object, identity: dict, cutoff: datetime, kickoff: datetime, label: str) -> tuple[dict, dict]:
    rules = _object(value, f"{label}_rules_missing")
    _require(all(rules.get(key) == expected for key, expected in identity.items()),
             f"{label}_rules_identity_conflict")
    reason = f"{label}_rules_incomplete_or_unsupported"
    capture = {"source_reference": _text(rules.get("source_reference"), reason),
               "fetched_at": _fetched_at(rules, cutoff, f"{label}_rules", recent=False).isoformat()}
    _require(rules.get("period") == _PERIOD, reason)
    terms = {"period": _PERIOD}
    for key in ("canceled", "postponed", "abandoned_no_result", "missing_result", "abandoned_determined"):
        payout = rules.get(key)
        _require(payout in ("split_50_50", "void") or
                 (key == "abandoned_determined" and payout == "settle_determined"), reason)
        terms[key] = payout
    for key in ("start_deadline_at", "abandonment_deadline_at"):
        terms[key] = _rule_deadline(rules.get(key), reason)
        _require(terms[key] == "no_deadline" or datetime.fromisoformat(terms[key]) >= kickoff, reason)
    seconds = rules.get("result_deadline_seconds")
    _require(seconds == "no_deadline" or (type(seconds) is int and seconds > 0), reason)
    terms["result_deadline_seconds"] = seconds
    order = rules.get("abandoned_resolution_order")
    sources = rules.get("result_sources")
    _require(isinstance(order, list) and all(item in ("official_ruling", "declared_score", "completed_game")
                                             for item in order), reason)
    _require(len(set(order)) == len(order), reason)
    _require(isinstance(sources, list) and bool(sources), reason)
    terms["abandoned_resolution_order"] = order
    terms["result_sources"] = [_text(source, reason) for source in sources]
    return capture, terms


def _external_quotes(external: Mapping, fixture: dict, line: Decimal, cutoff: datetime,
                     report: dict) -> dict:
    quotes = external.get("quotes")
    _require(isinstance(quotes, list) and len(quotes) == 2, "external_quote_pair_required")
    sides = {}
    captures = []
    observations = []
    for value in quotes:
        quote = _object(value, "external_quote_invalid")
        side = quote.get("side")
        _require(side in _SIDES and side not in sides, "external_quote_pair_required")
        _require(quote.get("source_id") == fixture["source_id"] and
                 quote.get("fixture_id") == fixture["fixture_id"], "external_quote_fixture_conflict")
        _require(_line(quote, "external") == line, "external_line_conflict")
        provider_id = _text(quote.get("provider_id"), "external_provider_id_missing")
        market_id = _text(quote.get("market_id"), "external_market_id_missing")
        source = _text(quote.get("source_reference"), "external_source_missing")
        fetched_at = _fetched_at(quote, cutoff, "external", recent=True)
        captures.append((provider_id, market_id, source, fetched_at))
        observed = None
        if quote.get("observed_at") is not None:
            observed = _utc(quote["observed_at"], "external_observed_at_invalid")
            _check_observation(observed, fetched_at, cutoff, "external")
        observations.append(observed)
        odds = _decimal(quote.get("american_odds"), "external_american_odds_invalid")
        _require(odds.copy_abs() >= 100 and odds == odds.to_integral_value()
                 and math.isfinite(float(odds)), "external_american_odds_invalid")
        moneyline = float(odds)
        implied = 100 / (moneyline + 100) if moneyline > 0 else -moneyline / (100 - moneyline)
        sides[side] = {"american_odds": int(odds), "implied_probability": implied,
                       "external_observed_at": observed.isoformat() if observed else None}
    _require(captures[0] == captures[1], "external_quote_capture_conflict")
    if None in observations:
        report["reasons"].append("external_observed_at_missing")
    else:
        _require(observations[0] == observations[1], "external_quote_observation_conflict")
    provider_id, market_id, source, fetched_at = captures[0]
    total = sum(row["implied_probability"] for row in sides.values())
    report["external"] = {"provider_id": provider_id, "market_id": market_id, "source_reference": source,
                          "fetched_at": fetched_at.isoformat(), "overround": total - 1}
    for row in sides.values():
        row["reference_probability"] = row["implied_probability"] / total
    return sides


def _books(values: object, condition: str, tokens: dict[str, str]) -> dict:
    _require(isinstance(values, list), "polymarket_books_missing")
    books = {}
    for value in values:
        book = _object(value, "book_payload_invalid")
        token = book.get("asset_id")
        _require(book.get("market") == condition, "book_condition_conflict")
        _require(isinstance(token, str) and token in tokens.values(), "book_token_conflict")
        _require(token not in books, "book_token_duplicate")
        books[token] = book
    return books


def _buy_price(book: Mapping, side: dict, cutoff: datetime, notional: float, rate: float) -> None:
    fetched = _fetched_at(book, cutoff, "book", recent=True)
    side.update(book_source_reference=_text(book.get("source_reference"), "book_source_missing"),
                book_fetched_at=fetched.isoformat(), book_observed_at=None)
    _require(book.get("timestamp") is not None, "book_observed_at_missing")
    observed = _book_time(book["timestamp"])
    side["book_observed_at"] = observed.isoformat()
    _check_observation(observed, fetched, cutoff, "book")
    minimum = _finite_float(book.get("min_order_size"), "book_min_order_size_invalid")
    _require(minimum > 0, "book_min_order_size_invalid")
    asks = book.get("asks")
    _require(isinstance(asks, list), "book_asks_missing")
    levels = []
    for level in asks:
        level = _object(level, "book_level_invalid")
        price = _finite_float(level.get("price"), "book_level_invalid")
        size = _finite_float(level.get("size"), "book_level_invalid")
        try:
            validated = BookLevel(price, size)
        except ValueError:
            raise _Rejected("book_level_invalid") from None
        levels.append((validated.price, validated.size))
    _require(len({price for price, _ in levels}) == len(levels), "book_price_level_duplicate")

    walk = walk_book(levels, notional=notional)
    _require(all(map(math.isfinite, (walk.depth_near2c, walk.filled_notional, walk.shares))), "book_depth_invalid")
    try:
        capacity = math.fsum(price * size for price, size in levels)
    except OverflowError:
        raise _Rejected("book_depth_invalid") from None
    # The canonical walk tolerates a micro-dollar shortfall. Require full
    # resting depth here, allowing only float conversion noise.
    filled = walk.filled and (capacity >= notional or math.isclose(capacity, notional, rel_tol=1e-15))
    side.update(best_ask=walk.best, filled_notional_usd=walk.filled_notional,
                filled_shares=walk.shares, depth_within_2c_usd=walk.depth_near2c,
                min_order_size=minimum, filled=filled)
    _require(filled and walk.vwap is not None, "book_depth_insufficient")
    _require(walk.shares >= minimum, "book_min_order_size_not_met")
    _require(0 < walk.vwap < 1, "book_vwap_unrepresentable")
    fee = fee_per_share(walk.vwap, rate)
    gross = side["reference_probability"] - walk.vwap
    side.update(buy_vwap=walk.vwap, estimated_taker_fee_per_share=fee, estimated_buy_cost_per_share=walk.vwap + fee,
                gross_reference_difference_cents=100 * gross,
                net_reference_difference_cents=100 * (gross - fee))


def compare_totals(evidence: object) -> dict:
    """Return a research report with both sides and explicit rejection reasons."""
    report = {"status": "rejected", "reasons": [], "paper_only": PAPER_ONLY,
              "report_only": REPORT_ONLY, "readonly": READONLY}
    try:
        request = _object(evidence, "evidence_invalid")
        cutoff = _utc(request.get("decision_at"), "decision_at_invalid")
        notional = _finite_float(request.get("notional_usd", 20), "notional_invalid")
        _require(notional >= 0.01, "notional_invalid")
        report.update(decision_at=cutoff.isoformat(), notional_usd=notional)
        polymarket = _object(request.get("polymarket"), "polymarket_evidence_missing")
        external = _object(request.get("external"), "external_evidence_missing")
        binding, kickoff = _fixture_binding(request.get("fixture_binding"), polymarket, external, cutoff)
        report["fixture_binding"] = binding

        market = _object(polymarket.get("market"), "polymarket_market_missing")
        _require(market.get("fixture_id") == binding["polymarket"]["fixture_id"], "polymarket_market_fixture_conflict")
        _require(_utc(market.get("kickoff_at"), "polymarket_market_kickoff_invalid") == kickoff,
                 "polymarket_market_kickoff_conflict")
        market_id = _text(market.get("market_id"), "polymarket_market_id_missing")
        source = _text(market.get("source_reference"), "polymarket_market_source_missing")
        fetched = None
        if market.get("fetched_at") is not None:
            fetched = _fetched_at(market, cutoff, "polymarket_market", recent=False)
        condition = _text(market.get("condition_id"), "polymarket_condition_invalid")
        _require(re.fullmatch(r"0x[0-9a-f]{64}", condition) is not None, "polymarket_condition_invalid")
        tokens = _token_map(market)
        line = _line(market, "polymarket")
        report.update(line=format(line, "f"), kind="total_goals", period=_PERIOD)
        availability = _market_availability(market, fetched, cutoff, report["reasons"])
        report["polymarket"] = {"market_id": market_id, "condition_id": condition,
                                "source_reference": source, "fetched_at": fetched.isoformat() if fetched else None,
                                **availability}

        sides = _external_quotes(external, binding["external"], line, cutoff, report)
        report["sides"] = sides
        for side, token in tokens.items():
            sides[side]["token_id"] = token
        terms = {}
        rule_identities = {
            "polymarket": {"market_id": market_id, "condition_id": condition},
            "external": {key: report["external"][key] for key in ("market_id", "provider_id")},
        }
        for name, venue in (("polymarket", polymarket), ("external", external)):
            report[name]["rules"] = None
            identity = {"fixture_id": binding[name]["fixture_id"], **rule_identities[name]}
            try:
                capture, terms[name] = _rules(venue.get("rules"), identity, cutoff, kickoff, name)
                report[name]["rules"] = {**capture, **terms[name]}
            except _Rejected as rejected:
                report["reasons"].append(str(rejected))
        if len(terms) == 2:
            conflicts = [key for key, value in terms["polymarket"].items() if terms["external"][key] != value]
            if conflicts:
                report["settlement_rule_conflicts"] = conflicts
                report["reasons"].append("settlement_rules_conflict")

        rate = _fee_rate(market)
        report["polymarket"]["taker_fee_rate"] = rate
        report["polymarket"]["fee_method"] = "fee_per_share_at_walked_vwap"
        books = _books(polymarket.get("books"), condition, tokens)
        for side, token in tokens.items():
            try:
                _require(token in books, "book_missing")
                _buy_price(books[token], sides[side], cutoff, notional, rate)
            except _Rejected as rejected:
                reason = str(rejected)
                sides[side]["book_rejection"] = reason
                report["reasons"].append(f"{reason}:{side}")
        if any("net_reference_difference_cents" in side for side in sides.values()):
            report["differences_indicative"] = bool(report["reasons"])
    except _Rejected as rejected:
        report["reasons"].append(str(rejected))
    if not report["reasons"]:
        report["status"] = "accepted"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("evidence", nargs="?", type=Path, help="JSON evidence file (default stdin)")
    args = parser.parse_args(argv)
    try:
        raw = args.evidence.read_text(encoding="utf-8") if args.evidence else sys.stdin.read()
        evidence = strict_json(raw)
    except (OSError, ValueError) as error:
        parser.error(f"invalid JSON evidence: {error}")
    print(json.dumps(compare_totals(evidence), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
