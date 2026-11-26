from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE1_CONFIG_PATH = REPO_ROOT / "strategy.example.phase1-screening.json"
TEST_CATALOG_PATH = REPO_ROOT / "docs/testing/phase1-test-catalog.md"

KEY_PHASE1_MODULE_TEST_PAIRS = (
    (
        "src/polymarket_alpha_lab/category_playbook_category_threshold_domain_policy_readiness.py",
        "tests/test_category_playbook_category_threshold_domain_policy_readiness.py",
    ),
    (
        "src/polymarket_alpha_lab/cli.py",
        "tests/test_cli_report_discovery.py",
    ),
    (
        "src/polymarket_alpha_lab/forecast_context_readiness_report.py",
        "tests/test_forecast_context_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/information_freshness_refresh_sla_readiness_report.py",
        "tests/test_information_freshness_refresh_sla_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/input_failure_degradation_readiness_report.py",
        "tests/test_input_failure_degradation_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/manual_operator_decision_packet.py",
        "tests/test_manual_operator_decision_packet.py",
    ),
    (
        "src/polymarket_alpha_lab/market_discovery_candidate_pool.py",
        "tests/test_market_discovery_candidate_pool.py",
    ),
    (
        "src/polymarket_alpha_lab/operator_final_go_no_go_packet_readiness_report.py",
        "tests/test_operator_final_go_no_go_packet_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/outcome_resolution_evidence_readiness_report.py",
        "tests/test_outcome_resolution_evidence_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/portfolio_probability_event_readiness_report.py",
        "tests/test_portfolio_probability_event_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/post_settlement_calibration_experience_feedback_report.py",
        "tests/test_post_settlement_calibration_experience_feedback_report.py",
    ),
    (
        "src/polymarket_alpha_lab/probability_event_cost_adjusted_position_recommendation_report.py",
        "tests/test_probability_event_cost_adjusted_position_recommendation_report.py",
    ),
    (
        "src/polymarket_alpha_lab/probability_event_market_signal_risk_readiness_report.py",
        "tests/test_probability_event_market_signal_risk_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/probability_event_recommendation_rank_explainability_report.py",
        "tests/test_probability_event_recommendation_rank_explainability_report.py",
    ),
    (
        "src/polymarket_alpha_lab/report_discovery.py",
        "tests/test_cli_report_discovery.py",
    ),
    (
        "src/polymarket_alpha_lab/source_scraping_tool_coverage_readiness_report.py",
        "tests/test_source_scraping_tool_coverage_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/specialist_team_routing_taxonomy_readiness_report.py",
        "tests/test_specialist_team_routing_taxonomy_readiness_report.py",
    ),
    (
        "src/polymarket_alpha_lab/strategy_phase1_readiness_aggregator.py",
        "tests/test_strategy_phase1_readiness_aggregator.py",
    ),
    (
        "src/polymarket_alpha_lab/supabase_local_dsn.py",
        "tests/test_supabase_local_dsn.py",
    ),
)

PHASE1_META_AND_INTEGRATION_TEST_FILES = (
    "tests/test_phase1_config_no_execution_fields.py",
    "tests/test_phase1_docs_links_and_terms.py",
    "tests/test_phase1_docs_module_index_matches_files.py",
    "tests/test_phase1_docs_no_secret_tokens.py",
    "tests/test_phase1_docs_required_terms.py",
    "tests/test_phase1_local_supabase_config_contract.py",
    "tests/test_phase1_manual_packet_no_live_execution_terms.py",
    "tests/test_phase1_file_catalog.py",
    "tests/test_phase1_no_review_scratch_references.py",
    "tests/test_phase1_public_payload_digest_smoke.py",
    "tests/test_phase1_readonly_boundary_scan.py",
    "tests/test_phase1_report_discovery_smoke.py",
    "tests/test_phase1_report_modules_import_no_io.py",
    "tests/test_phase1_strategy_stack_smoke.py",
    "tests/test_phase1_targeted_test_manifest.py",
)

KEY_PHASE1_DOC_PATHS = (
    "docs/cli/phase1-report-discovery.md",
    "docs/config/phase-1-strategy-screening-schema.md",
    "docs/contracts/phase1-data-field-contracts.md",
    "docs/data_dictionary/phase1-research-decision-objects.md",
    "docs/index/phase1-module-index.md",
    "docs/operators/phase1-probability-event-go-no-go-runbook.md",
    "docs/operators/phase1-strategy-stack-walkthrough.md",
    "docs/phase1/probability-event-readonly-supabase-principles.md",
    "docs/phases/2026-07-12-phase-1-capability-baseline.md",
    "docs/playbooks/phase1-specialist-team-playbooks.md",
    "docs/recommendations/phase1-recommendation-explainability.md",
    "docs/reports/phase1-report-registry.md",
    "docs/research/source-acquisition-quality-policy.md",
    "docs/risk/phase1-risk-capital-settlement-policy.md",
    "docs/roadmap/2026-07-12-project-progress-roadmap.md",
    "docs/strategy/phase1-probability-event-filtering-workflow.md",
    "docs/supabase/local-supabase-operations.md",
    "docs/testing/phase1-test-catalog.md",
)

PHASE1_CHANGED_TEST_PATHS = tuple(
    dict.fromkeys(
        (
            *(test for _source, test in KEY_PHASE1_MODULE_TEST_PAIRS),
            *PHASE1_META_AND_INTEGRATION_TEST_FILES,
        ),
    ),
)

