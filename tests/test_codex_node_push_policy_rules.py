from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def _agents_instructions() -> tuple[str, str]:
    instructions = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    normalized = " ".join(instructions.split())
    return instructions, normalized


def _assert_phrases_appear_in_order(text: str, phrases: tuple[str, ...]) -> None:
    position = -1
    for phrase in phrases:
        next_position = text.index(phrase)
        assert next_position > position
        position = next_position


def test_codex_node_push_policy_preserves_verified_push_gate():
    instructions, normalized = _agents_instructions()

    assert "codex node push policy" in instructions
    assert "focused local commit with a clean worktree" in normalized
    assert "focused tests for the changed surface pass" in normalized
    assert "the full test suite passes" in normalized
    assert "git diff --check" in normalized
    assert "python compile verification passes" in normalized
    assert "codegraph is synced" in normalized
    assert "secret scan finds no leaked credentials or tokens" in normalized
    assert (
        "post-node external review gate passes through an independent read-only"
        " review subagent"
    ) in normalized
    assert "if no independent review subagent can be dispatched" in normalized
    assert "treat the review gate as blocked" in normalized
    assert "there is no other reviewer under the current rules" in normalized
    # Research-client choices in the current owner override are not reviewers.
    # Preserve the historical review restriction below Scope, not a word ban
    # on unrelated, explicitly authorized client-selection instructions.
    legacy_policy = normalized.split("## scope", 1)[1].split(
        "## omo / sisyphus session workflow", 1
    )[0]
    assert "opencode" not in legacy_policy
    _assert_phrases_appear_in_order(
        normalized,
        (
            "the configured post-node external review gate passes through an"
            " independent read-only review subagent",
            "do not push half-finished work",
        ),
    )


def test_codex_node_push_policy_continues_after_verified_push_by_default():
    instructions, normalized = _agents_instructions()

    assert "codex node push policy" in instructions
    assert "verified, committed, reviewed, and pushed to github" in normalized
    assert "continue to the next suitable task by default" in normalized
    assert "do not pause solely because a github push completed" in normalized
    assert "user confirmation is needed" in normalized
    assert "blocker prevents meaningful progress" in normalized
    assert "phase or risk boundary would change" in normalized
    assert "user explicitly asks to stop or pause" in normalized
    assert normalized.index(
        "after a completed codex node is verified, committed, reviewed, and pushed to github",
    ) < normalized.index("## omo / sisyphus session workflow (historical)")


def test_review_subagent_monitoring_has_no_fixed_deadline_or_live_interruption():
    instructions, normalized = _agents_instructions()

    assert (
        "do not impose a fixed elapsed-time deadline on a review subagent"
        in normalized
    )
    assert "let it run to an explicit conclusion" in normalized
    assert (
        "do not interrupt, duplicate, or replace it while it is still working"
        in normalized
    )
    assert "independent, freshly dispatched" in normalized
    assert "had no role in planning or implementing" in normalized
    assert "read-only" in normalized
    assert "must not modify, create, or delete files" in normalized
    assert "explicit verdict line" in normalized
    assert "`verdict: pass`" in normalized
    assert "`verdict: fail`" in normalized


def test_agent_coordination_defaults_capture_parallel_cap_and_conflict_rule():
    instructions, normalized = _agents_instructions()

    assert "agent coordination defaults" in instructions
    assert "project sets no fixed subagent concurrency count" in normalized
    assert "discover usable capacity dynamically" in normalized
    assert "nested subagent depth remains capped at **3**" in normalized
    assert "independent, non-conflicting work" in normalized
    assert "write-scope overlap" in normalized
    assert "avoid assigning multiple subagents to edit the same files" in normalized
    assert "split ownership by non-overlapping files or modules" in normalized


def test_research_client_choices_are_separate_from_review_policy():
    instructions, normalized = _agents_instructions()
    owner = normalized.split("## current owner instruction: complete github-first handoffs", 1)[0]
    for client in ("codex", "claude code", "opencode", "grok cli", "zcode cli"):
        assert client in owner
    assert "self-review" in owner
    review = instructions.split("## review / audit defaults", 1)[1].split("\n## ", 1)[0]
    assert "opencode" not in review
    assert "independent, freshly dispatched read-only review subagent" in review
    assert "had no role in the reviewed change" in review
    assert "no other reviewer under the current rules" in review



def test_native_subagent_role_model_and_effort_are_explicit():
    instructions, _ = _agents_instructions()
    heading = "## native codex subagent routing"
    assert heading in instructions
    section = instructions.split(heading, 1)[1].split("\n## ", 1)[0]
    routing = " ".join(section.split())

    drafting, delimiter, other_work = routing.partition("- **all other subagent work**")
    assert delimiter
    assert "**plan drafting and substantive revision** uses" in drafting
    assert '`model="gpt-6.2-astra"` and `reasoning_effort="max"`' in drafting
    assert "plan review" not in drafting
    assert other_work.startswith(' uses `model="gpt-6.2-sol"` and `reasoning_effort="max"`.')
    for role in (
        "plan review",
        "implementation",
        "testing",
        "exploration",
        "audit",
        "documentation",
        "handoffs",
        "code review",
        "independent arbitration",
    ):
        assert role in other_work

    assert "pass both settings explicitly on every dispatch, including nested dispatches" in routing
    assert "never inherit the coordinator's or parent's model or effort" in routing
    assert "keep fast mode off" in routing


def test_root_has_one_project_subagent_model_rule():
    instructions, _ = _agents_instructions()
    active = instructions.split("## omo / sisyphus session workflow (historical)", 1)[0]

    assert active.count("## native codex subagent routing") == 1
    assert re.findall(r"\bgpt-\d[\w.-]*\b", active) == [
        "gpt-6.2-astra",
        "gpt-6.2-sol",
    ]
    assert re.findall(r"\bzcode(?: cli)?\b", active) == ["zcode cli"]
    assert "inherit the current session model" not in active
