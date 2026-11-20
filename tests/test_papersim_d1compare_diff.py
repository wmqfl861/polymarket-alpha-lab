"""D1 candidate-by-candidate diff offline proofs (synthetic fixtures only).

Everything here verifies CODE over synthetic candidate rows: no network
call, no database, no timer, no managed write and no frozen/mirror
script execution.  Synthetic fixtures are code verification only -- no
synthetic or historical cycle counts as a real operation, and nothing
here claims a positive D1 day.  Manual-news inputs are UNKNOWN by
construction; comparator equality is never fabricated.
"""

from __future__ import annotations

import pytest

from polymarket_alpha_lab.papersim.d1compare import diff


def _left(**overrides) -> diff.ComparatorCandidate:
    base = dict(market_id="6237097", side="no", kind="ml", league="Ligue_1",
                decision="enter", stake_usd=150.0, quote=0.74,
                gate_outcomes={"net_edge": True, "var_spread": True},
                snapshot_digest="a" * 64, as_of="2026-11-20T00:05:00Z",
                news="CONCORDANT", provenance="path_l:frozen")
    base.update(overrides)
    return diff.ComparatorCandidate(**base)


def _right(**overrides) -> diff.ComparatorCandidate:
    base = dict(market_id="6237097", side="no", kind="ml", league="Ligue_1",
                decision="enter", stake_usd=150.0, quote=0.74,
                gate_outcomes={"net_edge": True, "var_spread": True},
                snapshot_digest="a" * 64, as_of="2026-11-20T00:05:00Z",
                news=None, provenance="path_n:run-scan")
    base.update(overrides)
    return diff.ComparatorCandidate(**base)


# ---------------------------------------------------------------------------
# field deltas and join keys
# ---------------------------------------------------------------------------


def test_candidate_key_normalizes_side_and_kind() -> None:
    row = diff.ComparatorCandidate(market_id=" 1 ", side=" NO ", kind=" ML ")
    assert row.key() == ("1", "no", "ml")


def test_unknown_never_counts_as_equal_or_different() -> None:
    left = _left(stake_usd=None)
    right = _right()
    deltas = dict((delta.field, delta) for delta in
                  diff.compare_candidates(left, right))
    assert deltas["stake_usd"].status == "unknown_left"
    assert deltas["decision"].status == "equal"
    assert deltas["quote"].status == "equal"


def test_row_from_mapping_is_strict_about_gate_values() -> None:
    row = diff.row_from_mapping(
        {"market_id": "m", "side": "no", "kind": "ml",
         "gate_outcomes": {"a": True, "b": None, "c": False}},
        provenance="x")
    assert row.gate_outcomes == {"a": True, "b": None, "c": False}
    with pytest.raises(diff.DiffError):
        diff.row_from_mapping(
            {"market_id": "m", "side": "no", "kind": "ml",
             "gate_outcomes": {"a": "maybe"}}, provenance="x")
    with pytest.raises(diff.DiffError):
        diff.row_from_mapping({"side": "no", "kind": "ml"}, provenance="x")


# ---------------------------------------------------------------------------
# day diff: classes, news, basketball, honesty of counts
# ---------------------------------------------------------------------------


def test_identical_joined_row_counts_agreement_only_on_known_fields() -> None:
    report = diff.diff_day([_left()], [_right()])
    assert report.rows and report.rows[0].difference_class is None
    assert report.identical_rows == 1
    # decision/stake/quote/snapshot/as-of + 2 gates are known and equal on
    # both sides; news is unknown on the right and excluded entirely
    summary = report.summary()
    assert summary["compared_fields"] == 7
    assert summary["agreement_fields"] == 7
    assert summary["class_counts"] == {diff.DIFF_CLASS_LIBRARY_DEFECT: 0,
                                       diff.DIFF_CLASS_FROZEN_DRIFT: 0,
                                       diff.DIFF_CLASS_INTENTIONAL: 0}


