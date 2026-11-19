"""Model-spec identity: canonical text, SHA-256 and model-version strings.

MO-1 of the model auto-optimization line (design
``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
section 8; plan ``05-development-plan.md`` node MO-1). Everything in
this module is pure: standard library only, no network, no database,
no clock reads, no file I/O.

Identity rules (pinned by ``tests/test_papersim_optimize_spec.py``):

- Numbers are written as decimal strings. A ``float`` goes through
  ``repr`` (shortest round trip) into ``Decimal`` and is formatted with
  ``"f"`` so no scientific notation survives: ``1e-9`` and
  ``0.000000001`` produce the same text. An ``int`` never carries a
  decimal point, a ``float`` always does, and ``str`` values carry an
  ``s:`` prefix, so ``8``, ``8.0`` and ``"8"`` never collide.
  Non-finite floats are rejected: identity is undefined for them.
- Mapping keys are sorted lexicographically (keys must be strings);
  arrays (lists/tuples) keep their order. The ten sweep variants of
  the frozen soccer model are regression keys in lineage order
  (``soccer_model.SWEEPS``), so envelope order is part of the identity.
- ``legacy_model_version`` is INTENTIONALLY EXCLUDED from the canonical
  text and the digest: it is a historical alias (today's frozen version
  string such as ``"soccer-r23"``), not spec semantics. Two specs
  identical except for the alias share one identity. Do not "fix"
  this; ``test_spec_sha_changes_with_any_field`` documents the
  exclusion.
- ``canonical_json``/``spec_sha256`` recompute over the CURRENT field
  contents on every call; nothing is cached. The recursive deep
  freezing in ``__post_init__`` (``freeze_value`` wraps every nested
  mapping in ``MappingProxyType``) is what makes sharing instances
  safe; a single plain ``MappingProxyType`` wrap would be a shallow
  guard leaving nested dicts mutable.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

__all__ = [
    "ModelSpec",
    "ParamDef",
    "canonical_value",
    "decimal_text",
    "freeze_value",
    "validate_param",
]

_KINDS = frozenset({"float", "int", "choice"})


def decimal_text(value: float) -> str:
    """Render a finite float as a plain decimal string (no scientific notation).

    ``repr`` picks the shortest string that round-trips; ``Decimal``
    renders it in full decimal form. ``1e-9`` and ``0.000000001`` both
    yield ``"0.000000001"``.
    """

    if not math.isfinite(value):
        raise ValueError(f"non-finite float has no spec identity: {value!r}")
    return format(Decimal(repr(float(value))), "f")


def canonical_value(value: object) -> str:
    """Canonical text fragment of one spec value (see module docstring).

    ``None``/``bool``/``int``/``float``/``str`` are scalars; mappings
    sort their (string) keys; lists and tuples keep their order and
    share one spelling. Any other type is rejected.
    """

    if value is None:
        return "null"
    if value is True:  # bool before int: booleans are not numbers here
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, float):
        text = decimal_text(value)
        if "." not in text:  # e.g. 1e20 -> "100000000000000000000.0"
            text += ".0"
        return f"f:{text}"
    if isinstance(value, str):
        return "s:" + json.dumps(value, ensure_ascii=True)
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(
                    f"spec mapping keys must be str, got {type(key).__name__}: {key!r}"
                )
        items = sorted(value.items())
        body = ",".join(
            f"{canonical_value(key)}:{canonical_value(item)}" for key, item in items
        )
        return "{" + body + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_value(item) for item in value) + "]"
    raise TypeError(f"cannot canonicalize {type(value).__name__}: {value!r}")


def freeze_value(value: object) -> object:
    """Recursively wrap mappings in ``MappingProxyType`` (deep freeze).

    Mappings become read-only proxies at every depth; lists and tuples
    are rebuilt with frozen elements (lists stay lists: model snapshot
    code indexes and iterates them, it never mutates them); scalars
    pass through. Idempotent: an already-frozen mapping re-freezes
    cleanly.
    """

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [freeze_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class ParamDef:
    """One tunable parameter of a model family (design 04, section 8).

    ``kind`` is one of "float", "int" or "choice". ``choices`` is the
    closed option set for "choice"; ``lower``/``upper`` are inclusive
    bounds for numeric kinds; ``season_frozen`` marks parameters that
    may only be re-estimated before a season starts (metadata for the
    guard rules, not a validation concern).
    """

    name: str
    kind: str
    default: object
    choices: tuple = ()
    lower: float | None = None
    upper: float | None = None
    season_frozen: bool = False

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(
                f"ParamDef.kind must be one of {sorted(_KINDS)}, got {self.kind!r}"
            )
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"ParamDef {self.name!r}: choice kind needs choices")


def validate_param(defn: ParamDef, value: object) -> None:
    """Validate one parameter value against its definition; raise ``ValueError``.

    Pure check, no coercion: a ``bool`` never satisfies a numeric kind
    even though it subclasses ``int``.
    """

    if defn.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{defn.name}: expected a number, got {value!r}")
        if not math.isfinite(float(value)):
            raise ValueError(f"{defn.name}: non-finite value {value!r}")
    elif defn.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{defn.name}: expected an int, got {value!r}")
    elif defn.kind == "choice":
        if value not in defn.choices:
            raise ValueError(f"{defn.name}: {value!r} not in choices {defn.choices!r}")
    else:  # pragma: no cover - ParamDef.__post_init__ already enforces kinds
        raise ValueError(f"{defn.name}: unknown kind {defn.kind!r}")
    if defn.kind in ("float", "int") and defn.lower is not None:
        if float(value) < defn.lower:
            raise ValueError(f"{defn.name}: {value!r} below lower bound {defn.lower}")
    if defn.kind in ("float", "int") and defn.upper is not None:
        if float(value) > defn.upper:
            raise ValueError(f"{defn.name}: {value!r} above upper bound {defn.upper}")


@dataclass(frozen=True, eq=False)
class ModelSpec:
    """One model specification: family, procedure version, params, envelope.

    The spec's identity is ``spec_sha256()`` over ``canonical_json()``;
    ``legacy_model_version`` (the frozen baselines' existing version
    string) is an alias deliberately kept out of the identity.
    ``model_version()`` returns the legacy string when set, else
    ``f"{procedure_version}+{spec_sha256()[:12]}"`` so every
    non-baseline spec gets a fresh, content-derived queue identity.
    """

    family: str
    procedure_version: str
    params: Mapping[str, object]
    envelope: tuple[Mapping[str, object], ...]
    data_contract: str
    legacy_model_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", freeze_value(dict(self.params)))
        object.__setattr__(
            self, "envelope", freeze_value(tuple(dict(item) for item in self.envelope))
        )

    def canonical_json(self) -> str:
        """Canonical tagged text of the five identity-bearing fields.

        Keys are sorted; numbers are decimal strings; arrays keep their
        order. ``legacy_model_version`` is deliberately absent (see the
        module docstring). Recomputed from the current field contents
        on every call.
        """

        return canonical_value(
            {
                "family": self.family,
                "procedure_version": self.procedure_version,
                "params": self.params,
                "envelope": self.envelope,
                "data_contract": self.data_contract,
            }
        )

    def spec_sha256(self) -> str:
        """SHA-256 hex digest of ``canonical_json()`` (the spec identity)."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def model_version(self) -> str:
        """Queue-partition version string (see the class docstring)."""

        if self.legacy_model_version is not None:
            return self.legacy_model_version
        return f"{self.procedure_version}+{self.spec_sha256()[:12]}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelSpec):
            return NotImplemented
        return self.canonical_json() == other.canonical_json()

    def __hash__(self) -> int:
        return hash(self.spec_sha256())

    def __repr__(self) -> str:
        return (
            f"ModelSpec(family={self.family!r}, "
            f"procedure_version={self.procedure_version!r}, "
            f"sha256={self.spec_sha256()[:12]}...)"
        )
