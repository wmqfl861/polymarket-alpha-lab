from __future__ import annotations

from hashlib import sha256
import re
from pathlib import Path


PHASE1_NEW_DOC_PATHS = (
    Path("docs/acceptance/phase-1-development-node-acceptance-checklist.md"),
    Path("docs/config/phase-1-strategy-screening-schema.md"),
    Path("docs/contracts/phase1-data-field-contracts.md"),
    Path("docs/data_dictionary/phase1-research-decision-objects.md"),
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/phases/2026-07-12-phase-1-capability-baseline.md"),
    Path("docs/playbooks/phase1-specialist-team-playbooks.md"),
    Path("docs/quality/phase-1-development-node-quality-gates.md"),
    Path("docs/research/source-acquisition-quality-policy.md"),
    Path("docs/review/2026-07-12-operating-review-rules.md"),
    Path("docs/risk/phase1-risk-capital-settlement-policy.md"),
    Path("docs/roadmap/2026-07-12-project-progress-roadmap.md"),
    Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
    Path("docs/supabase/local-supabase-operations.md"),
)

REQUIRED_CORE_RULE_PATTERNS = {
    "Polymarket probability events": r"\bPolymarket\s+probability[- ]event",
    "manual go/no-go": r"\bmanual\b.{0,120}\bgo/no-go\b|\bgo/no-go\b.{0,120}\bmanual\b",
    "local Supabase/Postgres": r"\blocal(?:-only)?\s+Supabase/Postgres\b",
    "paper_only flag": r"\bpaper_only\b",
    "report_only flag": r"\breport_only\b",
    "readonly flag": r"\breadonly\b",
    "no live trading": (
        r"\bno\s+live\s+trading\b"
        r"|\bdoes\s+not\s+authorize\s+live\s+trading\b"
        r"|\bnot\b.{0,120}\blive\s+trading\b"
        r"|\blive\s+trading\b.{0,120}\b(?:forbidden|out\s+of\s+scope|not)\b"
    ),
    "no wallet/private key/order submission": (
        r"\bno\s+wallet/private\s+key/order\s+submission\b"
        r"|\bno\s+wallet/private\s+keys?\b"
        r"|\bwallet/private-key\s+handling\b"
        r"|\bwallet\s+handling\b.{0,160}\border\s+(?:signing|submission)\b"
        r"|\bno\s+live\s+order,\s+wallet\b.{0,160}\b(?:signing|submission)\b"
    ),
}

BOUNDARY_ONLY_TERMS = (
    "live trading",
    "wallet",
    "wallet/private key",
    "private key",
    "private-key",
    "order submission",
    "order signing",
    "submit order",
)

BOUNDARY_MARKERS = (
    "account mutation",
    "boundary",
    "does not",
    "do not",
    "exclude",
    "excludes",
    "fail closed",
    "forbidden",
    "must not",
    "no ",
    "not ",
    "out of scope",
    "prohibited",
    "readonly",
    "reject",
    "without",
)

CRITICAL_BOUNDARY_DOC_PATHS = (
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/risk/phase1-risk-capital-settlement-policy.md"),
    Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
)

DATA_FIELD_CONTRACT_PATH = Path("docs/contracts/phase1-data-field-contracts.md")
SCREENING_SCHEMA_PATH = Path("docs/config/phase-1-strategy-screening-schema.md")
RISK_POLICY_PATH = Path("docs/risk/phase1-risk-capital-settlement-policy.md")
AGENT_POLICY_PATH = Path("AGENTS.md")
AGENT_CONCURRENCY_PATH = Path(
    "docs/maintenance/phase1-agent-concurrency-and-review-rules.md",
)
REVIEW_MONITORING_PATHS = (
    AGENT_POLICY_PATH,
    AGENT_CONCURRENCY_PATH,
    Path("docs/review/2026-07-12-operating-review-rules.md"),
    Path("docs/roadmap/2026-07-12-project-progress-roadmap.md"),
)

