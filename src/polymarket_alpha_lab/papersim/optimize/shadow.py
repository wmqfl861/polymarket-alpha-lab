"""Scan-time shadow predictions: fit materials, quote sources, reduction.

MO-7 of the model auto-optimization line (design 04 section 10 step 3;
plan 05 node MO-7; plan review R2 PASS).  A scan computes one
prediction per NON-ACTIVE role spec of every lineage: the shadow quote
source fits each spec once per scan over pack-cited snapshots, predicts
per market, and the reduction below turns the family output into the
pure-gate duck shape ``{"model_median", "qs_variants"}`` -- exactly
what :func:`polymarket_alpha_lab.papersim.gates.evaluate_candidate`
consumes -- so the shadow judgement never touches bankroll, team caps
or any portfolio state (04 section 10 step 3).  Shadow predictions
never produce an entry or a cash event.

Fit-materials contract (plan MO-7 section 2.3): every fit input is
pinned by :class:`ShadowFitMaterials` -- the family id, the spec
digest, the ``content_sha256`` of every cited snapshot the history
derives from, and the history payload's OWN cutoff (``as_of``; never
the writing clock).  The scan records the inventory in its parameters
(``shadow`` section, present only when at least one role fitted) and
``replay.replay_shadow_prediction`` recomputes a stored shadow row
from the candidate bundle + the spec + those pinned snapshots (04
section 11 "recomputable").

Family coverage today (plan MO-7 section 6.3; seven families are
registered, ``optimize.families.iter_family_ids``):

- ``crypto-gbm`` -- COMPLETE: fit from a cited ``kraken_candles``
  snapshot whose payload is ``{"asset", "closes", "last_close_at"}``
  (the MO-7 citation payload; the dataset layer pins provenance for
  this kind only -- MO-10 owns the full contract); the last close is
  the spot; the fixture (asset, barrier, horizon, kind, yes_when)
  comes from the request's crypto probability record, and the quote
  reduces through ``crypto_model.conservative_edges`` exactly as the
  pipeline's crypto direction step does.
- ``club-soccer-frozen`` -- offline-testable: fit is the passthrough
  of a cited ``understat_xg``/``fotmob_xg`` snapshot (plus an optional
  ``league_bridge`` snapshot); quotes reduce through
  ``soccer_model.candidate_for_market`` (the FX-10 generic builder:
  ``total``/``btts``/``match_winner`` kinds, including the bridged
  match_winner path; ``team_total`` needs a ``team_side`` the pack
  does not carry and refuses).
- ``club-soccer-recalib`` -- LIMITED (registered plan residual): a
  scan-time fit needs a per-market knowledge cutoff the once-per-scan
  fit cannot honour, so the snapshot source reports missing fit
  materials; the fixture-stage refusal ``fixture_inputs_missing``
  (pack events carry no season/matchweek) is defined here and
  exercised through :class:`StaticShadowSource`.
- ``intl-elo-poisson`` / ``intl-elo-goal-residuals`` /
  ``nba-pace-efficiency`` -- RESERVED (their scan lineage prefixes
  are not wired in the provider); ``league-bridge`` is exempt
  (``targets=()`` yields no registry lineage).

MO-8 boundary note (plan MO-7 sections 1.2/6.6, aligned with the
provider's class docstring): the merged provider view takes
candidates and guards from EVERY consulted lineage -- including the
tunable production family (crypto-gbm); the frozen soccer family is
structurally candidate-free -- while the active is definitionally the
production baseline, so a promoted lineage active is visible in the
scan parameters but never prices the scan under MO-7.  Consuming
promotions is an explicit MO-8 obligation that must land together
with pack-side consultation of the provider.
"""

from __future__ import annotations

import datetime
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

__all__ = [
    "REFUSAL_FIT_MATERIALS_MISSING",
    "REFUSAL_FIXTURE_INPUTS_MISSING",
    "REFUSAL_MARKET_SELECTOR_MISSING",
    "REFUSAL_MODEL_REFUSAL",
    "ShadowFitMaterials",
    "ShadowFitted",
    "ShadowQuote",
    "ShadowQuoteSource",
    "SnapshotShadowSource",
    "StaticShadowSource",
]

