"""Model auto-optimization core: spec identity and model families.

MO-1 scope (``docs/handoffs/2026-11-19-audit-optimizer/05-development-plan.md``
node MO-1; design 04 section 8): the model-spec identity layer
(``optimize.spec``) and the family interface (``optimize.families``).
Everything here is pure: standard library only, no network, no
database driver, no clock reads, no file I/O, no third-party
dependencies.

Import discipline (pinned by ``tests/test_papersim_optimize_spec.py``):
importing this subpackage adds exactly ``optimize`` and ``optimize.spec``
to the module set of a bare ``import polymarket_alpha_lab.papersim``
baseline and nothing else. Family modules import model source code and
therefore load lazily through PEP 562 in ``optimize.families``. The
papersim CLI startup path never imports this subpackage; command
wiring arrives with MO-6 behind on-demand imports (the startup module
budget is 20, currently 19).
"""

from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    canonical_value,
    decimal_text,
    freeze_value,
    validate_param,
)

__all__ = [
    "ModelSpec",
    "ParamDef",
    "canonical_value",
    "decimal_text",
    "freeze_value",
    "validate_param",
]
