"""Model families: the shared interface plus a lazy family registry (PEP 562).

``base.ModelFamily`` (interface only, standard library plus
``optimize.spec``) is imported eagerly so the protocol is available as
soon as the package is. Family IMPLEMENTATIONS import model source
code (``soccer_model``, ``pipeline``, ``nba_model``, later
``intl_model`` wrappers), so they load lazily: importing
``polymarket_alpha_lab.papersim.optimize`` itself adds no model,
database or network modules beyond what the ``papersim`` package
baseline already loads. The attribute pattern mirrors the lazy
international submodules of ``papersim/__init__.py``.
"""

from __future__ import annotations

from typing import Any

from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily

__all__ = ["ModelFamily", "iter_family_ids"]

_FAMILY_MODULES: dict[str, str] = {
    "club-soccer-frozen": "soccer_frozen",
    # MO-5 lanes each add their own key on their own branch; the
    # three-way merge of these lines is owned by the coordinator.
    "club-soccer-recalib": "soccer_recalib",
    "nba-pace-efficiency": "nba",
}


def iter_family_ids() -> tuple[str, ...]:
    """Registered family ids without importing any family module."""

    return tuple(sorted(_FAMILY_MODULES))


def __getattr__(name: str) -> Any:
    module_name = _FAMILY_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return import_module(f".{module_name}", __name__)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_FAMILY_MODULES))
