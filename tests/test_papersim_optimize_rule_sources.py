"""Source ownership and compatibility checks for the registered v1 rules."""

import ast
from pathlib import Path

from polymarket_alpha_lab.papersim.optimize import policy, registry, runner


def _syntax(module):
    return ast.parse(Path(module.__file__).read_bytes())


def _assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
                if isinstance(target, ast.Tuple) and any(item.id == name for item in target.elts
                                                        if isinstance(item, ast.Name)):
                    return node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return node.value
    return None


def _dict_value(dictionary, key):
    for field, value in zip(dictionary.keys, dictionary.values, strict=True):
        if isinstance(field, ast.Constant) and field.value == key:
            return value
    raise AssertionError(f"missing rule field {key!r}")


def test_registered_envelope_ratio_has_one_production_definition():
    runner_source = _syntax(runner)
    policy_source = _syntax(policy)
    rules = _assignment(runner_source, "RUN_RULES_V1")
    bindings = {
        "registry": _assignment(_syntax(registry), "_ENVELOPE_MIN_RATIO"),
        "policy": _assignment(policy_source, "ENVELOPE_MIN_RATIO"),
        "runner": _assignment(runner_source, "ENVELOPE_MIN_RATIO"),
        "legacy table": _dict_value(rules, "envelope_width_ratio"),
    }
    owners = [location for location, value in bindings.items() if isinstance(value, ast.Constant)]
    assert owners == ["registry"], owners
    assert bindings["registry"].value == 0.8
    assert _imports(policy_source, registry.__name__)["_ENVELOPE_MIN_RATIO"] == "_ENVELOPE_MIN_RATIO"
    assert ast.unparse(bindings["policy"]) == "_ENVELOPE_MIN_RATIO"
    assert ast.unparse(bindings["runner"]) == "_rule_registry._ENVELOPE_MIN_RATIO"
    assert ast.unparse(bindings["legacy table"]) == "ENVELOPE_MIN_RATIO"
    assert _compare_sources(runner_source, "_run_lineages", "ratio", ast.GtE) == [
        "RUN_RULES_V1['envelope_width_ratio']"
    ]
    assert sorted(_compare_sources(runner_source, "envelope_status", "candidate_median", ast.Lt)) == [
        "0.0", "ENVELOPE_MIN_RATIO * incumbent_median",
    ]


def test_resubmission_and_candidate_capacity_read_the_registry():
    runner_source = _syntax(runner)
    rules = _assignment(runner_source, "RUN_RULES_V1")
    assert ast.unparse(_assignment(runner_source, "RESUBMISSION_BAN_DAYS")) == "_rule_registry._RESUBMISSION_BAN_DAYS"
    assert ast.unparse(_dict_value(rules, "resubmission_days")) == "RESUBMISSION_BAN_DAYS"
    assert _call_kwargs(runner_source, "_run_lineages", "resubmission_blocked", "days") == [
        "RUN_RULES_V1['resubmission_days']"
    ]
    assert _call_kwargs(runner_source, "banned_spec_digests", "dt.timedelta", "days") == ["RESUBMISSION_BAN_DAYS"]
    assert _compare_sources(runner_source, "_run_lineages", "len(state.candidate_shas)", ast.GtE) == ["MAX_CANDIDATES"]
    assert _compare_sources(runner_source, "_publication_block", "len(roles.candidates)", ast.GtE) == ["MAX_CANDIDATES"]


def _function(tree, name):
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _default(tree, function, parameter):
    args = _function(tree, function).args
    return next(default for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                if arg.arg == parameter)


def _call_kwargs(tree, function, callee, keyword):
    return [
        ast.unparse(arg.value)
        for node in ast.walk(_function(tree, function))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == callee
        for arg in node.keywords if arg.arg == keyword
    ]


def _compare_sources(tree, function, left, op):
    return [
        ast.unparse(value)
        for node in ast.walk(_function(tree, function))
        if isinstance(node, ast.Compare)
        and ast.unparse(node.left) == left and len(node.ops) == 1
        and isinstance(node.ops[0], op)
        for value in node.comparators
    ]


