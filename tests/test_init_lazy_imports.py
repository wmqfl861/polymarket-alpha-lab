"""M3-A1 (FX-20): lazy public exports of the root package.

The root ``__init__`` used to eager-import 76 provider modules to provide
369 public exports. Since M3-A1 the exports resolve lazily through the
module-level ``__getattr__``. These tests pin both the loading behavior and
the frozen export surface.

Regression anchors, all measured on baseline commit 4c8bec2f (the last
eager implementation):

- ``__all__``: exactly 369 names in the same order; SHA-256 of the
  newline-joined list (with trailing newline) is
  ``14358c8d80dc0802ce749d083686febc2fe4f1c9c5e74f9b4ba7e3e747e99c6c``.
- The ``name -> (provider module, attribute)`` table derived mechanically
  from those eager imports: canonical JSON (sorted keys, ``indent=0``)
  SHA-256 ``9dc40e96530de556e2677f6cb2ad6dad62cfd25bb4a437c326f95df2f8cf4cc7``.
- ``__module__`` semantics of all 369 exports (``None`` for the six plain
  constants): canonical JSON SHA-256
  ``816ea3b234908d4d2c7e5f8350eb7a1e1f48c2a95bc3b769ba2152bdfb0d5270``.
- 134 direct submodules were bound as attributes on the package by the
  eager imports as a side effect; sorted newline-joined SHA-256
  ``9baee976926b9a5b89ead57c76a37085a06a704eeb87ab37990b7aee9a95ea17``.
- Startup reference counts on the baseline: a bare root import loaded 135
  project modules; ``polymarket_alpha_lab.papersim.cli`` imported 151 and
  ``polymarket_alpha_lab.papersim.gates`` imported 150. The M3 acceptance
  limit for the papersim.cli startup measurement is 20 project modules
  (plan section 9.2 measurement discipline: fresh process, import only).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
import types

import polymarket_alpha_lab as lab

STARTUP_MODULE_LIMIT = 20


def test_papersim_lazy_international_submodules_keep_existing_attributes() -> None:
    assert _run_python(
        """
        import sys
        import polymarket_alpha_lab.papersim as papersim

        for name, export in (("intl_data", "load_intl_snapshot"), ("intl_model", "IntlFixture")):
            qualified = f"{papersim.__name__}.{name}"
            assert qualified not in sys.modules
            assert name in dir(papersim)
            assert qualified not in sys.modules
            module = getattr(papersim, name)
            assert module is sys.modules[qualified]
            assert getattr(papersim, export) is getattr(module, export)
        print("ok")
        """
    ).strip() == "ok"

EXPECTED_EXPORT_COUNT = 369
EXPECTED_PROVIDER_COUNT = 76
EXPECTED_SUBMODULE_ATTRIBUTE_COUNT = 134
EXPECTED_ALL_SHA256 = (
    "14358c8d80dc0802ce749d083686febc2fe4f1c9c5e74f9b4ba7e3e747e99c6c"
)
EXPECTED_MAPPING_SHA256 = (
    "9dc40e96530de556e2677f6cb2ad6dad62cfd25bb4a437c326f95df2f8cf4cc7"
)
EXPECTED_EXPORT_MODULE_SEMANTICS_SHA256 = (
    "816ea3b234908d4d2c7e5f8350eb7a1e1f48c2a95bc3b769ba2152bdfb0d5270"
)
EXPECTED_SUBMODULE_ATTRIBUTES_SHA256 = (
    "9baee976926b9a5b89ead57c76a37085a06a704eeb87ab37990b7aee9a95ea17"
)

# Prefixes of the FX-18 retirement-candidate families (B1-B7). A bare root
# import must not load any module from these families.
DELETION_CANDIDATE_FAMILY_PREFIXES = (
    "research_",
    "market_",
    "strategy_",
    "team_",
    "candidate_",
    "probability_",
    "paper_",
)


def _run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess probe failed (exit {result.returncode}):\n{result.stderr}"
    )
    return result.stdout


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_bare_root_import_loads_no_implementation_families() -> None:
    loaded = json.loads(
        _run_python(
            """
            import json
            import polymarket_alpha_lab
            import sys
            names = sorted(
                name
                for name in sys.modules
                if name == "polymarket_alpha_lab"
                or name.startswith("polymarket_alpha_lab.")
            )
            print(json.dumps(names))
            """
        )
    )
    # The lazy root package imports nothing beyond itself; in particular no
    # research_/market_/strategy_/team_/candidate_/probability_/paper_
    # family module from the FX-18 deletion candidates.
    assert loaded == ["polymarket_alpha_lab"]
    children = [name.split(".", 1)[1] for name in loaded[1:]]
    assert not [
        name for name in children if name.startswith(DELETION_CANDIDATE_FAMILY_PREFIXES)
    ]


def test_first_attribute_access_imports_provider_and_caches() -> None:
    assert (
        _run_python(
            """
            import sys
            import polymarket_alpha_lab as pkg

            assert "polymarket_alpha_lab.rejections" not in sys.modules
            first = pkg.RejectedCandidateLog
            assert "polymarket_alpha_lab.rejections" in sys.modules

            second = pkg.RejectedCandidateLog
            assert first is second
            assert pkg.__dict__["RejectedCandidateLog"] is first

            from polymarket_alpha_lab.rejections import RejectedCandidateLog

            assert first is RejectedCandidateLog
            print("ok")
            """
        ).strip()
        == "ok"
    )


def test_export_table_matches_frozen_baseline() -> None:
    exports = lab._EXPORT_PROVIDERS

    assert len(lab.__all__) == EXPECTED_EXPORT_COUNT
    assert len(set(lab.__all__)) == EXPECTED_EXPORT_COUNT
    assert _sha256("\n".join(lab.__all__) + "\n") == EXPECTED_ALL_SHA256

    assert len(exports) == EXPECTED_EXPORT_COUNT
    assert len({module for module, _attribute in exports.values()}) == (
        EXPECTED_PROVIDER_COUNT
    )
    assert set(exports) == set(lab.__all__)
    mapping_blob = json.dumps(
        {name: list(exports[name]) for name in sorted(exports)},
        sort_keys=True,
        indent=0,
    )
    assert _sha256(mapping_blob) == EXPECTED_MAPPING_SHA256

    # Every symbol resolves to exactly the provider attribute the eager
    # implementation used to bind, with identical module semantics.
    semantics: dict[str, str | None] = {}
    for name, (module_name, attribute) in exports.items():
        value = getattr(lab, name)
        assert value is getattr(sys.modules[module_name], attribute)
        semantics[name] = getattr(value, "__module__", None)
    semantics_blob = json.dumps(semantics, sort_keys=True, indent=0)
    assert _sha256(semantics_blob) == EXPECTED_EXPORT_MODULE_SEMANTICS_SHA256


def test_dir_lists_public_surface_without_importing() -> None:
    payload = json.loads(
        _run_python(
            """
            import json
            import polymarket_alpha_lab as pkg
            import sys

            def project_modules():
                return sorted(
                    name
                    for name in sys.modules
                    if name == "polymarket_alpha_lab"
                    or name.startswith("polymarket_alpha_lab.")
                )

            before = project_modules()
            entries = dir(pkg)
            after = project_modules()
            print(
                json.dumps(
                    {
                        "before": before,
                        "grown": [name for name in after if name not in before],
                        "missing_public": [
                            name for name in pkg.__all__ if name not in entries
                        ],
                        "missing_submodules": sorted(
                            name
                            for name in pkg._EAGER_SUBMODULE_ATTRIBUTES
                            if name not in entries
                        ),
                    }
                )
            )
            """
        )
    )
    assert payload["before"] == ["polymarket_alpha_lab"]
    assert payload["grown"] == []
    assert payload["missing_public"] == []
    assert payload["missing_submodules"] == []


def test_submodule_import_attribute_access_and_unknown_names() -> None:
    assert (
        _run_python(
            """
            import polymarket_alpha_lab as pkg

            # Explicit submodule imports keep working and bind the attribute
            # on the package through the import system.
            import polymarket_alpha_lab.journal
            assert pkg.journal.__name__ == "polymarket_alpha_lab.journal"

            # from-import of a submodule resolves without a prior import.
            from polymarket_alpha_lab import strategy_cycle
            assert strategy_cycle.__name__ == "polymarket_alpha_lab.strategy_cycle"

            # Bare attribute access to a former eager side-effect submodule
            # keeps returning the submodule without an explicit import.
            assert pkg.risk.__name__ == "polymarket_alpha_lab.risk"

            # Unknown names raise AttributeError (hasattr stays False).
            try:
                pkg.definitely_not_a_polymarket_alpha_lab_attribute
            except AttributeError:
                pass
            else:
                raise AssertionError("expected AttributeError for unknown name")
            assert not hasattr(
                pkg,
                "DEFAULT_PROPOSAL_EVIDENCE_COMPARISON_HISTORY_BOUNDARY_STATEMENT",
            )
            assert not hasattr(pkg, "CryptoBtcEvidenceEvaluation")
            print("ok")
            """
        ).strip()
        == "ok"
    )


def test_former_eager_submodule_attributes_remain_reachable() -> None:
    submodule_attributes = lab._EAGER_SUBMODULE_ATTRIBUTES

    assert len(submodule_attributes) == EXPECTED_SUBMODULE_ATTRIBUTE_COUNT
    assert _sha256("\n".join(sorted(submodule_attributes)) + "\n") == (
        EXPECTED_SUBMODULE_ATTRIBUTES_SHA256
    )
    assert not (set(lab.__all__) & set(submodule_attributes))
    for name in sorted(submodule_attributes):
        module = getattr(lab, name)
        assert isinstance(module, types.ModuleType)
        assert module.__name__ == f"polymarket_alpha_lab.{name}"


def test_star_import_remains_compatible() -> None:
    assert (
        _run_python(
            """
            namespace = {}
            exec("from polymarket_alpha_lab import *", namespace)
            public = sorted(
                name for name in namespace if not name.startswith("_")
            )
            assert len(public) == 369
            assert namespace["PaperFill"].__module__ == "polymarket_alpha_lab.paper"

            import polymarket_alpha_lab as pkg

            assert namespace["build_research_packet"] is (
                pkg.build_research_packet
            )
            print("ok")
            """
        ).strip()
        == "ok"
    )


def test_papersim_cli_startup_module_count() -> None:
    # Plan section 9.2 measurement: fresh interpreter, import only, count
    # polymarket_alpha_lab modules including the package itself.
    output = _run_python(
        """
        import polymarket_alpha_lab.papersim.cli
        import sys
        names = sorted(
            name
            for name in sys.modules
            if name == "polymarket_alpha_lab"
            or name.startswith("polymarket_alpha_lab.")
        )
        print("\\n".join(names))
        print("PROJECT_MODULE_COUNT", len(names))
        """
    ).strip().splitlines()
    count = int(output[-1].rsplit(None, 1)[-1])
    assert count <= STARTUP_MODULE_LIMIT, (
        f"papersim.cli startup loads {count} project modules "
        f"(limit {STARTUP_MODULE_LIMIT}):\n" + "\n".join(output[:-1])
    )