#: Fixed refusal tokens (a shadow row's ``refusal_reason`` values).
REFUSAL_FIT_MATERIALS_MISSING = "fit_materials_missing"
REFUSAL_FIXTURE_INPUTS_MISSING = "fixture_inputs_missing"
REFUSAL_MARKET_SELECTOR_MISSING = "market_selector_missing"
REFUSAL_MODEL_REFUSAL = "model_refusal"

_KIND_TERMINAL = ("crypto_terminal", "terminal")
_KIND_TOUCH = ("crypto_touch", "touch")
_SOCCER_KINDS = ("total", "btts", "match_winner")
_SOCCER_SIDES = {
    "total": ("over", "under"),
    "btts": ("yes", "no"),
    "match_winner": ("home", "draw", "away", "home_no", "draw_no", "away_no"),
}


def _hash64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 \
            or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"papersim_shadow_{name}_invalid")
    return value


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class ShadowFitMaterials:
    """One (family, spec) fit input, fully pinned (plan MO-7 section 2.3).

    ``snapshot_refs`` cites every snapshot the history derives from;
    ``as_of`` is the history payload's own cutoff (the last candle
    time for crypto, the earliest evaluated_at of the lineage's
    records for the provenance-only frozen fit) -- never the writing
    clock.  ``history`` is the family-specific frozen payload consumed
    at fit and quote time (see the module table).
    """

    family_id: str
    spec_sha256: str
    snapshot_refs: tuple[str, ...]
    as_of: datetime.datetime
    history: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.family_id) is not str or not self.family_id.strip():
            raise ValueError("papersim_shadow_family_invalid")
        _hash64(self.spec_sha256, "spec")
        if not isinstance(self.snapshot_refs, tuple) or not self.snapshot_refs:
            raise ValueError("papersim_shadow_refs_invalid")
        for reference in self.snapshot_refs:
            _hash64(reference, "ref")
        if not isinstance(self.as_of, datetime.datetime) or self.as_of.tzinfo is None:
            raise ValueError("papersim_shadow_as_of_invalid")
        if not isinstance(self.history, Mapping):
            raise ValueError("papersim_shadow_history_invalid")

    def params_entry(self) -> dict:
        """The canonical, time-free parameters view of one fitted role."""
        return {
            "family_id": self.family_id,
            "snapshot_refs": list(self.snapshot_refs),
            "as_of": self.as_of.astimezone(datetime.UTC).isoformat(),
        }


@dataclass(frozen=True)
class ShadowFitted:
    """One fitted role: its pinned materials plus the opaque family state."""

    materials: ShadowFitMaterials
    state: object


@dataclass(frozen=True)
class ShadowQuote:
    """One market-level shadow prediction, in the pure-gate duck shape.

    Either a full quote (finite ``model_median`` plus the non-empty
    ``qs_variants`` tuple) or an explicit ``refusal_reason`` -- never
    both, never neither (the same exclusivity the shadow-prediction
    row enforces, ``papersim_shadow_refusal_conflict``).
    """

    model_median: float | None
    qs_variants: tuple[float, ...] | None
    refusal_reason: str | None

    def __post_init__(self) -> None:
        if self.refusal_reason is not None:
            if self.model_median is not None or self.qs_variants is not None:
                raise ValueError("papersim_shadow_refusal_conflict")
            if type(self.refusal_reason) is not str or not self.refusal_reason.strip():
                raise ValueError("papersim_shadow_refusal_invalid")
            return
        median = _finite_float(self.model_median)
        if median is None:
            raise ValueError("papersim_shadow_median_invalid")
        if not isinstance(self.qs_variants, tuple) or not self.qs_variants:
            raise ValueError("papersim_shadow_variants_invalid")
        object.__setattr__(self, "model_median", median)
        object.__setattr__(
            self, "qs_variants",
            tuple(_finite_float(value) for value in self.qs_variants))
        if any(value is None for value in self.qs_variants):
            raise ValueError("papersim_shadow_variants_invalid")