PROJECT_REVIEW_PATHS = (
    AGENT_CONCURRENCY_PATH,
    Path("docs/review/2026-07-12-operating-review-rules.md"),
    Path("docs/review/phase1-claude-review-handoff.md"),
    Path("docs/quality/phase-1-development-node-quality-gates.md"),
    Path("docs/acceptance/phase-1-development-node-acceptance-checklist.md"),
    Path("docs/phase-1-multi-team-operating-model.md"),
    Path("docs/phases/2026-07-12-phase-1-capability-baseline.md"),
    Path("docs/reports/phase1-report-registry.md"),
    Path("docs/roadmap/2026-07-12-project-progress-roadmap.md"),
)
ACTIVATION_REVIEW_PATHS = (
    Path("docs/research-dispatch.md"),
    Path("docs/research-local-agent.md"),
    Path("docs/handoffs/g2-activation-readiness.md"),
    Path("docs/handoffs/g2-first-real-run-runbook-draft.md"),
)


def _read(path: Path) -> str:
    assert path.exists(), f"{path} must exist"
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _combined_text(paths: tuple[Path, ...]) -> str:
    return _normalized("\n".join(_read(path) for path in paths))


def _prose_contexts(text: str) -> tuple[str, ...]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.lower().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped and not (
            current and current[-1].rstrip().endswith((":", "include:"))
        ):
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        current.append(stripped)

    if current:
        blocks.append(" ".join(current))

    return tuple(
        " ".join(blocks[max(0, index - 1) : index + 2])
        for index in range(len(blocks))
    )


def test_probability_event_docs_freeze_canonical_pyes_orientation() -> None:
    paths = (
        Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
        Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
        Path("docs/data_dictionary/phase1-research-decision-objects.md"),
        Path("docs/playbooks/phase1-specialist-team-playbooks.md"),
        Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
    )
    contract = (
        "forecast_probability always denotes canonical Decimal P(YES), "
        "regardless of selected_side; selected_side identifies the paper-review "
        "side being evaluated and never reorients forecast_probability; "
        "P(NO) is 1 - P(YES)."
    )

    for path in paths:
        assert path.exists(), f"{path} must exist"

    texts = {path: path.read_text(encoding="utf-8") for path in paths}
    for path in paths:
        assert contract in texts[path], path

    for path in (
        Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
        Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
    ):
        assert "YES side probability = forecast_probability" in texts[path], path
        assert "NO side probability = 1 - forecast_probability" in texts[path], path

    playbook_text = texts[Path("docs/playbooks/phase1-specialist-team-playbooks.md")]
    calibration_contract = (
        "canonical `P(YES)` with an actual YES target of `1` and actual NO "
        "target of `0`, regardless of which paper-review side was selected"
    )
    assert calibration_contract in playbook_text

    outcome_calibration_contract = (
        "Persisted `actual_outcome` uses the string enum `yes` or `no`; "
        "calibration maps `yes` to Decimal target `1` and `no` to Decimal "
        "target `0`; `selected_side` never changes that mapping."
    )
    assert outcome_calibration_contract in playbook_text

    principles_text = texts[
        Path("docs/phase1/probability-event-readonly-supabase-principles.md")
    ]
    assert "side-aware forecast" not in principles_text.lower()


def test_phase1_new_docs_exist_and_are_markdown() -> None:
    assert len(PHASE1_NEW_DOC_PATHS) == 15

    for path in PHASE1_NEW_DOC_PATHS:
        assert path.suffix == ".md", path
        assert path.exists(), path


def test_phase1_new_docs_keep_required_core_rules_in_corpus() -> None:
    combined_text = _combined_text(PHASE1_NEW_DOC_PATHS)

    for rule_name, pattern in REQUIRED_CORE_RULE_PATTERNS.items():
        assert re.search(pattern, combined_text, flags=re.IGNORECASE), rule_name


def test_phase1_critical_boundary_docs_repeat_required_rules() -> None:
    for path in CRITICAL_BOUNDARY_DOC_PATHS:
        text = _normalized(_read(path))

        for rule_name, pattern in REQUIRED_CORE_RULE_PATTERNS.items():
            if rule_name == "local Supabase/Postgres" and path.name.startswith(
                "phase1-risk",
            ):
                continue
            assert re.search(pattern, text, flags=re.IGNORECASE), (path, rule_name)


def test_phase1_docs_keep_execution_sensitive_terms_as_boundaries() -> None:
    for path in PHASE1_NEW_DOC_PATHS:
        for context in _prose_contexts(_read(path)):
            if any(term in context for term in BOUNDARY_ONLY_TERMS):
                assert any(marker in context for marker in BOUNDARY_MARKERS), (
                    path,
                    context,
                )