def _imports(tree, module):
    return {
        alias.asname or alias.name: alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_holdout_tables_project_the_four_registry_rows():
    source = _syntax(runner)
    facts = _syntax(registry)
    rule_table = _assignment(source, "RUN_RULES_V1")
    assert ast.unparse(_assignment(source, "STAGE1_HOLDOUT_FLOORS")) == "_rule_registry._STAGE1_HOLDOUT_FLOORS"
    for name in ("STAGE1_HOLDOUT_MIN_MARKETS", "STAGE1_HOLDOUT_MIN_BLOCKS"):
        assert ast.unparse(_assignment(_syntax(policy), name)) == "_STAGE1_HOLDOUT_FLOORS['club-soccer']"
    assert ast.literal_eval(_assignment(facts, "_STAGE1_HOLDOUT_FLOORS").args[0]) == {
        "club-soccer": (300, 20), "crypto": (150, 20), "intl": (300, 0), "nba": (300, 30),
    }
    table = _dict_value(rule_table, "stage1_holdout_minimums")
    assert isinstance(table, ast.DictComp)
    assert ast.unparse(table.generators[0].iter) == "_rule_registry._STAGE1_FAMILY_CLASSES.items()"
    assert {
        key: ast.unparse(_dict_value(table.value, key)) for key in ("min_events", "min_blocks")
    } == {
        "min_events": "STAGE1_HOLDOUT_FLOORS[floor_class][0]",
        "min_blocks": "STAGE1_HOLDOUT_FLOORS[floor_class][1]",
    }
    assert _compare_sources(source, "_apply_holm", "float(upper)", ast.Lt) == ["_STAGE1_CI_UPPER_BOUND"]
    for entry in ("evaluate_stage1_rule", "execute_optimization_run"):
        assert _compare_sources(source, entry, "stat.ci95_upper", ast.Lt) == ["_STAGE1_CI_UPPER_BOUND"]
    assert ast.literal_eval(_assignment(facts, "_STAGE1_CI_UPPER_BOUND")) == 0.0
    assert ast.unparse(_assignment(source, "_STAGE1_CI_UPPER_BOUND")) == "_rule_registry._STAGE1_CI_UPPER_BOUND"


def test_forward_tables_and_registration_project_the_registry_and_policy():
    policy_source, runner_source, registry_source = map(_syntax, (policy, runner, registry))
    source = Path(runner.__file__).parents[1] / "store_psycopg.py"
    store_source = ast.parse(source.read_bytes())
    rules = _assignment(runner_source, "RUN_RULES_V1")
    thresholds = _assignment(policy_source, "FORWARD_THRESHOLDS")
    assert isinstance(thresholds, ast.DictComp)
    assert ast.unparse(thresholds.value) == "_THRESHOLDS[row_id]"
    assert ast.unparse(thresholds.generators[0].iter) == "_FORWARD_TARGET_ROWS.items()"
    rows = _assignment(policy_source, "_THRESHOLDS")
    assert isinstance(rows, ast.DictComp)
    assert ast.unparse(rows.generators[0].iter) == "_FORWARD_ROWS.items()"
    assert {kw.arg: ast.unparse(kw.value) for kw in rows.value.keywords} == {
        "min_markets": "row.min_events", "max_shadow_days": "_shadow_window_days(row.shadow_window)",
        "family_class": "key", "min_complete_blocks": "row.min_complete_blocks",
        "min_span_days": "row.min_span_days",
    }
    club_rows = _assignment(registry_source, "_FORWARD_ROWS").args[0]
    for key in ("club-soccer-ou", "club-soccer-wdl"):
        assert ast.unparse(_dict_value(club_rows, key).keywords[0].value) == "_CLUB_FORWARD_MATCHWEEKS"
    legacy = _dict_value(rules, "stage2_forward_minimums")
    assert isinstance(legacy, ast.DictComp)
    assert ast.unparse(legacy.generators[0].iter) == "_rule_registry._LEGACY_FORWARD_ROWS.items()"
    assert ast.unparse(_dict_value(legacy.value, "min_events")) == "row.min_events"
    assert ast.unparse(_dict_value(legacy.value, "min_blocks")) == (
        "row.min_complete_blocks if row.min_complete_blocks is not None else row.min_span_days"
    )
    windows = _dict_value(rules, "max_shadow_windows")
    assert isinstance(windows, ast.DictComp)
    assert ast.unparse(windows.generators[0].iter) == "_rule_registry._SHADOW_WINDOWS"
    assert ast.unparse(windows.value) == "_rule_registry._shadow_window_label(key)"
    legacy_plan = _function(runner_source, "_forward_plan")
    assert any(
        isinstance(node, ast.ImportFrom) and node.module.endswith(".policy")
        and {alias.name for alias in node.names} == {"FORWARD_THRESHOLDS", "SECOND_LOOK_SAMPLE_MULTIPLIER"}
        for node in legacy_plan.body
    )
    fallback = next(node.orelse for node in legacy_plan.body if isinstance(node, ast.If))
    assert {
        target.id: ast.unparse(node.value)
        for node in fallback if isinstance(node, ast.Assign) for target in node.targets
    } == {
        "first": "RUN_RULES_V1['stage2_forward_minimums'][key]['min_events']",
        "window_days": "_rule_registry._shadow_window_days(_rule_registry._LEGACY_FORWARD_ROWS[key].shadow_window)",
    }
    assert ast.unparse(_dict_value(legacy_plan.body[-1].value, "final_look_min_events")) == (
        "SECOND_LOOK_SAMPLE_MULTIPLIER * first"
    )
    managed = _function(store_source, "_optimizer_forward_plan")
    assert ast.unparse(_assignment(managed, "row")) == "policy.classify_threshold(identity['family'], lineage.split('/', 1)[1])"
    managed_fields = {
        "first_look_min_events": "row.min_markets",
        "final_look_min_events": "policy.SECOND_LOOK_SAMPLE_MULTIPLIER * row.min_markets",
        "min_complete_blocks": "row.min_complete_blocks",
        "min_span_days": "row.min_span_days",
        "max_shadow_days": "row.max_shadow_days",
        "first_look_ci_percentiles": "list(policy.FIRST_EVIDENCE_CI_PERCENTILES)",
        "final_look_ci_percentiles": "list(policy.SECOND_EVIDENCE_CI_PERCENTILES)",
        "paired_mean_max": "policy.PROMOTE_PAIRED_MEAN_MAX",
        "ci_upper_max": "policy.PROMOTE_CI_UPPER_MAX",
        "close_deviation_min_gap": "policy.CLOSE_DEVIATION_MIN_GAP",
        "close_deviation_min_markets": "policy.CLOSE_DEVIATION_MIN_MARKETS",
    }
    assert {
        key: ast.unparse(_dict_value(managed.body[-1].value, key))
        for key in managed_fields
    } == managed_fields


def test_statistics_have_one_owner_per_registered_parameter():
    from polymarket_alpha_lab.papersim.optimize import metrics, scorecard

    metrics_source, scorecard_source, runner_source = map(_syntax, (metrics, scorecard, runner))
    policy_source = _syntax(policy)
    facts = _syntax(registry)
    metrics_module = metrics.__name__
    for name, value in (("_HOLM_ALPHA_DEFAULT", 0.05), ("_SIGN_FLIP_REPS_DEFAULT", 2000)):
        node = _assignment(metrics_source, name)
        assert isinstance(node, ast.Constant) and node.value == value
    assert ast.literal_eval(_assignment(scorecard_source, "_SIGN_FLIP_SEED_DEFAULT")) == 20261119
    assert ast.unparse(_default(metrics_source, "holm", "alpha")) == "_HOLM_ALPHA_DEFAULT"
    assert ast.unparse(_default(metrics_source, "sign_flip_pvalue", "reps")) == "_SIGN_FLIP_REPS_DEFAULT"
    assert ast.unparse(_default(scorecard_source, "build_scorecard", "sign_flip_reps")) == "_SIGN_FLIP_REPS_DEFAULT"
    assert ast.unparse(_default(scorecard_source, "build_scorecard", "sign_flip_seed")) == "_SIGN_FLIP_SEED_DEFAULT"
    assert _imports(scorecard_source, metrics_module)["_SIGN_FLIP_REPS_DEFAULT"] == "_SIGN_FLIP_REPS_DEFAULT"
    imports = _imports(runner_source, metrics_module)
    assert (imports["HOLM_ALPHA"], imports["SIGN_FLIP_REPS"]) == ("_HOLM_ALPHA_DEFAULT", "_SIGN_FLIP_REPS_DEFAULT")
    assert _imports(runner_source, scorecard.__name__)["SIGN_FLIP_SEED"] == "_SIGN_FLIP_SEED_DEFAULT"
    assert all(_assignment(runner_source, name) is None for name in ("HOLM_ALPHA", "SIGN_FLIP_REPS", "SIGN_FLIP_SEED"))
    assert _call_kwargs(runner_source, "_run_lineages", "sign_flip_pvalue", "seed") == ["seed"]
    assert _call_kwargs(runner_source, "_run_lineages", "sign_flip_pvalue", "reps") == ["SIGN_FLIP_REPS"]
    assert _call_kwargs(runner_source, "_apply_holm", "holm", "alpha") == ["RUN_RULES_V1['alpha']"]
    assert _call_kwargs(runner_source, "evaluate_stage1_rule", "holm", "alpha") == ["HOLM_ALPHA"]
    rules = _assignment(runner_source, "RUN_RULES_V1")
    assert ast.unparse(_dict_value(rules, "alpha")) == "HOLM_ALPHA"
    for field, owner, value in (
        ("fp_rate_max", "_FALSE_POSITIVE_RATE_MAX", 0.05),
        ("detection_min", "_DETECTION_RATE_MIN", 0.80),
        ("promote_ci_upper_max", "_PROMOTE_CI_UPPER_MAX", 0.005),
    ):
        assert ast.unparse(_dict_value(rules, field)) == f"_rule_registry.{owner}"
        assert ast.literal_eval(_assignment(facts, owner)) == value
    assert ast.unparse(_assignment(policy_source, "PROMOTE_CI_UPPER_MAX")) == "_PROMOTE_CI_UPPER_MAX"
    evaluation = "polymarket_alpha_lab.papersim.evaluation"
    assert _imports(runner_source, evaluation)["_BOOTSTRAP_PERCENTILES"] == "BOOTSTRAP_PERCENTILES"
    assert _imports(policy_source, evaluation)["SECOND_EVIDENCE_CI_PERCENTILES"] == "BOOTSTRAP_PERCENTILES"
    assert _assignment(policy_source, "SECOND_EVIDENCE_CI_PERCENTILES") is None
    assert ast.unparse(_assignment(runner_source, "CI_CONFIDENCE_LEVEL")) == (
        "(_BOOTSTRAP_PERCENTILES[1] - _BOOTSTRAP_PERCENTILES[0]) / 100"
    )
    assert ast.unparse(_assignment(policy_source, "FIRST_EVIDENCE_CI_PERCENTILES")) == "_FIRST_EVIDENCE_CI_PERCENTILES"
    assert ast.literal_eval(_assignment(facts, "_FIRST_EVIDENCE_CI_PERCENTILES")) == (0.5, 99.5)
    label = _dict_value(rules, "first_look_ci_tighten")
    assert isinstance(label, ast.BinOp)
    assert [node.attr for node in ast.walk(label) if isinstance(node, ast.Attribute)] == [
        "_FIRST_EVIDENCE_CI_PERCENTILES", "_FIRST_EVIDENCE_CI_PERCENTILES",
    ]
    execute = _function(runner_source, "execute_optimization_run")
    artifact = next(
        node.value for node in ast.walk(execute)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == "statistical_parameters" for key in node.value.keys)
    )
    fields = _dict_value(artifact, "statistical_parameters")
    assert {
        key.value: ast.unparse(value) for key, value in zip(fields.keys, fields.values, strict=True)
    } == {
        "bootstrap_resamples": "bootstrap_resamples()", "ci_confidence_level": "CI_CONFIDENCE_LEVEL",
        "holm_alpha": "HOLM_ALPHA", "sign_flip_reps": "SIGN_FLIP_REPS", "sign_flip_seed": "SIGN_FLIP_SEED",
    }


