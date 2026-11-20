"""B1 optimize provider proofs (offline; plan 05 section 2 / design 04 section 10).

The provider interface is the frozen seam ``run-scan`` takes its model
specifications through.  With the registry empty the frozen-baseline
provider must satisfy the registered bit-identity anchor: every
lineage's ACTIVE spec is the family baseline and its ``model_version()``
IS the pipeline constant, so the scan output equals the unwired stack
byte for byte.  The fallback reason is always recorded (04 section 10
step 1) and an unknown lineage is a configuration error, never a
silent default.
"""

from __future__ import annotations

import pytest

from polymarket_alpha_lab.papersim import pipeline
from polymarket_alpha_lab.papersim.optimize.provider import (
    CRYPTO_FROZEN_FAMILY_ID,
    CRYPTO_FROZEN_PROCEDURE_VERSION,
    FALLBACK_REGISTRY_EMPTY,
    FrozenBaselineProvider,
    LineageSpecs,
    SpecProvider,
    crypto_frozen_baseline,
)


@pytest.mark.parametrize("lineage", [
    "club-soccer",
    "club-soccer/over_under",
    "club-soccer/btts/epl",
])
def test_soccer_lineages_carry_the_frozen_pipeline_version(lineage):
    roles = FrozenBaselineProvider().specs_for_lineage(lineage)
    assert isinstance(roles, LineageSpecs)
    assert roles.active.model_version() == pipeline.MODEL_VERSION_SOCCER
    assert roles.baseline.spec_sha256() == roles.active.spec_sha256()
    assert roles.candidates == () and roles.guard is None
    assert roles.fallback_reason == FALLBACK_REGISTRY_EMPTY


@pytest.mark.parametrize("lineage", ["crypto", "crypto/terminal", "crypto/touch/eth"])
def test_crypto_lineages_carry_the_frozen_pipeline_version(lineage):
    roles = FrozenBaselineProvider().specs_for_lineage(lineage)
    assert roles.active.model_version() == pipeline.MODEL_VERSION_CRYPTO
    assert roles.baseline.spec_sha256() == crypto_frozen_baseline().spec_sha256()
    assert roles.fallback_reason == FALLBACK_REGISTRY_EMPTY


def test_crypto_frozen_baseline_shape():
    # MO-5d: the placeholder (empty params, "crypto-r19") was consumed
    # by the family delegation; the baseline now carries the estimator
    # default params and the family's procedure generation.
    spec = crypto_frozen_baseline()
    assert spec.family == CRYPTO_FROZEN_FAMILY_ID == "crypto-gbm"
    assert spec.procedure_version == CRYPTO_FROZEN_PROCEDURE_VERSION == "crypto-r19e1"
    assert dict(spec.params) == {
        "vol_mode": "window",
        "vol_weights": (0.0, 1.0, 0.0, 0.0),
        "vol_ew_decay": 0.995,
    }
    assert tuple(spec.envelope) == ()
    assert spec.data_contract == "crypto-frozen-v1"
    # The legacy alias rides model_version but stays OUT of the spec
    # identity (optimize.spec.ModelSpec): renaming it cannot re-key a
    # stored spec.
    assert spec.legacy_model_version == pipeline.MODEL_VERSION_CRYPTO
    assert "crypto-r19+fx13" not in spec.canonical_json()


def test_soccer_baseline_is_the_registered_family_baseline():
    from polymarket_alpha_lab.papersim.optimize.families import soccer_frozen

    roles = FrozenBaselineProvider().specs_for_lineage("club-soccer/over_under")
    family_baseline = soccer_frozen.SOCCER_FROZEN.baseline_spec()
    assert roles.baseline.spec_sha256() == family_baseline.spec_sha256()


