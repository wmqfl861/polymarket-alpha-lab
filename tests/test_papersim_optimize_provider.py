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
    spec = crypto_frozen_baseline()
    assert spec.family == CRYPTO_FROZEN_FAMILY_ID == "crypto-gbm"
    assert spec.procedure_version == CRYPTO_FROZEN_PROCEDURE_VERSION
    assert dict(spec.params) == {} and tuple(spec.envelope) == ()
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
