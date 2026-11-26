from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "testing" / "phase1-test-catalog.md"
TEST_FILE_PATTERN = re.compile(r"(?<![\w/.-])(tests/test_[A-Za-z0-9_./-]+\.py)(?![\w/.-])")

EXPECTED_CHANGED_TEST_PATHS = tuple(
    sorted(
        Path(path)
        for path in (
            "tests/test_category_playbook_category_threshold_domain_policy_readiness.py",
            "tests/test_cli_report_discovery.py",
            "tests/test_forecast_context_readiness_report.py",
            "tests/test_information_freshness_refresh_sla_readiness_report.py",
            "tests/test_input_failure_degradation_readiness_report.py",
            "tests/test_manual_operator_decision_packet.py",
            "tests/test_market_discovery_candidate_pool.py",
            "tests/test_operator_final_go_no_go_packet_readiness_report.py",
            "tests/test_outcome_resolution_evidence_readiness_report.py",
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
            "tests/test_portfolio_probability_event_readiness_report.py",
            "tests/test_post_settlement_calibration_experience_feedback_report.py",
            "tests/test_probability_event_cost_adjusted_position_recommendation_report.py",
            "tests/test_probability_event_market_signal_risk_readiness_report.py",
            "tests/test_probability_event_recommendation_rank_explainability_report.py",
            "tests/test_source_scraping_tool_coverage_readiness_report.py",
            "tests/test_specialist_team_routing_taxonomy_readiness_report.py",
            "tests/test_strategy_phase1_readiness_aggregator.py",
            "tests/test_supabase_local_dsn.py",
        )
    ),
)


def _manifest_test_paths(text: str) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                Path(match.group(1))
                for match in TEST_FILE_PATTERN.finditer(text)
                if ".." not in Path(match.group(1)).parts
            },
        ),
    )


def test_phase1_targeted_test_manifest_references_existing_test_files() -> None:
    assert MANIFEST_PATH.is_file()

    test_paths = _manifest_test_paths(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(EXPECTED_CHANGED_TEST_PATHS) == 33
    assert test_paths == EXPECTED_CHANGED_TEST_PATHS

    missing_paths = tuple(path for path in test_paths if not (REPO_ROOT / path).is_file())
    assert missing_paths == ()