def test_crypto_baseline_is_the_registered_family_baseline():
    # B1's registered debt is retired: the crypto baseline identity is
    # single-sourced in the family and the provider delegates to it.
    from polymarket_alpha_lab.papersim.optimize.families import crypto as crypto_family

    roles = FrozenBaselineProvider().specs_for_lineage("crypto/touch/eth")
    family_baseline = crypto_family.CRYPTO_GBM.baseline_spec()
    assert roles.baseline.spec_sha256() == family_baseline.spec_sha256()
    assert crypto_frozen_baseline().spec_sha256() == family_baseline.spec_sha256()
    assert CRYPTO_FROZEN_FAMILY_ID == crypto_family.FAMILY_ID
    assert CRYPTO_FROZEN_PROCEDURE_VERSION == crypto_family.PROCEDURE_VERSION


def test_provider_satisfies_the_frozen_protocol():
    assert isinstance(FrozenBaselineProvider(), SpecProvider)


@pytest.mark.parametrize("lineage", ["", "   ", "nba/point-total", "club-cricket/fancy"])
def test_unknown_lineage_is_a_configuration_error(lineage):
    with pytest.raises(ValueError):
        FrozenBaselineProvider().specs_for_lineage(lineage)


def test_spec_digests_are_stable_across_calls():
    provider = FrozenBaselineProvider()
    first = provider.specs_for_lineage("crypto/terminal")
    second = provider.specs_for_lineage("crypto/terminal")
    assert first.active.spec_sha256() == second.active.spec_sha256()
    assert first == second


# ---------------------------------------------------------------------------
# MO-7: the registry-backed provider (plan 05 node MO-7; plan review R2
# sections 1.1-1.3 -- composition, the complete lineage mapping and the
# fallback reasons; review P1-1's match_winner rule and P3-5's guard /
# match_winner matrix assertions live here).
# ---------------------------------------------------------------------------

import datetime  # noqa: E402
from hashlib import sha256  # noqa: E402

from polymarket_alpha_lab.papersim.optimize.provider import (  # noqa: E402
    FALLBACK_FAMILY_VERSION_UNKNOWN,
    FALLBACK_LINEAGE_NOT_INITIALIZED,
    FALLBACK_REGISTRY_READ_FAILED,
    FALLBACK_SPEC_CHECKSUM_MISMATCH,
    RegistryProvider,
    family_ids_for_prefix,
)
from polymarket_alpha_lab.papersim.optimize.registry import (  # noqa: E402
    MAX_CANDIDATES,
    LineageRoles,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec  # noqa: E402

AT = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _spec(family: str, procedure: str, *, params=None, data_contract="dc-mo7"):
    return ModelSpec(family=family, procedure_version=procedure,
                     params=dict(sorted((params or {}).items())),
                     envelope=(), data_contract=data_contract)


def _roles(lineage, baseline, *, candidates=(), guard=None, active=None):
    return LineageRoles(lineage=lineage, baseline=baseline,
                        active=baseline if active is None else active,
                        candidates=tuple(candidates), guard=guard)


def _soccer_frozen_baseline():
    from polymarket_alpha_lab.papersim.optimize.families import soccer_frozen
    return soccer_frozen.SOCCER_FROZEN.baseline_spec()


def _recalib_baseline():
    from polymarket_alpha_lab.papersim.optimize.families import soccer_recalib
    return soccer_recalib.SOCCER_RECALIB.baseline_spec()


def _crypto_baseline():
    from polymarket_alpha_lab.papersim.optimize.families import crypto
    return crypto.CRYPTO_GBM.baseline_spec()


def test_family_ids_for_prefix_is_the_deterministic_family_set():
    assert family_ids_for_prefix("club-soccer") == (
        "club-soccer-frozen", "club-soccer-recalib")
    assert family_ids_for_prefix("crypto") == ("crypto-gbm",)


def test_empty_registry_delegates_wholesale_to_the_b1_provider():
    provider = RegistryProvider(roles={}, specs={})
    frozen = FrozenBaselineProvider()
    for lineage in ("club-soccer", "club-soccer/over_under", "crypto",
                    "crypto/terminal", "crypto/touch/eth"):
        assert provider.specs_for_lineage(lineage) == frozen.specs_for_lineage(lineage)


def test_unreadable_registry_falls_back_with_the_read_failure_reason():
    provider = RegistryProvider.from_connection(None)
    roles = provider.specs_for_lineage("crypto/terminal")
    assert roles.fallback_reason == FALLBACK_REGISTRY_READ_FAILED
    assert roles.active.model_version() == pipeline.MODEL_VERSION_CRYPTO
    assert roles.candidates == () and roles.guard is None


def test_bare_prefix_and_range_suffix_stay_accepted_with_lineage_fallback():
    crypto_baseline = _crypto_baseline()
    roles = {"crypto-gbm/terminal": _roles("crypto-gbm/terminal",
                                           crypto_baseline.spec_sha256())}
    provider = RegistryProvider(roles=roles, specs={
        crypto_baseline.spec_sha256(): crypto_baseline})
    for lineage in ("club-soccer", "club-soccer/btts/epl", "crypto"):
        resolved = provider.specs_for_lineage(lineage)
        assert resolved.fallback_reason == FALLBACK_LINEAGE_NOT_INITIALIZED
        assert resolved.candidates == ()
    with pytest.raises(ValueError):
        provider.specs_for_lineage("nba/point-total")


def test_match_winner_resolves_through_the_estimator_lineage():
    frozen = _soccer_frozen_baseline()
    recalib = _recalib_baseline()
    candidate = _spec("club-soccer-recalib", "p4r1",
                      params={"half_life_days": 90.0})
    lineage = "club-soccer-recalib/match_winner"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, recalib.spec_sha256(),
                               candidates=(candidate.spec_sha256(),))},
        specs={recalib.spec_sha256(): recalib,
               candidate.spec_sha256(): candidate})
    resolved = provider.specs_for_lineage("club-soccer/match_winner")
    assert resolved.fallback_reason is None
    assert resolved.active.spec_sha256() == frozen.spec_sha256()
    assert resolved.active.model_version() == pipeline.MODEL_VERSION_SOCCER
    assert resolved.baseline.spec_sha256() == frozen.spec_sha256()
    assert resolved.candidates == (candidate,)
    assert resolved.registry == ((lineage, recalib.spec_sha256(),
                                  (candidate.spec_sha256(),)),)


