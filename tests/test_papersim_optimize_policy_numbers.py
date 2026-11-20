"""MO-8 registered-number cross-checks against DELIVERY_PLAN 80.7.

Two directions (plan v2 section 1.6): the registration section's BYTES
must contain the registered spellings verbatim (doc-side drift fails),
and the policy constants must equal the registered semantic values
transcribed here as literals -- including the ditto ("同上") cell of
the stage-1 table resolved explicitly to the row above (review P3-10).
The repo root is pinned from this test file's location so the check
runs from any working directory.
"""

from pathlib import Path

from polymarket_alpha_lab.papersim.optimize.policy import (
    CLOSE_DEVIATION_MIN_GAP,
    CLOSE_DEVIATION_MIN_MARKETS,
    ENVELOPE_MIN_RATIO,
    FIRST_EVIDENCE_CI_PERCENTILES,
    FORWARD_THRESHOLDS,
    HANDOVER_WINDOW_DAYS,
    OWNER_CONFIRM_QUEUE_ENTRIES,
    PROMOTE_CI_UPPER_MAX,
    PROMOTE_PAIRED_MEAN_MAX,
    RULE_REGISTERED,
    RULE_VERSION,
    SECOND_EVIDENCE_CI_PERCENTILES,
    SECOND_LOOK_SAMPLE_MULTIPLIER,
    STAGE1_HOLDOUT_MIN_BLOCKS,
    STAGE1_HOLDOUT_MIN_MARKETS,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "DELIVERY_PLAN.md"


def _section_80_7() -> str:
    """The verbatim bytes between the 80.7 and 80.8 headings."""
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("### 80.7")
    end = text.index("### 80.8")
    return text[start:end]


# Registered spellings that must appear verbatim in the frozen section.
_VERBATIM_TOKENS = (
    "2026-11-20",
    "300 场",
    "20 个比赛周区块",
    "150 场",
    "2 个完整比赛周",
    "12 周",
    "同上",
    "120 场",
    "不少于 14 天",
    "10 周",
    "60 场",
    "2 个国际比赛窗口",
    "12 个月",
    "60 个市场",
    "不少于 28 天",
    "6 个月",
    "95% 区间上界 < 0",
    "配对差的均值 ≤ 0",
    "95% 区间上界 ≤ +0.005",
    "不比现役低 1 美分以上",
    "不足 30 个时只标注",
    "从 95% 收紧到 99%",
    "样本翻倍",
    "距上次换届不足 28 天",
    "30 笔以上入场",
    "晋级，待确认",
    "0.8 倍",
)


def test_registration_section_carries_the_registered_spellings():
    section = _section_80_7()
    for token in _VERBATIM_TOKENS:
        assert token in section, token


def test_policy_constants_match_the_registration():
    # Decision-condition row (80.7 second table).
    assert PROMOTE_PAIRED_MEAN_MAX == 0.0            # 配对差的均值 ≤ 0（含）
    assert PROMOTE_CI_UPPER_MAX == 0.005             # 上界 ≤ +0.005（含）
    assert FIRST_EVIDENCE_CI_PERCENTILES == (0.5, 99.5)   # 第一次判 99%
    assert SECOND_EVIDENCE_CI_PERCENTILES == (2.5, 97.5)  # 第二次判 95%
    assert SECOND_LOOK_SAMPLE_MULTIPLIER == 2        # 样本翻倍判第二次
    assert CLOSE_DEVIATION_MIN_GAP == -0.01          # 不比现役低 1 美分以上（含）
    assert CLOSE_DEVIATION_MIN_MARKETS == 30         # 不足 30 个只标注
    assert OWNER_CONFIRM_QUEUE_ENTRIES == 30         # 满 30 笔改 owner 确认
    assert HANDOVER_WINDOW_DAYS == 28                # 不足 28 天拦截
    assert ENVELOPE_MIN_RATIO == 0.8                 # 包络不得低于 0.8 倍
    assert STAGE1_HOLDOUT_MIN_MARKETS == 300         # 留出期下限（ditto 行同值）
    assert STAGE1_HOLDOUT_MIN_BLOCKS == 20           # 20 个比赛周区块
    # Rule identity.
    assert RULE_VERSION == "promo-rule-v1"
    assert RULE_REGISTERED == "2026-11-20"
    # Forward minimum rows (first table), per family/target class.
    rows = {key: row for key, row in FORWARD_THRESHOLDS.items()}
    ou = rows[("club-soccer-frozen", "over_under")]
    wdl = rows[("club-soccer-recalib", "match_winner")]
    nba = rows[("nba-pace-efficiency", "moneyline")]
    intl = rows[("intl-elo-poisson", "match_winner")]
    crypto = rows[("crypto-gbm", "touch")]
    assert (ou.min_markets, ou.min_complete_blocks, ou.max_shadow_days) \
        == (150, 2, 84)          # 150 场、2 完整比赛周、12 周
    assert (wdl.min_markets, wdl.min_complete_blocks,
            wdl.max_shadow_days) == (300, 2, 84)   # 300 场、同上、12 周
    assert (nba.min_markets, nba.min_span_days, nba.max_shadow_days) \
        == (120, 14, 70)         # 120 场、不少于 14 天、10 周
    assert (intl.min_markets, intl.min_complete_blocks,
            intl.max_shadow_days) == (60, 2, 365)  # 60 场、2 窗口、12 个月
    assert (crypto.min_markets, crypto.min_span_days,
            crypto.max_shadow_days) == (60, 28, 182)  # 60 市场、28 天、6 个月


def test_ditto_cell_resolves_to_the_row_above():
    # The stage-1 column of the WDL row literally reads "同上"; its
    # registered value is the club-soccer row above it (300 matches,
    # 20 matchweek blocks) -- resolved explicitly, never guessed.
    section = _section_80_7()
    wdl_row = [line for line in section.splitlines()
               if line.startswith("| 俱乐部足球（胜平负）")][0]
    assert "同上" in wdl_row
    assert "300 场" in [line for line in section.splitlines()
                       if line.startswith("| 俱乐部足球（大小球、两队进球、球队总进球）")][0]
    assert STAGE1_HOLDOUT_MIN_MARKETS == 300
    assert STAGE1_HOLDOUT_MIN_BLOCKS == 20
