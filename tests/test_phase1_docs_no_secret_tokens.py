from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE1_CHANGED_DOC_PATHS = (
    Path("docs/cli/phase1-report-discovery.md"),
    Path("docs/config/phase-1-strategy-screening-schema.md"),
    Path("docs/contracts/phase1-data-field-contracts.md"),
    Path("docs/data_dictionary/phase1-research-decision-objects.md"),
    Path("docs/index/phase1-module-index.md"),
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/operators/phase1-strategy-stack-walkthrough.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/phases/2026-07-12-phase-1-capability-baseline.md"),
    Path("docs/playbooks/phase1-specialist-team-playbooks.md"),
    Path("docs/recommendations/phase1-recommendation-explainability.md"),
    Path("docs/reports/phase1-report-registry.md"),
    Path("docs/research/source-acquisition-quality-policy.md"),
    Path("docs/risk/phase1-risk-capital-settlement-policy.md"),
    Path("docs/roadmap/2026-07-12-project-progress-roadmap.md"),
    Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
    Path("docs/supabase/local-supabase-operations.md"),
    Path("docs/testing/phase1-test-catalog.md"),
)
EXPECTED_PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS = (
    *PHASE1_CHANGED_DOC_PATHS,
    Path("strategy.example.phase1-screening.json"),
)

PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS = EXPECTED_PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "GitHub token",
        re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "GitHub fine-grained token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "private key block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "wallet seed phrase",
        re.compile(
            r"\b(?:seed phrase|mnemonic|recovery phrase)\b\s*[:=]\s*"
            r"(?!<redacted>|redacted|placeholder|example|sample|dummy)"
            r"(?:[a-z]{3,12}\s+){11,23}[a-z]{3,12}\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "password assignment",
        re.compile(
            r"\bpassword\b\s*[:=]\s*"
            r"(?!(?:<redacted>|redacted|placeholder|example|sample|dummy|none|null|\"\"|'')\b)"
            r"[^\s,;#)}\]]{8,}",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "hosted Postgres credential",
        re.compile(
            r"\bpostgres(?:ql)?://"
            r"(?!(?:user|username|postgres|readonly|example|sample|placeholder)(?::|@))"
            r"[^:\s/@]+:[^@\s/]+@"
            r"(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::|/))"
            r"[^/\s]+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "hosted Supabase credential",
        re.compile(
            r"\bhttps://[a-z0-9]{20}\.supabase\.co\b|"
            r"\b(?:service_role|supabase_service_role|supabase_anon_key)\b\s*[:=]\s*"
            r"(?!(?:<redacted>|redacted|placeholder|example|sample|dummy)\b)"
            r"[A-Za-z0-9_.-]{20,}",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "generic secret or API token assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key)\b\s*[:=]\s*"
            r"(?!(?:<redacted>|redacted|placeholder|example|sample|dummy|none|null|\"\"|'')\b)"
            r"[A-Za-z0-9_.-]{20,}",
            flags=re.IGNORECASE,
        ),
    ),
)


def _read_public_surface(path: Path) -> str:
    resolved = REPO_ROOT / path
    assert resolved.exists(), f"{path} must exist"
    return resolved.read_text(encoding="utf-8")


def _secret_like_findings(text: str) -> tuple[tuple[str, str], ...]:
    findings: list[tuple[str, str]] = []
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append((label, match.group(0)))
    return tuple(findings)


def test_secret_scanner_rejects_real_secret_like_samples() -> None:
    unsafe_samples = (
        "token = ghp_0123456789abcdef0123456789abcdef0123",
        "api_key = live_api_key_0123456789abcdef0123456789abcdef",
        "password = super-secret-password",
        "-----BEGIN PRIVATE KEY-----",
        "seed phrase: abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
        "postgresql://app:hosted-password@db.example.com:5432/postgres",
        "https://abcdefghijklmnopqrst.supabase.co",
    )

    for sample in unsafe_samples:
        assert _secret_like_findings(sample), sample


def test_secret_scanner_allows_explicit_redacted_placeholders() -> None:
    allowed_samples = (
        "api_key = <redacted>",
        "auth_token = redacted",
        "secret_key = placeholder",
        "password = example",
        "seed phrase: <redacted>",
        "postgresql://postgres:postgres@localhost:54322/postgres",
        "supabase_service_role = <redacted>",
    )

    for sample in allowed_samples:
        assert _secret_like_findings(sample) == (), sample


def test_phase1_docs_and_strategy_example_do_not_contain_secret_like_tokens() -> None:
    assert len(PHASE1_CHANGED_DOC_PATHS) == 18
    assert len(PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS) == 19
    assert PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS == EXPECTED_PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS

    findings = {
        str(path): _secret_like_findings(_read_public_surface(path))
        for path in PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS
    }

    assert findings == {
        str(path): ()
        for path in PHASE1_PUBLIC_DOC_OR_CONFIG_PATHS
    }