def test_over_under_merges_estimator_candidates_sorted_without_arbitration():
    frozen = _soccer_frozen_baseline()
    recalib = _recalib_baseline()
    late = _spec("club-soccer-recalib", "p4r1", params={"half_life_days": 240.0})
    early = _spec("club-soccer-recalib", "p4r1", params={"half_life_days": 60.0})
    assert late.spec_sha256() < early.spec_sha256()  # the pinned sort key
    provider = RegistryProvider(
        roles={"club-soccer-frozen/over_under": _roles(
                   "club-soccer-frozen/over_under", frozen.spec_sha256()),
               "club-soccer-recalib/over_under": _roles(
                   "club-soccer-recalib/over_under", recalib.spec_sha256(),
                   candidates=(early.spec_sha256(), late.spec_sha256()))},
        specs={frozen.spec_sha256(): frozen, recalib.spec_sha256(): recalib,
               early.spec_sha256(): early, late.spec_sha256(): late})
    resolved = provider.specs_for_lineage("club-soccer/over_under")
    assert resolved.fallback_reason is None
    assert resolved.active.spec_sha256() == frozen.spec_sha256()
    assert resolved.candidates == (late, early)
    assert len(resolved.candidates) <= MAX_CANDIDATES
    assert [entry[0] for entry in resolved.registry] == [
        "club-soccer-frozen/over_under", "club-soccer-recalib/over_under"]


def test_guard_assembly_comes_from_the_estimator_lineage():
    frozen = _soccer_frozen_baseline()
    recalib = _recalib_baseline()
    guard = _spec("club-soccer-recalib", "p4r1", params={"half_life_days": 180.0})
    lineage = "club-soccer-recalib/over_under"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, recalib.spec_sha256(),
                               guard=guard.spec_sha256())},
        specs={recalib.spec_sha256(): recalib, guard.spec_sha256(): guard})
    resolved = provider.specs_for_lineage("club-soccer/over_under")
    assert resolved.guard == guard
    # a guard that IS the lineage baseline (the rollback-to-baseline
    # shape) resolves to the baseline spec; the scan-side dedup keeps
    # the row set collision-free.
    provider_rolled = RegistryProvider(
        roles={lineage: _roles(lineage, recalib.spec_sha256(),
                               guard=recalib.spec_sha256())},
        specs={recalib.spec_sha256(): recalib})
    rolled = provider_rolled.specs_for_lineage("club-soccer/over_under")
    assert rolled.guard == recalib


