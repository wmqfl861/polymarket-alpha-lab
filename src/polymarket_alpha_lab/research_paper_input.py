"""One bounded supplied scenario set from stdin; not a file business-data store."""
from dataclasses import fields
from datetime import datetime

from polymarket_alpha_lab.research_paper import (
    MAX_INPUT_BYTES, MAX_SCENARIOS, ResearchPaperCosts, ResearchPaperScenario,
    _decimal_string, scenarios_copy,
)
from polymarket_alpha_lab.team_research_agent_types import strict_json
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot

_NUMBER_FIELDS = ('requested_shares', 'min_net_edge', 'min_confidence', 'max_spread', 'max_entry_cost')
_FIELDS = {f.name for f in fields(ResearchPaperScenario)} - {'paper_only', 'report_only', 'readonly'}


def decode_paper_scenarios(raw: bytes):
    """Amounts must be exact decimal strings; source JSON strings retain bytes."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_INPUT_BYTES:
            raise ValueError
        data = strict_json(raw.decode('utf-8'))
        if (type(data) is not dict or set(data) != {'scenarios'} or type(data['scenarios']) is not list
                or not 1 <= len(data['scenarios']) <= MAX_SCENARIOS):
            raise ValueError
        values = []
        for item in data['scenarios']:
            if type(item) is not dict or set(item) != _FIELDS:
                raise ValueError
            costs, market = item['costs'], item['market']
            if type(costs) is not dict or set(costs) != {f.name for f in fields(ResearchPaperCosts)}:
                raise ValueError
            if type(market) is not dict or set(market) != {'market_slug', 'fetched_at', 'raw_json'}:
                raise ValueError
            item = dict(item)
            item['market'] = GammaMarketSnapshot(market['market_slug'], datetime.fromisoformat(market['fetched_at']),
                                                market['raw_json'].encode('utf-8'))
            item['costs'] = ResearchPaperCosts(**{k: _decimal_string(v) for k, v in costs.items()})
            item['book_json'] = item['book_json'].encode('utf-8')
            for name in ('decision_at', 'book_captured_at'):
                item[name] = datetime.fromisoformat(item[name])
            for name in _NUMBER_FIELDS:
                item[name] = _decimal_string(item[name])
            values.append(ResearchPaperScenario(**item))
        return scenarios_copy(tuple(values))
    except Exception:
        raise ValueError('research_paper_input_invalid') from None