@runtime_checkable
class ShadowQuoteSource(Protocol):
    """The scan-side shadow seam (plan MO-7 section 2.3).

    ``fitted_for`` resolves and fits one (lineage, spec) ONCE per scan
    (the scan caches by spec digest; roles are frozen for the whole
    run) and returns ``None`` when the pack cites no usable fit
    materials -- the scan then records ``fit_materials_missing`` per
    market.  ``quote_for`` predicts one market from the fitted state.
    Both are pure over their explicit inputs; the production reader
    wraps ``papersim.snapshots.read_snapshot`` over the scan
    connection.
    """

    def fitted_for(self, lineage: str, spec: ModelSpec,
                   records: Sequence[Any]) -> ShadowFitted | None: ...

    def quote_for(self, fitted: ShadowFitted, record: Any,
                  request: Any) -> ShadowQuote: ...


class StaticShadowSource:
    """Caller-injected source (tests, transition).

    ``entries`` maps ``spec_sha256 -> (materials, state, quote_fn)``
    where ``quote_fn(state, record, request)`` returns a
    :class:`ShadowQuote`.  The pinning contract is the same: the
    materials' snapshot refs and ``as_of`` are what the scan records
    and what replay recomputes from.
    """

    def __init__(self, entries: Mapping[str, tuple[ShadowFitMaterials, object,
                                                    Callable[..., ShadowQuote]]]) -> None:
        self._entries = dict(entries)

    def fitted_for(self, lineage: str, spec: ModelSpec,
                   records: Sequence[Any]) -> ShadowFitted | None:
        entry = self._entries.get(spec.spec_sha256())
        if entry is None:
            return None
        materials, state, _quote = entry
        return ShadowFitted(materials=materials, state=state)

    def quote_for(self, fitted: ShadowFitted, record: Any, request: Any) -> ShadowQuote:
        entry = self._entries.get(fitted.materials.spec_sha256)
        if entry is None:
            return ShadowQuote(None, None, REFUSAL_FIT_MATERIALS_MISSING)
        _materials, _state, quote = entry
        return quote(fitted.state, record, request)


