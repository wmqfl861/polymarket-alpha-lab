from __future__ import annotations

from hashlib import sha256
import re
from pathlib import Path


PHASE1_NEW_DOC_PATHS = (
    Path("docs/config/phase-1-strategy-screening-schema.md"),
    Path("docs/contracts/phase1-data-field-contracts.md"),
    Path("docs/data_dictionary/phase1-research-decision-objects.md"),
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/phases/2026-07-12-phase-1-capability-baseline.md"),
    Path("docs/playbooks/phase1-specialist-team-playbooks.md"),
    Path("docs/research/source-acquisition-quality-policy.md"),
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
ACTIVATION_REVIEW_PATHS = (
    Path("docs/research-dispatch.md"),
    Path("docs/research-local-agent.md"),
    Path("docs/handoffs/g2-activation-readiness.md"),
    Path("docs/handoffs/g2-first-real-run-runbook-draft.md"),
)
REUSABLE_HANDOFF_PATH = Path("docs/handoffs/l7-l8-status.md")


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
    assert len(PHASE1_NEW_DOC_PATHS) == 12

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


def test_project_scope_preserves_research_clients_and_owner_funds_boundary() -> None:
    scope = _normalized(_read(Path("AGENTS.md")))
    clients = scope.split("The D1 research adapter choices are ", 1)[1].split(".", 1)[0]
    for client in ("Codex", "Claude Code", "OpenCode", "Grok CLI", "ZCode CLI"):
        assert client in clients
    assert "Only the owner can authorize real funds, credential use or live orders separately." in scope
    for flag in ("paper_only", "report_only", "readonly"):
        assert f"`{flag}=True`" in scope


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
    handoff = _read(REUSABLE_HANDOFF_PATH)
    for number, section, date, commit in (
        (
            "64.", "3.3", "2026-09-30", "4f84f264d8d762c298f4623459aa24923aab4bc0",
        ),
        (
            "66.", "3.6", "2026-10-07", "df137300c7b293a159588650488996da05f9d68d",
        ),
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
        for source in (receipt, handoff_receipt):
            assert date in source and commit in source, section
        assert f"/blob/{commit}/AGENTS.md)" in receipt
        # The handoff retains its original references at its published revision.
        assert "[Project Agent Routing](../../AGENTS.md#project-agent-routing)" in handoff_receipt


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