PHASE1_PROJECT_INPUT_PATHS = (
    *(source for source, _test in KEY_PHASE1_MODULE_TEST_PAIRS),
    *PHASE1_CHANGED_TEST_PATHS,
    *KEY_PHASE1_DOC_PATHS,
    "strategy.example.phase1-screening.json",
)

MANIFEST_FILE_PATH_PATTERN = re.compile(
    r"(?<![\w/.-])((?:docs/[A-Za-z0-9_./-]+\.md|"
    r"src/polymarket_alpha_lab/[A-Za-z0-9_./-]+\.py|"
    r"tests/test_[A-Za-z0-9_./-]+\.py|"
    r"strategy\.example\.phase1-screening\.json)"
    r")(?![\w/.-])",
)
REVIEW_SCRATCH_REFERENCE_PATTERN = re.compile(
    r"(?:^|[\"'(<\s])(?:\./)?\.review(?:/|\\)",
    flags=re.IGNORECASE | re.MULTILINE,
)
REVIEW_SCRATCH_SCAN_EXCLUSIONS = frozenset(
    {"tests/test_phase1_no_review_scratch_references.py"},
)

SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|private[_-]?key|token|service[_-]?role|password)",
    flags=re.IGNORECASE,
)
PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "<password>",
        "<redacted>",
        "redacted",
        "example",
        "placeholder",
    },
)


def _repo_path(path: str) -> Path:
    return REPO_ROOT / path


def _manifest_file_paths(text: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1) for match in MANIFEST_FILE_PATH_PATTERN.finditer(text)}))


def _walk_json_values(value: object, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            assert isinstance(key, str)
            next_path = (*path, key)
            yield next_path, child
            yield from _walk_json_values(child, next_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json_values(child, (*path, f"[{index}]"))


def _suspicious_secret_values(config: dict[str, object]) -> tuple[str, ...]:
    suspicious: list[str] = []
    for path, value in _walk_json_values(config):
        key_name = path[-1]
        if not SECRET_KEY_PATTERN.search(key_name):
            continue
        if not isinstance(value, str):
            suspicious.append(".".join(path))
            continue
        if value.lower() not in PLACEHOLDER_VALUES and value.startswith("<") is False:
            suspicious.append(".".join(path))
    return tuple(suspicious)


def test_phase1_key_modules_and_their_tests_are_present() -> None:
    assert len(KEY_PHASE1_MODULE_TEST_PAIRS) == 19
    for module_path, test_path in KEY_PHASE1_MODULE_TEST_PAIRS:
        assert _repo_path(module_path).is_file(), f"{module_path} must exist"
        assert _repo_path(test_path).is_file(), f"{test_path} must cover {module_path}"


def test_phase1_guardrail_tests_and_key_docs_are_present() -> None:
    assert len(PHASE1_CHANGED_TEST_PATHS) == 33
    assert len(KEY_PHASE1_DOC_PATHS) == 18

    for test_path in PHASE1_META_AND_INTEGRATION_TEST_FILES:
        assert _repo_path(test_path).is_file(), f"{test_path} must exist"

    for doc_path in KEY_PHASE1_DOC_PATHS:
        assert _repo_path(doc_path).is_file(), f"{doc_path} must exist"
        assert _repo_path(doc_path).suffix == ".md", f"{doc_path} must stay Markdown"


def test_phase1_file_catalog_is_exact_and_directly_tested() -> None:
    assert len(PHASE1_PROJECT_INPUT_PATHS) == 71
    assert len(set(PHASE1_PROJECT_INPUT_PATHS)) == 71
    assert len(KEY_PHASE1_MODULE_TEST_PAIRS) == 19
    assert len(PHASE1_CHANGED_TEST_PATHS) == 33
    assert len(KEY_PHASE1_DOC_PATHS) == 18

    changed_sources = {path for path in PHASE1_PROJECT_INPUT_PATHS if path.startswith("src/")}
    directly_tested_sources = {source for source, _test in KEY_PHASE1_MODULE_TEST_PAIRS}
    assert directly_tested_sources == changed_sources
    assert all(test in PHASE1_CHANGED_TEST_PATHS for _source, test in KEY_PHASE1_MODULE_TEST_PAIRS)


def test_phase1_documented_file_catalog_matches_project_inputs() -> None:
    assert TEST_CATALOG_PATH.is_file()
    listed_paths = _manifest_file_paths(TEST_CATALOG_PATH.read_text(encoding="utf-8"))

    assert listed_paths == tuple(sorted(PHASE1_PROJECT_INPUT_PATHS))


def test_phase1_project_inputs_do_not_reference_review_scratch_space() -> None:
    scratch_refs: list[str] = []
    for relative_path in PHASE1_PROJECT_INPUT_PATHS:
        if relative_path in REVIEW_SCRATCH_SCAN_EXCLUSIONS:
            continue
        path = _repo_path(relative_path)
        text = path.read_text(encoding="utf-8")
        if REVIEW_SCRATCH_REFERENCE_PATTERN.search(text):
            scratch_refs.append(relative_path)

    assert scratch_refs == []


def test_phase1_strategy_example_config_is_parseable_and_secret_free() -> None:
    with PHASE1_CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = json.load(config_file)

    assert isinstance(config, dict)
    assert config["config_version"] == "phase-1-strategy-screening-v0"
    assert {flag: config.get(flag) for flag in ("paper_only", "report_only", "readonly")} == {
        "paper_only": True,
        "report_only": True,
        "readonly": True,
    }
    assert _suspicious_secret_values(config) == ()