def test_phase1_data_contract_freezes_input_failure_timestamp_and_digest_shape() -> None:
    text = _normalized(_read(DATA_FIELD_CONTRACT_PATH))

    for required_text in (
        "InputFailureDegradationReadinessReport.generated_at",
        "exact timezone-aware `datetime`",
        "datetime.UTC",
        "2026-07-12T10:30:00+00:00",
        "must not be shortened to `Z`",
        "Input failure degradation readiness",
        "Probability-event market-signal risk readiness",
        "stored `payload_digest`",
        "InputFailureSignal",
    ):
        assert required_text in text


def test_phase1_docs_freeze_forecast_context_minimums_and_time_ordering() -> None:
    contracts_text = _normalized(_read(DATA_FIELD_CONTRACT_PATH))
    schema_text = _normalized(_read(SCREENING_SCHEMA_PATH))

    assert (
        "published_at <= captured_at <= context_captured_at <= generated_at"
        in contracts_text
    )
    assert "source timestamps must still be `<= generated_at`" in contracts_text
    for required_text in (
        "default_min_source_count",
        "default_min_source_family_count",
        "microstructure_min_source_count",
        "microstructure_min_source_family_count",
        "superforecaster_min_source_count",
        "superforecaster_min_source_family_count",
        "yes_ask_naive_v0",
        "book_imbalance_v0",
        "superforecaster_prompt_v0",
    ):
        assert required_text in schema_text


def test_phase1_risk_policy_freezes_portfolio_watch_reason_rollup() -> None:
    text = _normalized(_read(RISK_POLICY_PATH))

    for reason_code in (
        "same_outcome_dependency_watch",
        "correlation_cluster_concentration_watch",
        "event_category_concentration_watch",
        "capital_lockup_concentration_watch",
        "extended_lockup_watch",
        "negative_edge_after_lockup_watch",
        "low_exit_liquidity_watch",
    ):
        assert f"`{reason_code}`" in text
    assert "all canonical watch reasons" in text
    assert "block reasons plus `low_exit_liquidity_watch` only" in text


def test_project_policy_freezes_sustained_parallel_development_iron_rule() -> None:
    agents_text = _normalized(_read(AGENT_POLICY_PATH))
    concurrency_text = _normalized(_read(AGENT_CONCURRENCY_PATH))

    for required_text in (
        "Sustained parallel development is a project iron rule",
        "The project sets no fixed subagent concurrency count",
        "Nested subagent depth remains capped at **3**",
        "discover usable capacity dynamically",
        "Use fewer threads whenever",
        "multiple modules and multiple development nodes",
        "Reclaim agents immediately",
    ):
        assert required_text in agents_text

    for required_text in (
        "implements Project Iron Rule 6",
        "project defines no fixed subagent-thread count",
        "discover usable capacity dynamically",
        "Nested depth remains capped at **3**",
        "may run fewer active threads",
        "multiple modules and multiple development nodes",
        "Failed or blocked agents must also be reclaimed promptly",
        "primary Codex coordinator follows the `AGENTS.md` Codex Node Push Policy",
        "Subagents must not create commits or push branches",
    ):
        assert required_text in concurrency_text

    for stale_text in (
        "64 active subagent threads",
        "nested subagent depth cap of **4**",
        "nested depth **4**",
    ):
        assert stale_text not in agents_text
        assert stale_text not in concurrency_text


def test_project_policy_freezes_long_running_review_monitoring() -> None:
    for path in REVIEW_MONITORING_PATHS:
        text = _normalized(_read(path))

        if path == AGENT_POLICY_PATH:
            # Owner rule change (2026-09-24): reviews run as independent
            # read-only subagents. The no-fixed-deadline and no-live-
            # interruption protections carry over in the new phrasing.
            assert (
                "Do not impose a fixed elapsed-time deadline on a review"
                " subagent"
            ) in text
            assert "let it run to an explicit conclusion" in text
            assert (
                "do not interrupt, duplicate, or replace it while it is still"
                " working"
            ) in text
            continue
        assert "must not be wrapped in a fixed elapsed-time timeout" in text
        assert "inspectable session" in text
        assert "check them about every 30 seconds" in text
        assert "process/session liveness" in text
        assert "when available, stream growth or event count" in text
        assert "stderr or terminal events, CPU, and network activity" in text
        assert (
            "Elapsed time alone or a quiet interval is not evidence of a stall"
            in text
        )
        assert "do not interrupt, terminate, restart, duplicate, or replace it" in text
        assert "do not route around" in text
        assert "keep waiting and monitoring" in text
        assert "Act only on an explicit result or error" in text
        assert "confirmed process/session exit" in text
        assert "concrete auth/permission/provider failure" in text
        assert "proven stall" in text
        assert "newer user instruction" in text