def test_rule_objects_are_available_at_cold_import():
    import json
    import subprocess
    import sys

    source = Path(runner.__file__).resolve().parents[3]
    program = """
import sys
sys.path.insert(0, sys.argv[1])
import polymarket_alpha_lab.papersim
baseline = set(sys.modules)
from polymarket_alpha_lab.papersim.optimize import runner
loaded = set(sys.modules)
import json
from types import MappingProxyType

rules = runner.RUN_RULES_V1
floors = runner.STAGE1_HOLDOUT_FLOORS
assert type(floors) is MappingProxyType
try:
    floors['crypto'] = (1, 1)
except TypeError:
    pass
else:
    raise AssertionError('mutable floor projection')
assert type(rules) is dict
assert len(rules) == 11
for field in ('stage1_holdout_minimums', 'stage2_forward_minimums'):
    assert type(rules[field]) is dict
    assert len({id(value) for value in rules[field].values()}) == len(rules[field])
    for row in rules[field].values():
        assert type(row) is dict and tuple(row) == ('min_events', 'min_blocks')
        assert all(type(value) is int for value in row.values())
assert type(rules['max_shadow_windows']) is dict
assert all(type(value) is str for value in rules['max_shadow_windows'].values())
for field in ('alpha', 'envelope_width_ratio', 'fp_rate_max', 'detection_min', 'promote_ci_upper_max'):
    assert type(rules[field]) is float
assert type(rules['resubmission_days']) is int
assert type(rules['rule_version']) is type(rules['first_look_ci_tighten']) is str
assert json.loads(json.dumps(rules)) == rules
for module in ('registry', 'metrics', 'scorecard', 'evaluation'):
    before = set(sys.modules)
    path = 'papersim' if module == 'evaluation' else 'papersim.optimize'
    __import__(f'polymarket_alpha_lab.{path}.{module}')
    assert set(sys.modules) == before, module
print(json.dumps({
    'modules': sorted(name.removeprefix('polymarket_alpha_lab.') for name in loaded - baseline
                      if name.startswith('polymarket_alpha_lab.')),
    'rules': list(rules),
    'floors': list(floors),
    'stage1': list(rules['stage1_holdout_minimums']),
    'stage2': list(rules['stage2_forward_minimums']),
    'windows': list(rules['max_shadow_windows']),
}))
"""
    process = subprocess.run([sys.executable, "-IB", "-c", program, str(source)],
                             capture_output=True, text=True, timeout=120, check=True)
    assert json.loads(process.stdout) == {
        "modules": [
            "papersim.evaluation", "papersim.optimize", "papersim.optimize.metrics",
            "papersim.optimize.registry", "papersim.optimize.runner", "papersim.optimize.scorecard",
            "papersim.optimize.search", "papersim.optimize.spec", "papersim.optimize.walkforward",
            "papersim.store", "research_probability_scores", "team_research_agent_types", "team_taxonomy",
        ],
        "rules": [
            "rule_version", "alpha", "envelope_width_ratio", "resubmission_days",
            "fp_rate_max", "detection_min", "stage1_holdout_minimums",
            "stage2_forward_minimums", "max_shadow_windows",
            "promote_ci_upper_max", "first_look_ci_tighten",
        ],
        "floors": ["club-soccer", "crypto", "intl", "nba"],
        "stage1": [
            "club-soccer-recalib", "club-soccer-frozen", "intl-elo-poisson",
            "intl-elo-goal-residuals", "nba-pace-efficiency", "crypto-gbm",
        ],
        "stage2": ["club-soccer(ou/btts/tt)", "club-soccer(match_winner)", "nba", "intl", "crypto"],
        "windows": ["club", "nba", "intl", "crypto"],
    }