class SnapshotShadowSource:
    """Pack-citation-driven production source (plan MO-7 section 2.3).

    ``snapshot_reader(sha)`` returns the snapshots-row mapping
    (``content`` bytes + ``kind`` text) or ``None`` when the digest is
    unknown -- production wraps
    :func:`polymarket_alpha_lab.papersim.snapshots.read_snapshot` over
    the scan connection.  Fit inputs are read from the records' cited
    snapshots by kind, in pack order, first parseable payload wins; a
    family whose needed kind is not cited (or whose fit refuses the
    payload) yields ``None`` from :meth:`fitted_for` and the scan
    records ``fit_materials_missing``.
    """

    def __init__(self, snapshot_reader: Callable[[str], Mapping[str, Any] | None]) -> None:
        self._read = snapshot_reader
        self._fitted: dict[str, ShadowFitted] = {}

    def fitted_for(self, lineage: str, spec: ModelSpec,
                   records: Sequence[Any]) -> ShadowFitted | None:
        key = spec.spec_sha256()
        if key in self._fitted:
            return self._fitted[key]
        fitted = self._fit(spec, records)
        if fitted is not None:
            self._fitted[key] = fitted
        return fitted

    def quote_for(self, fitted: ShadowFitted, record: Any, request: Any) -> ShadowQuote:
        family = fitted.materials.family_id
        if family == "crypto-gbm":
            return self._quote_crypto(fitted, request)
        if family == "club-soccer-frozen":
            return self._quote_soccer_frozen(fitted, record, request)
        return ShadowQuote(None, None, REFUSAL_MARKET_SELECTOR_MISSING)

    # -- fit helpers -------------------------------------------------------

    def _fit(self, spec: ModelSpec, records: Sequence[Any]) -> ShadowFitted | None:
        if spec.family == "crypto-gbm":
            return self._fit_crypto(spec, records)
        if spec.family == "club-soccer-frozen":
            return self._fit_soccer_frozen(spec, records)
        # club-soccer-recalib: a scan-time fit needs a per-market knowledge
        # cutoff the once-per-scan fit cannot honour (registered plan
        # residual); intl/nba are reserved.  Both report missing fit
        # materials rather than fitting on an unsound boundary.
        return None

    def _cited(self, records: Sequence[Any], kinds: tuple[str, ...]
               ) -> list[tuple[str, bytes]]:
        found: dict[str, bytes] = {}
        order: list[str] = []
        for record in records:
            for reference in tuple(getattr(record, "snapshot_refs", ()) or ()):
                if reference in found:
                    continue
                row = self._read(reference)
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("kind")) not in kinds:
                    continue
                content = row.get("content")
                if not isinstance(content, (bytes, bytearray, memoryview)):
                    continue
                found[reference] = bytes(content)
                order.append(reference)
        return [(reference, found[reference]) for reference in order]

    def _fit_crypto(self, spec: ModelSpec, records: Sequence[Any]) -> ShadowFitted | None:
        cited = self._cited(records, ("kraken_candles",))
        asset = closes = stamp = None
        chosen = None
        for reference, content in cited:
            try:
                payload = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            candidate_asset = payload.get("asset")
            candidate_closes = payload.get("closes")
            candidate_stamp = payload.get("last_close_at")
            if type(candidate_asset) is not str or not candidate_asset:
                continue
            if not isinstance(candidate_closes, list) or not candidate_closes:
                continue
            values = [_finite_float(value) for value in candidate_closes]
            if any(value is None or value <= 0 for value in values):
                continue
            if type(candidate_stamp) is not str:
                continue
            try:
                boundary = datetime.datetime.fromisoformat(candidate_stamp)
            except ValueError:
                continue
            if boundary.tzinfo is None:
                continue
            asset, closes, stamp = candidate_asset, tuple(values), boundary
            chosen = reference
            break
        if chosen is None or asset is None or closes is None or stamp is None:
            return None
        from polymarket_alpha_lab.papersim.optimize.families.crypto import (
            CRYPTO_GBM,
            CryptoFamilyHistory,
        )
        try:
            state = CRYPTO_GBM.fit(CryptoFamilyHistory(closes={asset: closes}),
                                   spec, stamp)
        except (TypeError, ValueError):
            return None
        materials = ShadowFitMaterials(
            family_id="crypto-gbm", spec_sha256=spec.spec_sha256(),
            snapshot_refs=(chosen,), as_of=stamp,
            history={"asset": asset, "closes": closes, "spot": closes[-1]})
        return ShadowFitted(materials=materials, state=state)

    def _fit_soccer_frozen(self, spec: ModelSpec,
                           records: Sequence[Any]) -> ShadowFitted | None:
        cited = self._cited(records, ("understat_xg", "fotmob_xg"))
        payload = None
        chosen = None
        for reference, content in cited:
            try:
                candidate = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(candidate, Mapping):
                payload = dict(candidate)
                chosen = reference
                break
        if payload is None or chosen is None:
            return None
        bridge = None
        bridges = self._cited(records, ("league_bridge",))
        if bridges:
            try:
                bridge_payload = json.loads(bridges[0][1].decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                bridge_payload = None
            if isinstance(bridge_payload, Mapping):
                table: dict[str, float] = {}
                sound = True
                for key, value in bridge_payload.items():
                    number = _finite_float(value)
                    if number is None or not isinstance(key, str):
                        sound = False
                        break
                    table[key] = number
                bridge = table if sound else None
        boundary = min(record.evaluated_at for record in records)
        from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import (
            SOCCER_FROZEN,
            SoccerFrozenHistory,
        )
        try:
            state = SOCCER_FROZEN.fit(SoccerFrozenHistory(xg_snapshot=payload,
                                                          bridge=bridge),
                                      spec, boundary)
        except (TypeError, ValueError):
            return None
        materials = ShadowFitMaterials(
            family_id="club-soccer-frozen", spec_sha256=spec.spec_sha256(),
            snapshot_refs=(chosen,) if bridge is None else (chosen, bridges[0][0]),
            as_of=boundary,
            history={"xg_snapshot": payload, "bridge": bridge})
        return ShadowFitted(materials=materials, state=state)

    # -- quote helpers -----------------------------------------------------

    def _quote_crypto(self, fitted: ShadowFitted, request: Any) -> ShadowQuote:
        from polymarket_alpha_lab.papersim import crypto_model
        from polymarket_alpha_lab.papersim.optimize.families.crypto import (
            CRYPTO_GBM,
            CryptoFamilyFixture,
        )
        probability = getattr(request, "probability", None)
        kind_text = str(getattr(request, "market_kind", "") or "").lower()
        if kind_text in _KIND_TERMINAL:
            kind = "terminal"
        elif kind_text in _KIND_TOUCH:
            kind = "touch"
        else:
            return ShadowQuote(None, None, REFUSAL_MARKET_SELECTOR_MISSING)
        yes_ask = _finite_float(getattr(request, "yes_ask", None))
        no_ask = _finite_float(getattr(request, "no_ask", None))
        if (probability is None or yes_ask is None or no_ask is None
                or not 0.0 < yes_ask < 1.0 or not 0.0 < no_ask < 1.0):
            return ShadowQuote(None, None, REFUSAL_MARKET_SELECTOR_MISSING)
        yes_when = getattr(request, "yes_when", None) if kind == "terminal" else None
        fixture = CryptoFamilyFixture(
            asset=str(fitted.materials.history["asset"]),
            spot=float(fitted.materials.history["spot"]),
            barrier=float(getattr(probability, "barrier")),
            horizon_hours=float(getattr(probability, "horizon_hours")),
            kind=kind, yes_when=yes_when)
        predicted = CRYPTO_GBM.predict(fitted.state, fixture)
        if predicted is None:
            return ShadowQuote(None, None, REFUSAL_MODEL_REFUSAL)
        edges = crypto_model.conservative_edges(predicted, yes_ask=yes_ask,
                                                no_ask=no_ask)
        buy_side = getattr(request, "buy_side", None)
        if not buy_side:
            buy_side = "yes" if str(getattr(request, "side", "")).lower() == "yes" else "no"
        pair = (float(predicted.convention_a), float(predicted.convention_b))
        if buy_side == "yes":
            return ShadowQuote(float(edges.fair_yes), pair, None)
        return ShadowQuote(float(edges.fair_no), (1.0 - pair[0], 1.0 - pair[1]), None)

    def _quote_soccer_frozen(self, fitted: ShadowFitted, record: Any,
                             request: Any) -> ShadowQuote:
        from polymarket_alpha_lab.papersim import gates, soccer_model
        events = getattr(record, "events", None)
        home = getattr(events, "home", None)
        away = getattr(events, "away", None)
        league = getattr(events, "league", None)
        if not home or not away:
            return ShadowQuote(None, None, REFUSAL_FIXTURE_INPUTS_MISSING)
        home_league = league or getattr(request, "home_league", None)
        away_league = league or getattr(request, "away_league", None)
        if not home_league or not away_league:
            return ShadowQuote(None, None, REFUSAL_FIXTURE_INPUTS_MISSING)
        kind = str(getattr(request, "market_kind", "") or "").lower()
        side = str(getattr(request, "side", "") or "").lower()
        if kind not in _SOCCER_KINDS or side not in _SOCCER_SIDES.get(kind, ()):
            return ShadowQuote(None, None, REFUSAL_MARKET_SELECTOR_MISSING)
        walk = gates.walk_book(list(getattr(request, "book", ()) or []),
                               side=gates.SIDE_ASK, notional=250.0)
        best = _finite_float(walk.best)
        if best is None:
            return ShadowQuote(None, None, REFUSAL_MARKET_SELECTOR_MISSING)
        envelope = soccer_model.candidate_for_market(
            fitted.materials.history["xg_snapshot"],
            home, home_league, away, away_league,
            getattr(request, "question", ""),
            getattr(request, "market_id", ""),
            best,
            kind=kind, side=side, line=getattr(request, "line", None),
            season=None, bridge=fitted.materials.history.get("bridge"))
        if envelope is None:
            return ShadowQuote(None, None, REFUSAL_MODEL_REFUSAL)
        candidate = envelope.candidate
        return ShadowQuote(float(candidate.model_median),
                           tuple(float(value) for value in candidate.qs_variants),
                           None)
