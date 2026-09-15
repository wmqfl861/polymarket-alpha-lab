"""Bounded in-memory inputs for hypothetical research-to-paper replay.

No files, network, new persistence or implied authentication of supplied books.
All monetary assumptions are explicit; legacy default fees are never selected.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
import json

from polymarket_alpha_lab.cost_aware_event_strategy import (
    PaperCostAwareEventCostAssumptions, PaperCostAwareEventStrategyConfig,
)
from polymarket_alpha_lab.domain import OrderBookLevel
from polymarket_alpha_lab.normalize import normalize_order_book
from polymarket_alpha_lab.research_resolution import digest, utc
from polymarket_alpha_lab.team_research_agent_types import hard_flags, identifier, integer, strict_json
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot

MAX_INPUT_BYTES = 2097152
MAX_RAW_BYTES = 131072
MAX_SCENARIOS = 100
MAX_LEVELS = 200
MATH_CONTEXT = Context(prec=64)


def number(value, *, positive=False, maximum=Decimal('1000000')):
    """Exact six-place bounded decimals; reject rather than round inputs."""
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError('paper_decimal_invalid')
    sign, digits, exponent = value.as_tuple()
    if len(digits) > 18 or not -6 <= exponent <= 6 or value < 0 or value > maximum:
        raise ValueError('paper_decimal_invalid')
    if positive and value == 0:
        raise ValueError('paper_decimal_invalid')
    return value


@dataclass(frozen=True, slots=True)
class ResearchPaperBook:
    captured_at: datetime
    raw_json: bytes = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'captured_at', utc('captured_at', self.captured_at))
        if type(self.raw_json) is not bytes or not 1 <= len(self.raw_json) <= MAX_RAW_BYTES:
            raise ValueError('paper_book_size_invalid')
        # Structural validation does not skip bad levels or silently turn an
        # unknown book into an empty one. Venue timestamps/auth are not inferred.
        self.normalized()

    @property
    def content_sha256(self):
        return sha256(self.raw_json).hexdigest()

    def normalized(self):
        raw = strict_json(self.raw_json.decode('utf-8'))
        if type(raw) is not dict or type(raw.get('asset_id')) is not str:
            raise ValueError('paper_book_invalid')
        token = raw['asset_id']
        if not token.isascii() or not token.isdigit() or not 1 <= len(token) <= 78:
            raise ValueError('paper_token_invalid')
        sides = []
        for name in ('bids', 'asks'):
            rows = raw.get(name)
            if type(rows) is not list or len(rows) > MAX_LEVELS:
                raise ValueError('paper_depth_invalid')
            levels = []
            for row in rows:
                if type(row) is not dict or set(row) != {'price', 'size'}:
                    raise ValueError('paper_level_invalid')
                if any(type(row[k]) is not str or not 1 <= len(row[k]) <= 24 for k in row):
                    raise ValueError('paper_level_invalid')
                price = number(Decimal(row['price']), positive=True, maximum=Decimal('1'))
                size = number(Decimal(row['size']), positive=True)
                if price >= 1:
                    raise ValueError('paper_price_invalid')
                levels.append(OrderBookLevel(price, size))
            if len({x.price for x in levels}) != len(levels):
                raise ValueError('paper_duplicate_level')
            sides.append(tuple(sorted(levels, key=lambda x: x.price, reverse=name == 'bids')))
        if sides[0] and sides[1] and sides[0][0].price >= sides[1][0].price:
            raise ValueError('paper_crossed_book')
        return normalize_order_book(raw, captured_at=self.captured_at)


@dataclass(frozen=True, slots=True)
class ResearchPaperScenario:
    record_id: str
    request_sha256: str
    record_sha256: str
    simulated_at: datetime
    shares: Decimal
    market: GammaMarketSnapshot = field(repr=False)
    yes_book: ResearchPaperBook = field(repr=False)
    no_book: ResearchPaperBook = field(repr=False)
    costs: PaperCostAwareEventCostAssumptions
    config: PaperCostAwareEventStrategyConfig
    resolution_risk: Decimal
    cost_reference: str
    max_snapshot_age_seconds: int
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        hard_flags(self)
        identifier('record_id', self.record_id)
        identifier('cost_reference', self.cost_reference)
        digest(self.request_sha256)
        digest(self.record_sha256)
        object.__setattr__(self, 'simulated_at', utc('simulated_at', self.simulated_at))
        integer('max_snapshot_age_seconds', self.max_snapshot_age_seconds, 1, 300)
        number(self.shares, positive=True)
        number(self.resolution_risk, maximum=Decimal('1'))
        for name, expected in (('market', GammaMarketSnapshot), ('yes_book', ResearchPaperBook),
                ('no_book', ResearchPaperBook), ('costs', PaperCostAwareEventCostAssumptions),
                ('config', PaperCostAwareEventStrategyConfig)):
            if type(getattr(self, name)) is not expected:
                raise ValueError('paper_scenario_type_invalid')
            object.__setattr__(self, name, replace(getattr(self, name)))
        if len(self.market.raw_json) > MAX_RAW_BYTES:
            raise ValueError('paper_market_size_invalid')
        for name, value in asdict(self.costs).items():
            number(value, maximum=Decimal('1') if name == 'taker_fee_rate' else Decimal('1000000'))
        for name, value in asdict(self.config).items():
            if name != 'config_version':
                number(value)

    @property
    def content_sha256(self):
        body = asdict(self)
        body['market'] = dict(market_slug=self.market.market_slug,
            fetched_at=self.market.fetched_at, raw_sha256=self.market.content_sha256)
        for name in ('yes_book', 'no_book'):
            book = getattr(self, name)
            body[name] = dict(captured_at=book.captured_at, raw_sha256=book.content_sha256)
        return sha256(json.dumps(body, default=_json_value, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _json_value(value):
    if type(value) is datetime:
        return utc('time', value).isoformat()
    if type(value) is Decimal:
        with localcontext(MATH_CONTEXT):
            return format(value.normalize(), 'f')
    raise TypeError('paper_json_type_invalid')


def copy_scenarios(scenarios):
    if type(scenarios) is not tuple or len(scenarios) > MAX_SCENARIOS:
        raise ValueError('paper_scenario_limit')
    copied, size = [], 0
    for scenario in scenarios:
        if type(scenario) is not ResearchPaperScenario:
            raise ValueError('paper_scenario_type_invalid')
        item = replace(scenario)
        size += sum(len(x.raw_json) for x in (item.market, item.yes_book, item.no_book))
        if size > MAX_INPUT_BYTES:
            raise ValueError('paper_input_limit')
        copied.append(item)
    if len({s.record_id for s in copied}) != len(copied):
        raise ValueError('paper_duplicate_scenario')
    return tuple(copied)


def decode_scenarios(raw: bytes):
    """Closed UTF8 stdin schema; all costs and screening thresholds mandatory."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_INPUT_BYTES:
            raise ValueError
        data = strict_json(raw.decode('utf-8'))
        if type(data) is not list or len(data) > MAX_SCENARIOS:
            raise ValueError
        result = []
        keys = {f.name for f in fields(ResearchPaperScenario)} - {'paper_only', 'readonly', 'report_only'}
        for value in data:
            if type(value) is not dict or set(value) != keys:
                raise ValueError
            value = dict(value)
            for name in ('shares', 'resolution_risk'):
                if type(value[name]) is not str or len(value[name]) > 24:
                    raise ValueError
                value[name] = Decimal(value[name])
            value['simulated_at'] = datetime.fromisoformat(value['simulated_at'])
            for name in ('market', 'yes_book', 'no_book'):
                d = value[name]
                time_key = 'fetched_at' if name == 'market' else 'captured_at'
                required = {time_key, 'raw_json'} | ({'market_slug'} if name == 'market' else set())
                if type(d) is not dict or set(d) != required or type(d['raw_json']) is not str:
                    raise ValueError
                d = dict(d, raw_json=d['raw_json'].encode('utf-8'))
                d[time_key] = datetime.fromisoformat(d[time_key])
                value[name] = (GammaMarketSnapshot if name == 'market' else ResearchPaperBook)(**d)
            for name, cls in (('costs', PaperCostAwareEventCostAssumptions),
                              ('config', PaperCostAwareEventStrategyConfig)):
                d = value[name]
                if type(d) is not dict or set(d) != {f.name for f in fields(cls)}:
                    raise ValueError
                if any(type(v) is not str or len(v) > (128 if k == 'config_version' else 24) for k, v in d.items()):
                    raise ValueError
                value[name] = cls(**{k: v if k == 'config_version' else Decimal(v) for k, v in d.items()})
            result.append(ResearchPaperScenario(**value))
        return copy_scenarios(tuple(result))
    except Exception:
        raise ValueError('paper_scenario_input_invalid') from None


__all__ = ('ResearchPaperBook', 'ResearchPaperScenario', 'decode_scenarios')