def test_policy_public_forward_rows_and_percentile_identity():
    import subprocess
    import sys
    from dataclasses import FrozenInstanceError, fields

    import pytest

    from polymarket_alpha_lab.papersim import evaluation

    assert policy.ForwardThreshold.__module__ == policy.__name__
    assert [(field.name, field.default) for field in fields(policy.ForwardThreshold)[3:]] == [
        ("min_complete_blocks", None), ("min_span_days", None),
    ]
    assert [field.name for field in fields(policy.ForwardThreshold)] == [
        "min_markets", "max_shadow_days", "family_class", "min_complete_blocks", "min_span_days",
    ]
    sample = policy.ForwardThreshold(150, 84, "club-soccer-ou")
    with pytest.raises(FrozenInstanceError):
        sample.min_markets = 1

    inventory = {
        ("club-soccer-frozen", "over_under"): "club-soccer-ou",
        ("club-soccer-frozen", "btts"): "club-soccer-ou",
        ("club-soccer-frozen", "team_total"): "club-soccer-ou",
        ("club-soccer-recalib", "over_under"): "club-soccer-ou",
        ("club-soccer-recalib", "match_winner"): "club-soccer-wdl",
        ("nba-pace-efficiency", "moneyline"): "nba",
        ("nba-pace-efficiency", "totals"): "nba",
        ("intl-elo-poisson", "match_winner"): "intl",
        ("intl-elo-poisson", "over_under"): "intl",
        ("intl-elo-goal-residuals", "match_winner"): "intl",
        ("intl-elo-goal-residuals", "over_under"): "intl",
        ("crypto-gbm", "touch"): "crypto",
        ("crypto-gbm", "terminal"): "crypto",
    }
    assert type(policy.FORWARD_THRESHOLDS) is dict
    assert tuple(policy.FORWARD_THRESHOLDS) == tuple(inventory)
    rows = {}
    for pair, name in inventory.items():
        row = policy.classify_threshold(*pair)
        assert type(row) is policy.ForwardThreshold
        assert row.family_class == name
        assert row is rows.setdefault(name, row)
    assert len({id(row) for row in rows.values()}) == 5
    assert [
        (row.min_markets, row.max_shadow_days, row.min_complete_blocks, row.min_span_days)
        for row in rows.values()
    ] == [
        (150, 84, 2, None), (300, 84, 2, None), (120, 70, None, 14),
        (60, 365, 2, None), (60, 182, None, 28),
    ]
    from polymarket_alpha_lab.papersim.optimize.policy import SECOND_EVIDENCE_CI_PERCENTILES

    assert "SECOND_EVIDENCE_CI_PERCENTILES" in policy.__all__
    assert vars(policy)["SECOND_EVIDENCE_CI_PERCENTILES"] is SECOND_EVIDENCE_CI_PERCENTILES
    assert type(SECOND_EVIDENCE_CI_PERCENTILES) is tuple
    assert SECOND_EVIDENCE_CI_PERCENTILES is evaluation.BOOTSTRAP_PERCENTILES

    source = Path(policy.__file__).resolve().parents[3]
    program = """
import sys
sys.path.insert(0, sys.argv[1])
import polymarket_alpha_lab.papersim
baseline = set(sys.modules)
from polymarket_alpha_lab.papersim.optimize import policy
from polymarket_alpha_lab.papersim.optimize.policy import SECOND_EVIDENCE_CI_PERCENTILES
from polymarket_alpha_lab.papersim.evaluation import BOOTSTRAP_PERCENTILES
assert SECOND_EVIDENCE_CI_PERCENTILES is BOOTSTRAP_PERCENTILES
print(repr(sorted(name.removeprefix('polymarket_alpha_lab.') for name in set(sys.modules) - baseline
                  if name.startswith('polymarket_alpha_lab.'))))
assert 'psycopg' not in sys.modules
"""
    process = subprocess.run([sys.executable, "-IB", "-c", program, str(source)],
                             capture_output=True, text=True, timeout=120, check=True)
    assert ast.literal_eval(process.stdout) == [
        "papersim.evaluation", "papersim.optimize", "papersim.optimize.policy",
        "papersim.optimize.registry", "papersim.optimize.spec", "research_probability_scores",
        "team_research_agent_types", "team_taxonomy",
    ]
