"""Small-stake sports simulation prices the best ask with the registered model gates."""

import pytest

from polymarket_alpha_lab.papersim import gates, sports_paper_pricing as pricing


NEWS = gates.check_news("synthetic-match", "t30", news_delta=gates.NEWS_UNCHANGED)
FORECAST = {"model_median": 0.63, "qs_variants": (0.61, 0.65)}


def test_thin_and_thick_books_with_same_displayed_price_give_same_decision():
    thin = [{"price": "0.9", "size": "10000"}, {"price": "0.42", "size": "0.001"}]
    thick = [(0.42, 10000)]

    signal = pricing.evaluate_candidate(FORECAST, thin, news=NEWS, fee_rate=0.05)

    assert signal == pricing.evaluate_candidate(FORECAST, thick, news=NEWS, fee_rate=0.05)
    assert signal is not None and signal.decision.passed
    assert signal.entry_price == 0.42
    assert signal.exec_edge == round(0.63 - 0.42 - 0.05 * 0.42 * (1 - 0.42), 4)


def test_fee_can_disqualify_a_direction_after_the_probability_haircut():
    forecast = {"model_median": 0.50, "qs_variants": (0.49, 0.50)}
    asks = [(0.40, 0.01)]
    no_fee = pricing.evaluate_candidate(forecast, asks, news=NEWS, fee_rate=0.0)
    with_fee = pricing.evaluate_candidate(forecast, asks, news=NEWS, fee_rate=0.05)

    assert no_fee is not None and no_fee.decision.passed
    assert with_fee is not None and with_fee.decision.failed == ("exec_edge",)
    assert with_fee.exec_edge_haircut < with_fee.exec_edge < no_fee.exec_edge


@pytest.mark.parametrize(
    ("variants", "price", "under_risk", "news", "gate"),
    [
        ((0.58, 0.70), 0.42, False, NEWS, "variant_spread"),
        ((0.60, 0.65), 0.57, False, NEWS, "worst_edge"),
        ((0.61, 0.65), 0.55, True, NEWS, "exec_edge"),
        ((0.61, 0.65), 0.42, False, None, "news_concordant"),
    ],
)
def test_other_admission_gates_still_block_the_quote(variants, price, under_risk, news, gate):
    forecast = {"model_median": 0.63, "qs_variants": variants}
    signal = pricing.evaluate_candidate(forecast, [(price, 0.001)], news=news, fee_rate=0.05,
                                        under_risk=under_risk)

    assert signal is not None and not signal.decision.passed
    assert gate in signal.decision.failed


def test_invalid_and_empty_asks_do_not_create_a_price_or_signal():
    invalid_levels = [(0.01, 0), (0.02, -1), (0.03, float("nan")), (0.04, float("inf")),
                      (0.05, True), {"price": "nan", "size": 1}, (1, 10), (0, 10), {}]
    assert pricing.best_ask([]) is None
    assert pricing.best_ask(invalid_levels) is None
    assert pricing.evaluate_candidate(FORECAST, invalid_levels, news=NEWS, fee_rate=0.05) is None
    assert pricing.best_ask(iter([*invalid_levels, {"price": ".36001", "size": ".001"}])) == 0.36001


def test_snapshot_covers_full_book_and_only_combines_the_selected_price():
    from datetime import UTC, datetime
    from hashlib import sha256
    import json

    now = datetime(2026, 11, 29, tzinfo=UTC)
    raw = {"asset_id": "901", "market": "synthetic-condition", "timestamp": str(int(now.timestamp()) * 1000),
           "asks": [{"price": ".70", "size": "75"}, {"price": ".40", "size": ".6"},
                    {"price": ".4", "size": ".40"}], "bids": [{"price": ".35", "size": "2"}]}
    def evidence(book):
        return pricing.book_evidence(book, token_id="901", condition_id="synthetic-condition", observed_at=now)

    proof = evidence(raw)
    assert float(proof["best_ask_price"]) == .4
    assert float(proof["best_ask_size"]) == 1
    canonical = json.dumps(raw, separators=(",", ":"), sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert proof["book_snapshot_sha256"] == sha256(canonical.encode()).hexdigest()
    assert proof["book_snapshot_sha256"] != evidence(raw | {"bids": []})["book_snapshot_sha256"]
    assert proof["book_snapshot_sha256"] != evidence(raw | {"asks": raw["asks"][1:]})["book_snapshot_sha256"]
    assert evidence(dict(reversed(list(raw.items())))) == proof
    assert evidence(raw | {"asset_id": "902"}) is None
    assert evidence(raw | {"market": "another-condition"}) is None


def test_displayed_notional_and_fee_inclusive_share_capacity_use_bound_snapshot_only():
    from datetime import UTC, datetime
    from decimal import Decimal

    now = datetime(2026, 11, 29, tzinfo=UTC)
    raw = {"asset_id": "901", "market": "fixture", "asks": [{"price": ".4", "size": "1"}],
           "timestamp": str(int(now.timestamp()) * 1000), "bids": []}
    args = dict(price=Decimal(".4"), fee_per_share=Decimal(".012"), stake_fen=15000,
                token_id="901", condition_id="fixture", observed_at=now)
    proof = pricing.book_evidence(raw, token_id="901", condition_id="fixture", observed_at=now)
    covered = pricing.best_ask_coverage(proof, **args)
    assert covered["status"] == "insufficient" and Decimal(covered["best_ask_notional_usd"]) == Decimal(".4")
    assert Decimal(covered["required_shares"]) * Decimal(".412") * Decimal("6.654376") == Decimal(150)
    borderline = pricing.best_ask_coverage(proof | {"best_ask_size": "55"}, **args)
    assert borderline["status"] == "insufficient" and borderline["fee_inclusive_share_status"] == "sufficient"
    assert pricing.best_ask_coverage(proof | {"best_ask_size": "57"}, **args)["status"] == "sufficient"
    assert pricing.best_ask_coverage(proof, **(args | {"token_id": "902"}))["reason"] == "invalid_snapshot_identity"
    assert pricing.best_ask_coverage(proof, **(args | {"condition_id": "other"}))["reason"] == "invalid_snapshot_identity"
    assert pricing.best_ask_coverage(proof | {"best_ask_price": ".5"}, **args)["reason"] == "invalid_snapshot"
    assert pricing.best_ask_coverage(proof | {"currency_rate": None}, **args)["reason"] == "missing_registered_rate"
    assert pricing.best_ask_coverage({}, **args)["reason"] == "missing_snapshot"
    earlier = datetime(2026, 11, 27, tzinfo=UTC)
    earlier_book = raw | {"timestamp": str(int(earlier.timestamp()) * 1000)}
    earlier_proof = pricing.book_evidence(earlier_book, token_id="901", condition_id="fixture", observed_at=earlier)
    assert earlier_proof["currency_rate"] is None
    assert pricing.best_ask_coverage(earlier_proof, **(args | {"observed_at": earlier}))["reason"] == "missing_registered_rate"