def test_as_of_difference_is_also_a_class_1_difference() -> None:
    report = diff.diff_day([_left(as_of="2026-11-20T00:05:00Z")],
                           [_right(as_of="2026-11-20T00:05:01Z")])
    assert report.rows[0].difference_class == diff.DIFF_CLASS_LIBRARY_DEFECT


def test_decision_difference_is_class_1_library_defect() -> None:
    report = diff.diff_day([_left()], [_right(decision="no_entry")])
    row = report.rows[0]
    assert row.difference_class == diff.DIFF_CLASS_LIBRARY_DEFECT
    assert report.class_counts[diff.DIFF_CLASS_LIBRARY_DEFECT] == 1
    assert report.identical_rows == 0


def test_manual_news_unknown_rows_are_annotated_never_agreement() -> None:
    report = diff.diff_day([_left()], [_right()])
    assert len(report.unknown_news_rows) == 1
    news_row = report.unknown_news_rows[0]
    assert "news" in news_row.unknown_fields
    assert diff.NEWS_PROVENANCE_ANNOTATION in news_row.annotations
    summary = report.summary()
    # the news field is excluded from compared/agreement counts entirely
    deltas = dict((delta.field, delta.status)
                  for delta in diff.compare_candidates(_left(), _right()))
    assert deltas["news"] == "unknown_right"


def test_left_only_and_right_only_rows_get_fixed_classes() -> None:
    report = diff.diff_day([_left(market_id="L1"), _left(market_id="B")],
                           [_right(market_id="R1"), _right(market_id="B")])
    left_only = report.path_l_only[0]
    right_only = report.path_n_only[0]
    assert left_only.difference_class == diff.DIFF_CLASS_FROZEN_DRIFT
    assert right_only.difference_class == diff.DIFF_CLASS_LIBRARY_DEFECT
    assert "legacy ad-hoc" in left_only.annotations[0]
    assert "new-code defect" in right_only.annotations[0]
    assert report.class_counts[diff.DIFF_CLASS_FROZEN_DRIFT] == 1
    assert report.class_counts[diff.DIFF_CLASS_LIBRARY_DEFECT] == 1


def test_basketball_rows_are_captured_not_compared() -> None:
    report = diff.diff_day([_left(league="NBA", market_id="B1")],
                           [_right(league="NBA", market_id="B1")])
    assert len(report.captured_not_compared) == 1
    row = report.captured_not_compared[0]
    assert row.difference_class is None
    assert any("basketball_not_modeled" in note for note in row.annotations)
    assert report.agreement_fields == 0
    assert report.compared_fields == 0
    assert all(count == 0 for count in report.class_counts.values())


def test_fully_unknown_row_is_not_identical_and_not_classified() -> None:
    left = _left(decision=None, stake_usd=None, quote=None,
                 snapshot_digest=None, as_of=None, gate_outcomes={})
    right = _right(decision=None, stake_usd=None, quote=None,
                   snapshot_digest=None, as_of=None, gate_outcomes={})
    report = diff.diff_day([left], [right])
    row = report.rows[0]
    assert row.difference_class is None
    assert report.identical_rows == 0
    assert report.compared_fields == 0
    assert any("unverifiable" in note for note in row.annotations)


def test_duplicate_keys_are_refused() -> None:
    with pytest.raises(diff.DiffError) as error:
        diff.diff_day([_left(), _left()], [])
    assert error.value.code == "diff_duplicate_key"
    with pytest.raises(diff.DiffError):
        diff.diff_day([], [_right(), _right()])


def test_summary_document_carries_pre_registered_items() -> None:
    report = diff.diff_day([_left()], [_right()])
    document = report.as_document()
    assert document["pre_registered_intentional_items"] == \
        list(diff.DIFF_PRE_REGISTERED_ITEMS)
    assert "fx26_event_keys" in document["pre_registered_intentional_items"]
    assert "manual_news_layer_absent" in document["pre_registered_intentional_items"]
    assert "basketball_not_modeled" in document["pre_registered_intentional_items"]
    assert document["news_provenance_annotation"]