def test_current_review_documents_reference_the_root_subagent_routing() -> None:
    root_policy = AGENT_POLICY_PATH.resolve()
    for path in PROJECT_REVIEW_PATHS + ACTIVATION_REVIEW_PATHS:
        text = _read(path)
        links = re.findall(r"\]\(([^)\s]+)#native-codex-subagent-routing\)", text)
        assert links, path
        for link in links:
            assert (path.parent / link).resolve() == root_policy, path

    for path in PROJECT_REVIEW_PATHS:
        text = _read(path).lower()
        assert not re.search(r"\b(?:gpt-\d[\w.-]*|claude-opus-[\w.-]+|zcode)\b", text), path


def test_activation_templates_do_not_dispatch_through_retired_approval_rules() -> None:
    for path in ACTIVATION_REVIEW_PATHS:
        text = _normalized(_read(path)).lower()
        for retired_route in (
            "codex consultation under the standing delegation",
            "deciding_authority : codex consultation",
            "returned to codex",
            "native-subagent arbitration per delivery_plan.md 66",
            "the §66 native-subagent arbitration",
        ):
            assert retired_route not in text, (path, retired_route)
        assert "owner" in text, path



def _numbered_section(text: str, heading: str, number: str) -> str:
    next_heading = rf"^#{{1,{len(heading)}}} "
    pattern = rf"(?ms)^{heading} {re.escape(number)} [^\n]*\n.*?(?={next_heading}|\Z)"
    match = re.search(pattern, text)
    assert match, number
    return match.group(0).rstrip("\n")


def test_reusable_dispatch_receipts_reference_historical_commits() -> None:
    delivery = _read(Path("DELIVERY_PLAN.md"))
    handoff = _read(Path("docs/handoffs/l7-l8-status.md"))
    for number, section, date, commit in (
        ("64.", "3.3", "2026-09-30", "4f84f264d8d762c298f4623459aa24923aab4bc0"),
        ("66.", "3.6", "2026-10-07", "df137300c7b293a159588650488996da05f9d68d"),
    ):
        receipt = _numbered_section(delivery, "##", number)
        handoff_receipt = _numbered_section(handoff, "###", section)
        index_row = next(
            (line for line in handoff.splitlines() if line.startswith(f"| {commit[:8]} |")),
            "",
        )
        assert index_row, section
        for source in (receipt, handoff_receipt, index_row):
            assert not re.search(
                r"\b(?:gpt-\d[\w.-]*|zcode)\b|all tasks to native subagents|修改项目规则",
                source,
                flags=re.IGNORECASE,
            ), section
        for source, link in (
            (receipt, "(AGENTS.md#native-codex-subagent-routing)"),
            (handoff_receipt, "(../../AGENTS.md#native-codex-subagent-routing)"),
        ):
            assert date in source and commit in source, section
            assert link in source, section


def test_incident_receipt_preserves_evidence_without_legacy_identity_commands() -> None:
    delivery = _read(Path("DELIVERY_PLAN.md"))
    incident = _numbered_section(delivery, "###", "80.1")
    assert not re.search(r"git\s+config\s+user\.(?:name|email)\b", incident)
    for pin in (
        "fatal: empty ident",
        "7649ad1416c3bf1fafa9cce6e92cc83a277dd9b6",
        "b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8",
        "e25103155015a73b0584eba58b1ffa6d001672cc",
        "VERDICT: PASS",
        "44567005859",
    ):
        assert pin in incident, pin


def test_cleanup_preserves_owner_quote_and_optimizer_rules_v1() -> None:
    delivery = _read(Path("DELIVERY_PLAN.md"))
    # Body digests bind the original signed-off wording and numeric rules.
    for section, expected in (
        ("80.4", "7cd25674956bc2bd600fcb6d7dc7259ab53fdf8c4574484240059c437b4c7723"),
        ("80.7", "7ab6505e08fae8fb5da33bd08a67e5d994a222a10f808254eec56ab785286dce"),
    ):
        _, _, body = _numbered_section(delivery, "###", section).partition("\n\n")
        assert sha256(body.encode("utf-8")).hexdigest() == expected, section