def test_naming_identity_defense_falls_the_lineage_back():
    frozen = _soccer_frozen_baseline()
    foreign = _spec("club-soccer-recalib", "p4r1", params={"half_life_days": 90.0})
    lineage = "club-soccer-frozen/over_under"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, frozen.spec_sha256(),
                               candidates=(foreign.spec_sha256(),))},
        specs={frozen.spec_sha256(): frozen, foreign.spec_sha256(): foreign})
    resolved = provider.specs_for_lineage("club-soccer/over_under")
    assert resolved.fallback_reason == FALLBACK_FAMILY_VERSION_UNKNOWN
    assert resolved.candidates == ()
    # a production lineage whose active left its baseline is the same
    # corruption class (no promotion can exist there under MO-7).
    drifted = RegistryProvider(
        roles={lineage: _roles(lineage, frozen.spec_sha256(),
                               active=foreign.spec_sha256())},
        specs={frozen.spec_sha256(): frozen, foreign.spec_sha256(): foreign})
    assert drifted.specs_for_lineage("club-soccer/over_under").fallback_reason \
        == FALLBACK_FAMILY_VERSION_UNKNOWN


def test_spec_failure_falls_the_lineage_back():
    crypto_baseline = _crypto_baseline()
    candidate = _spec("crypto-gbm", "crypto-r19e1", params={"vol_ew_decay": 0.99})
    lineage = "crypto-gbm/terminal"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, crypto_baseline.spec_sha256(),
                               candidates=(candidate.spec_sha256(),))},
        specs={crypto_baseline.spec_sha256(): crypto_baseline},
        spec_failures={candidate.spec_sha256(): FALLBACK_SPEC_CHECKSUM_MISMATCH})
    resolved = provider.specs_for_lineage("crypto/terminal")
    assert resolved.fallback_reason == FALLBACK_SPEC_CHECKSUM_MISMATCH
    assert resolved.candidates == ()
    # a referenced digest that never loaded is the same condition
    unloaded = RegistryProvider(
        roles={lineage: _roles(lineage, crypto_baseline.spec_sha256(),
                               candidates=(candidate.spec_sha256(),))},
        specs={crypto_baseline.spec_sha256(): crypto_baseline})
    assert unloaded.specs_for_lineage("crypto/terminal").fallback_reason \
        == FALLBACK_SPEC_CHECKSUM_MISMATCH


def test_procedure_version_mismatch_falls_the_lineage_back():
    crypto_baseline = _crypto_baseline()
    stale = _spec("crypto-gbm", "crypto-r19")
    lineage = "crypto-gbm/terminal"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, crypto_baseline.spec_sha256(),
                               candidates=(stale.spec_sha256(),))},
        specs={crypto_baseline.spec_sha256(): crypto_baseline,
               stale.spec_sha256(): stale})
    resolved = provider.specs_for_lineage("crypto/terminal")
    assert resolved.fallback_reason == FALLBACK_FAMILY_VERSION_UNKNOWN


def test_production_baseline_drift_falls_the_lineage_back():
    drifted_baseline = _spec("crypto-gbm", "crypto-r19e1",
                             params={"vol_ew_decay": 0.99})
    lineage = "crypto-gbm/terminal"
    provider = RegistryProvider(
        roles={lineage: _roles(lineage, drifted_baseline.spec_sha256())},
        specs={drifted_baseline.spec_sha256(): drifted_baseline})
    resolved = provider.specs_for_lineage("crypto/terminal")
    assert resolved.fallback_reason == FALLBACK_FAMILY_VERSION_UNKNOWN
