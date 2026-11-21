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
    # Keep D1's authorized client choices separate from project review.
    operating_policy = normalized.split("## scope", 1)[1].split(
        "## omo / sisyphus session workflow", 1
    )[0]
    assert "opencode" not in operating_policy
    push_policy = operating_policy.split("## codex node push policy", 1)[1]
    assert "use zcode for node verification under" in push_policy
    _assert_phrases_appear_in_order(
        push_policy,
        (
            "the configured post-node external review gate passes through an"
            " independent read-only zcode session (project iron rule 4)"
            " with `verdict: pass`",
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
    instructions, _ = _agents_instructions()
    rule = instructions.split("\n4. **independent zcode review.**", 1)[1].split(
        "\n5. ", 1
    )[0]
    review = " ".join(rule.split())

    for requirement in (
        "independent, freshly dispatched read-only zcode session",
        "had no role in planning or implementing the reviewed change or executing its tests",
        "review prompts must be read-only",
        "must not modify, create, or delete files",
        "start the actual zcode client in `guarded` mode",
        "shell, editing, writing, and all other state-changing tools explicitly disabled",
        "verify that the session started",
        "record the reviewed plan or candidate revision",
        "explicit verdict line (`verdict: pass` or `verdict: fail` with findings)",
        "do not impose a fixed elapsed-time deadline on a review session",
        "let it run to an explicit conclusion",
        "do not interrupt, duplicate, or replace it while it is still working",
        "return a signed failure to the original reviewer for re-review",
        "record the original session, original verdict, and re-review result",
        "preserve the original `verdict: fail` even when a new zcode gate is required",
        "if a required zcode session cannot be dispatched, zcode cannot produce a"
        " valid verdict, or the original reviewer is unreachable, treat the review"
        " gate as blocked and report it to the owner",
        "only the required reviewer may clear that gate",
    ):
        assert requirement in review, requirement


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
    owner = normalized.split(
        "## current owner instruction: freeze v1 and close six work packages", 1
    )[1].split("## current owner instruction: complete github-first handoffs", 1)[0]
    for client in ("codex", "claude code", "opencode", "grok cli", "zcode cli"):
        assert client in owner
    assert "self-review" in owner
    assert "d1's local research-client choices govern the product adapter" in normalized
    assert "opencode invocation settings apply only to opencode calls" in normalized
    section = instructions.split("## review / audit defaults", 1)[1].split("\n## ", 1)[0]
    review = " ".join(section.split())
    assert "opencode" not in review
    assert "apply project iron rule 4 to the independent zcode gates" in review
    assert "obtain `verdict: pass` on the applicable plan review before implementation" in review
    assert "on the applicable code or stage review before landing a stage" in review


def test_native_subagent_role_model_and_effort_are_explicit():
    instructions, _ = _agents_instructions()
    heading = "## project agent routing"
    assert heading in instructions
    section = instructions.split(heading, 1)[1].split("\n## ", 1)[0]
    routing = " ".join(section.split())

    bullets = re.findall(r"(?m)^- .+(?:\n  .+)*", section)
    assert len(bullets) == 3
    drafting, development, other_work = (" ".join(bullet.split()) for bullet in bullets)
    assert drafting.startswith("- **plan drafting and substantive revision** use native codex")
    assert '`model="gpt-6.2-astra"` and `reasoning_effort="max"`' in drafting
    assert development.startswith("- **development, implementation, and fixes** use native codex")
    assert '`model="gpt-6.2-sol"` and `reasoning_effort="max"`' in development
    assert "changes to test code" in development
    assert other_work.startswith(
        "- **all other project work, including unspecified tasks**, goes to the"
        " actual local zcode client on linux."
    )
    for role in (
        "plan review",
        "code review",
        "independent review",
        "stage and handoff reviews",
        "investigation",
        "test execution",
        "independent acceptance",
        "documentation inventory",
        "handoff preparation",
        "independent arbitration within delegated authority",
    ):
        assert role in other_work, role
        assert role not in drafting and role not in development, role

    assert "apply the same routing to nested dispatches" in routing
    assert "pass both settings explicitly on every native dispatch" in routing
    assert "never inherit the coordinator's or parent's model or effort" in routing
    assert "keep fast mode off for every role" in routing
    assert "record the actual zcode session identity and result" in routing
    assert "record the backend only if the client discloses it" in routing
    assert "preserve the original executor, progress, and actual receipts" in routing
    assert "record any newly required zcode gates with their own evidence" in routing


def test_root_has_one_project_subagent_model_rule():
    instructions, _ = _agents_instructions()
    active = instructions.split("## omo / sisyphus session workflow (historical)", 1)[0]

    routing_heading = "## project agent routing"
    coordination_heading = "## 协调、复审与授权"
    assert active.count(routing_heading) == 1
    assert active.count(coordination_heading) == 1
    assert "## native codex subagent routing" not in active
    routing = active.split(routing_heading, 1)[1].split("\n## ", 1)[0]
    coordination = active.split(coordination_heading, 1)[1].split("\n## ", 1)[0]
    model_pattern = r"\bgpt-\d[\w.-]*\b"
    expected = ["gpt-6.2-astra", "gpt-6.2-sol"]
    assert re.findall(model_pattern, routing) == expected
    assert re.findall(model_pattern, coordination) == expected
    assert re.findall(model_pattern, active) == expected * 2
    assert "计划编写和实质修订由原生 codex `gpt-6.2-astra` 子代理承担。" in coordination
    assert "开发、实施及修复由原生 codex `gpt-6.2-sol` 子代理承担，包括修改测试代码。" in coordination
    _, delimiter, other_work = coordination.partition(
        "其余所有项目工作，包括未列明的任务，交本机实际 zcode 客户端"
    )
    assert delimiter
    other_work = other_work.split("。", 1)[0]
    for role in (
        "计划审核",
        "代码审核",
        "独立复审",
        "阶段和交接审核",
        "调查",
        "测试执行",
        "独立验收",
        "文档清点",
        "交接准备",
        "既有授权内的独立裁定",
    ):
        assert role in other_work, role
    assert "嵌套派工沿用同一分工" in coordination
    assert "每次原生派工显式指定模型和 `reasoning_effort=max`，各角色关闭快速模式" in coordination
    assert "独立复审按铁律 4 执行" in coordination
    assert "根规则须由 owner 明确确认拟合入的完整正文，方可经 pr 合入" in coordination
    assert "涉及真实资金、凭据或真实下单，须由 owner 本人单独明确确认" in coordination
    assert "inherit the current session model" not in active
