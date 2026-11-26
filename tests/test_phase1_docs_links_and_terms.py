from __future__ import annotations

import re
from pathlib import Path


PHASE1_DOC_PATHS = (
    Path("docs/config/phase-1-strategy-screening-schema.md"),
    Path("docs/contracts/phase1-data-field-contracts.md"),
    Path("docs/data_dictionary/phase1-research-decision-objects.md"),
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/phase-1-multi-team-operating-model.md"),
    Path("docs/phase-1-team-memory.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/phases/2026-07-12-phase-1-capability-baseline.md"),
    Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
)

REQUIRED_GLOBAL_TERMS = (
    "Polymarket",
    "paper_only",
    "report_only",
    "readonly",
    "Supabase",
    "Postgres",
    "go/no-go",
)

LOCAL_STORAGE_PATTERNS = (
    r"\blocal Supabase/Postgres\b",
    r"\blocal Supabase\b",
    r"\blocal Postgres\b",
    r"\blocal-only Supabase/Postgres\b",
    r"\bSupabase/Postgres durable report history only\b",
)

GUARDED_STORAGE_TERMS = (
    "hosted supabase",
    "hosted postgres",
    "remote supabase",
    "remote postgres",
    "project.supabase.co",
    "supabase service role",
    "service_role",
)

GUARDED_LIVE_AUTOMATION_TERMS = (
    "live trading",
    "autonomous order",
    "autonomous orders",
    "order placement",
    "order submission",
    "order signing",
    "submit order",
    "sign order",
    "exchange mutation",
    "order mutation",
)

ALLOWED_BOUNDARY_MARKERS = (
    "no ",
    "not ",
    "never ",
    "exclude",
    "excludes",
    "excluded",
    "forbid",
    "forbids",
    "forbidden",
    "out of scope",
    "does not authorize",
    "do not add",
    "do not introduce",
    "must not",
    "none may",
    "prohibited",
    "reject",
    "rejects",
    "without",
    "only",
    "boundary",
)

MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
NEGATED_BULLET_PATTERN = re.compile(r"(?:^| )-\s+(?:a |an |any )?(?:not |no )")


def _read_doc(path: Path) -> str:
    assert path.exists(), f"{path} must exist"
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _markdown_links(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in MARKDOWN_LINK_PATTERN.finditer(text))


def _prose_blocks(text: str) -> tuple[str, ...]:
    lines = text.lower().splitlines()
    blocks: list[str] = []
    current_block: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            if current_block:
                blocks.append(" ".join(current_block))
                current_block = []
            continue
        current_block.append(line.strip())
    if current_block:
        blocks.append(" ".join(current_block))

    return tuple(blocks)


def _prose_blocks_with_context(text: str) -> tuple[tuple[str, str], ...]:
    blocks = _prose_blocks(text)
    return tuple(
        (
            block,
            " ".join(blocks[max(0, index - 1) : index + 2]),
        )
        for index, block in enumerate(blocks)
    )


def _is_external_or_anchor(link: str) -> bool:
    return (
        link.startswith("#")
        or link.startswith("http://")
        or link.startswith("https://")
        or link.startswith("mailto:")
    )


def _link_target(source_path: Path, link: str) -> Path:
    link_path = link.split("#", 1)[0]
    return (source_path.parent / link_path).resolve()


def _is_boundary_context(text: str) -> bool:
    return (
        any(marker in text for marker in ALLOWED_BOUNDARY_MARKERS)
        or bool(NEGATED_BULLET_PATTERN.search(text))
        or "must not include:" in text
        or " is not:" in text
        or " is not a " in text
    )


def test_phase1_docs_paths_exist_and_cover_expected_nodes() -> None:
    assert len(PHASE1_DOC_PATHS) == 9

    for path in PHASE1_DOC_PATHS:
        assert path.exists(), f"{path} must exist"
        assert path.suffix == ".md", f"{path} must be Markdown"


def test_phase1_docs_use_canonical_terms_across_the_doc_set() -> None:
    combined_text = "\n".join(_read_doc(path) for path in PHASE1_DOC_PATHS)
    normalized = _normalized(combined_text)

    for term in REQUIRED_GLOBAL_TERMS:
        assert term in normalized

    lowercase_brand_mentions = tuple(
        match.group(0)
        for match in re.finditer(r"(?<![-_`])\bpolymarket\b(?![-_`])", combined_text)
    )
    assert lowercase_brand_mentions == ()


def test_phase1_docs_keep_storage_local_supabase_postgres_only() -> None:
    for path in PHASE1_DOC_PATHS:
        text = _normalized(_read_doc(path))
        lower_text = text.lower()

        if "supabase" in lower_text or "postgres" in lower_text:
            assert any(re.search(pattern, text) for pattern in LOCAL_STORAGE_PATTERNS), path

        for block, context in _prose_blocks_with_context(_read_doc(path)):
            if any(term in block for term in GUARDED_STORAGE_TERMS):
                assert _is_boundary_context(context), (path, context)


def test_phase1_docs_keep_human_go_no_go_as_operator_boundary() -> None:
    combined_text = _normalized("\n".join(_read_doc(path) for path in PHASE1_DOC_PATHS))
    lower_text = combined_text.lower()

    assert "go/no-go" in lower_text
    assert "operator" in lower_text or "manual" in lower_text
    assert "automatic go/no-go" not in lower_text
    assert "automated go/no-go" not in lower_text
    assert "autonomous go/no-go" not in lower_text


def test_phase1_docs_only_use_live_trading_and_order_terms_as_boundaries() -> None:
    for path in PHASE1_DOC_PATHS:
        for block, context in _prose_blocks_with_context(_read_doc(path)):
            if any(term in block for term in GUARDED_LIVE_AUTOMATION_TERMS):
                assert _is_boundary_context(context), (
                    path,
                    context,
                )


def test_phase1_docs_markdown_cross_references_resolve_to_existing_paths() -> None:
    for path in PHASE1_DOC_PATHS:
        for link in _markdown_links(_read_doc(path)):
            if _is_external_or_anchor(link):
                continue

            assert _link_target(path, link).exists(), f"{path} has broken link {link!r}"
