"""Seal originals and coverage for the managed scan preparation path."""

from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re

from polymarket_alpha_lab.papersim import crypto_model, fotmob, pipeline, prepare, prepare_inputs as inputs
from polymarket_alpha_lab.papersim import queue_policy, snapshots, store, store_psycopg as pg, xg
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket, prices, sports, understat
from polymarket_alpha_lab.papersim.d1compare import acquire, journal, operations
from polymarket_alpha_lab.papersim.optimize.provider import scan_prefix_of
from polymarket_alpha_lab.research_crypto_contract_scope import _QUESTION, _fold


def _ref(row: dict) -> dict:
    return {"sha256": row["content_sha256"], "byte_length": row["byte_length"]}


def _registered(conn, raw, kind, source, at, version=1) -> dict:
    row = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(raw, kind, source, at, version))
    if (row["kind"], row["content_sha256"], row["byte_length"]) != (kind, inputs._digest(raw), len(raw)):
        raise prepare.PreparationError("papersim_prepare_snapshot_registration_conflict")
    if kind != acquire._BODY_KIND and row["schema_version"] != version:
        raise prepare.PreparationError("papersim_prepare_snapshot_registration_conflict")
    if row["fetched_at"] > at:
        raise prepare.PreparationError("papersim_prepare_snapshot_registration_conflict")
    return row


def _interval_reference(conn, instance_id, team, start, end):
    # The interval is a registered, immutable policy source. Choose the sole
    # applicable scope with a policy, and refuse overlapping active policies.
    with conn.transaction():
        rows = conn.execute(
            "SELECT output_evidence FROM papersim.command_run_events "
            "WHERE command = 'd1-register-interval' AND phase = 'SUCCEEDED' "
            "AND occurred_at < %s ORDER BY run_event_id", (start,),
        ).fetchall()
    applicable = []
    for (references,) in rows:
        interval = operations._read_document(
            conn, references, kind=operations._INTERVAL_KIND, source="d1:operation:interval:v1",
        )
        if (interval.get("instance_id") == instance_id and interval.get("team") == team
                and interval.get("book_age_policy") is not None
                and inputs._utc(interval["starts_at"]) <= start < end <= inputs._utc(interval["ends_at"])):
            digest = references[0].removeprefix("snapshot_sha256:")
            applicable.append(_ref(snapshots.verify_snapshot(conn, digest)))
    if len(applicable) > 1:
        raise prepare.PreparationError("d1_capture_book_age_policy_ambiguous")
    reference = applicable[0] if applicable else None
    inputs._book_age_policy(conn, reference, start, end, instance_id=instance_id, team=team)
    return reference


def _page_rows(manifest, entries):
    bodies = {entry.content_sha256: entry.content for entry in entries}
    pages = {}
    for request in manifest["requests"]:
        digest = request["content_sha256"]
        if request["page"] is not None:
            pages[request["sequence"]] = journal.decode_public_json(bodies[digest])
    for market in manifest["markets"]:
        sightings = sorted(market["observations"], key=lambda item: (item["sequence"], item["path"]))
        for sighting in sightings:
            page = pages.get(sighting["sequence"])
            match = inputs._PATH.fullmatch(sighting["path"])
            if page is None or match is None:
                raise prepare.PreparationError("papersim_prepare_gamma_location_invalid")
            event = page[int(match[1])]
            body = event["markets"][int(match[2])]
            tag = manifest["requests"][sighting["sequence"] - 1]["request_meta"]["query"]["tag_slug"]
            yield market, event, body, tag, sighting


def _queue_kind(queue, prefix):
    supported = inputs._CRYPTO_TYPES if prefix == "crypto" else inputs._SOCCER_TYPES
    _, lineage_kind = scan_prefix_of(queue.lineage)
    return next((kind for kind, alias in supported.items()
                 if queue.market_type in (kind, alias, "other")
                 and (lineage_kind or queue.market_type) in (kind, alias)), None)


def _soccer_scope(tag, event):
    tags = {tag} | {item.get("slug") for item in event.get("tags") or [] if isinstance(item, dict)}
    if tags & inputs._SOCCER_CROSS_TAGS:
        return None
    eligible = [league for league, names in inputs._SOCCER_TAGS.items() if tags & names]
    return eligible[0] if len(eligible) == 1 else None


def _soccer_proposition(event, question, kind):
    title = event.get("title", "")
    fixture = re.split(r" (?:versus|vs\.?)[ ]", title, maxsplit=1)
    home, away = fixture if len(fixture) == 2 else (None, None)
    if kind == "btts" and home and question == f"Will both teams score in {home} versus {away}?":
        return "yes", "no", None
    if kind == "match_winner" and home:
        if question == f"Will {home} versus {away} end in a draw?":
            return "draw", "draw_no", None
        for team, opponent, name in ((home, away, "home"), (away, home, "away")):
            if question == f"Will {team} win against {opponent}?":
                return name, f"{name}_no", None
    if kind in ("total", "team_total", "spread") and home:
        subjects = (f"{home} versus {away}",) if kind == "total" else (home, away)
        for subject in subjects:
            term = (f"Will {subject} cover " if kind == "spread" else
                    f"Will {subject} have more than ")
            end = r"\?" if kind == "spread" else r" goals\?"
            match = re.fullmatch(re.escape(term) + r"([+-]?" + inputs._NUMBER + ")" + end, question)
            if match:
                return ("cover", "nocover", match[1]) if kind == "spread" else ("over", "under", match[1])
    return None


def _crypto_proposition(body, kind):
    question = _fold(body.get("question", ""))
    terminal = _QUESTION.fullmatch(question)
    if terminal:
        return ("yes", "no", terminal["price"]) if kind == pipeline.KIND_CRYPTO_TERMINAL else None
    if kind == pipeline.KIND_CRYPTO_TOUCH and re.search(r"\b(?:hit|touch|reach|dip)\b", question):
        return "yes", "no", None
    return None


def _targets(config, manifest, entries):
    targets, bound, observations = {}, set(), []
    queues = [queue for queue in config.queues if not queue.pack]
    for market, event, body, tag, sighting in _page_rows(manifest, entries):
        identity = market["condition_id"]
        if market["binding_status"] != "explicit_yes_no":
            observations.append({**sighting, "condition_id": identity, "reason_code": market["binding_status"]})
            continue
        event_id = str(event["id"]) if event.get("id") is not None else None
        try:
            end = prepare._utc(body.get("endDate"))
        except prepare.PreparationError:
            observations.append({**sighting, "condition_id": identity,
                                 "reason_code": "papersim_prepare_target_clock_unavailable"})
            continue
        category = str(body.get("category", "")).lower()
        matched = False
        for queue in queues:
            prefix, _ = scan_prefix_of(queue.lineage)
            kind = _queue_kind(queue, prefix)
            if kind is None or event_id is None or end is None:
                continue
            if prefix == "crypto" and (category == "crypto" or tag in {"crypto", "bitcoin", "ethereum"}):
                proposition = _crypto_proposition(body, kind)
                boundary = end
            elif prefix == "club-soccer" and (category == "sports" or _soccer_scope(tag, event)):
                proposition = _soccer_proposition(event, body.get("question", ""), kind)
                try:
                    boundary = prepare._utc(body.get("gameStartTime"))
                except prepare.PreparationError:
                    boundary = None
                scope = queue_policy.SCOPE_CROSS_LEAGUE if tag in inputs._SOCCER_CROSS_TAGS else queue_policy.SCOPE_DOMESTIC
                if scope != queue.league_scope:
                    continue
            else:
                continue
            if proposition is None or boundary is None:
                continue
            matched = True
            yes, no, line = proposition
            for side, model_side in (("YES", yes), ("NO", no)):
                key = identity, kind, side
                target = {
                    "queue": queue.queue, "condition_id": identity, "market_id": market["market_id"],
                    "slug": market["slug"], "gamma_event_id": event_id, "tag_slug": tag,
                    "terms_sha256": journal._gamma_terms_sha256(body), "market_kind": kind, "buy_side": side,
                    "model_side": model_side, "event_start_at": boundary.isoformat(),
                    "expires_at": end.isoformat(), "line": line, "sources": None, "news": None,
                }
                selected = targets.setdefault(key, (target, event, body))
                if selected[0]["queue"] != queue.queue:
                    raise prepare.PreparationError("papersim_prepare_target_queue_ambiguous")
                bound.add(identity)
        if not matched:
            observations.append({"condition_id": identity, "sequence": sighting["sequence"],
                                 "path": sighting["path"], "reason_code": "papersim_prepare_queue_binding_missing"})
    return targets, [item for item in observations if item["condition_id"] not in bound]


class _Capture:
    def __init__(self, conn, config, config_bytes, scope, instance_id, operation_key, interval):
        self.conn = conn
        self.config = config
        self.config_bytes = config_bytes
        self.scope = scope
        self.instance_id = instance_id
        self.operation_key = operation_key
        self.interval = interval
        self.responses = []
        self.fetches = []
        self.target_sources = {}
        self.summary = None
        self.target = None

    def register_market(self, attempt):
        row = _registered(self.conn, attempt.body, acquire._BODY_KIND, attempt.url, attempt.read_at, 2)
        # D1 v1 and repeated requests may already own the digest.
        if row["schema_version"] not in acquire._BATCH_SOURCES:
            raise prepare.PreparationError("papersim_prepare_snapshot_registration_conflict")

    def register_model(self, evidence):
        kind, source = evidence.source_kind, evidence.source_reference
        inputs._source_request(kind, source)
        row = _registered(self.conn, evidence.raw_bytes, inputs.MODEL_RESPONSE_KIND, source, evidence.fetched_at)
        self.responses.append({
            "sequence": len(self.responses) + 1, "source_kind": kind, "source_ref": source,
            "status": "received", "received_at": evidence.fetched_at, "body": _ref(row),
            "source_observed_at": evidence.source_observed_at,
            "source_time_status": "upstream" if evidence.source_observed_at else "receipt_only",
            "target": self.target, "budget": "charged", "reason_code": None,
        })

    def _fetch(self, method, *args, **kwargs):
        first = len(self.responses)
        result = method(*args, **kwargs, budget=self.budget, allow_public_fetch=True,
                        register_response=self.register_model)
        sequences = list(range(first + 1, len(self.responses) + 1))
        self.fetches.append({"target": self.target, "method": method.__name__, "requests": sequences,
                             "status": result.status.value, "reason_code": result.reason_code,
                             "requests_charged": self.budget.requests_used,
                             "response_bytes_charged": self.budget.response_bytes_used})
        return result, sequences

    def _crypto_sources(self, body):
        rule = inputs._TERMINAL_RULE.fullmatch(_fold(body.get("description") or ""))
        question = _QUESTION.fullmatch(_fold(body.get("question", "")))
        if rule is None or question is None:
            return None
        asset = "BTC" if question["asset"] in ("bitcoin", "btc") else "ETH"
        provider = rule["venue"]
        self.target = {"asset": asset, "provider": provider}
        now = acquire._now_utc()
        seconds = prices._epoch_seconds(now)
        end = datetime.fromtimestamp(seconds - seconds % prices.BAR_SECONDS, UTC)
        # Capture the requested registered model history in the one shared
        # budget. A short venue history is explicitly incomplete.
        days = max(crypto_model.VOL_WINDOW_BARS.values()) * prices.BAR_SECONDS
        if provider == "kraken":
            days = min(days, prices.KRAKEN_USABLE_ROWS * prices.KRAKEN_HOUR_SECONDS)
        start = end - timedelta(seconds=days - days % prices.BAR_SECONDS)
        _, candles = self._fetch(prices.fetch_closed_candles, asset, provider, start, end)
        _, spot = self._fetch(prices.fetch_spot, asset, provider)
        return ({"candles": candles, "spot": spot[0], "window_start": start, "window_end": end}
                if spot else None)

    def _soccer_sources(self, event, target, fixture_sources):
        league = _soccer_scope(target["tag_slug"], event)
        if league is None:
            return None
        kickoff = inputs._utc(target["event_start_at"])
        title = event.get("title")
        fixture = league, target["gamma_event_id"], kickoff, title
        if fixture in fixture_sources:
            return fixture_sources[fixture]
        season = str(kickoff.year - (kickoff.month < 7))
        prior = xg.prior_season(season)
        self.target = {"league": league, "season": season}
        complete = True
        if league in understat.UNDERSTAT_LEAGUES:
            current, sequences = [], []
            for block_league, year in [(name, season) for name in understat.UNDERSTAT_LEAGUES] + [(league, prior)]:
                result, refs = self._fetch(understat.fetch_league_snapshot, block_league, year, None,
                                           now=acquire._now_utc())
                complete = complete and result.is_success
                sequences.extend(refs)
                if (block_league, year) == (league, season) and result.is_success:
                    current = result.value["data"]["dates"]
            provider = understat.SOURCE_KIND
        else:
            choice = next((item for item in fotmob.FotMobLeague if item.league_key == league), None)
            if choice is None:
                return None
            current, sequences = [], []
            for year in (prior, season):
                result, refs = self._fetch(sports.fetch_fotmob_inputs, {"league": choice, "season": year})
                complete = complete and result.is_success
                sequences.extend(refs)
                if year == season and result.is_success:
                    first = result.evidence[0]
                    current = [
                        {"id": item["id"], "h": {"id": item["home"]["id"], "title": item["home"]["name"]},
                         "a": {"id": item["away"]["id"], "title": item["away"]["name"]},
                         "datetime": item["status"]["utcTime"], "isResult": item["status"].get("finished")}
                        for item in fotmob._parse_league_payload(journal.decode_public_json(first.raw_bytes), season)
                    ]
            provider = sports.SOURCE_KIND_FOTMOB
        for row in current:
            if row["isResult"] or inputs._soccer_fixture_time(row["datetime"]) != kickoff:
                continue
            home, away = row["h"]["title"], row["a"]["title"]
            if title in (f"{home} versus {away}", f"{home} vs. {away}", f"{home} vs {away}"):
                sources = {"provider": provider, "league": league, "season": season, "fixture_id": str(row["id"]),
                           "home_team_id": str(row["h"]["id"]), "away_team_id": str(row["a"]["id"]),
                           "projection_binding": None, "responses": sequences}
                if complete:
                    fixture_sources[fixture] = sources
                return sources
        return None

    def collect_models(self, markets, requests, budget):
        self.budget = budget
        entries, rows = [], []
        for attempt in requests:
            if attempt.fetched_at is not None:
                entries.append(journal.capture(attempt.url, attempt.fetched_at, attempt.request_meta, attempt.body))
            rows.append({"sequence": attempt.sequence, "page": attempt.page, "request_meta": attempt.request_meta,
                         "content_sha256": inputs._digest(attempt.body) if attempt.fetched_at is not None else None})
        targets, _ = _targets(self.config, {"markets": list(markets.values()), "requests": rows}, entries)
        crypto_sources, soccer_sources = {}, {}
        for _, (target, event, body) in sorted(targets.items()):
            condition = target["condition_id"]
            if condition in self.target_sources:
                continue
            kind = target["market_kind"]
            if kind == pipeline.KIND_CRYPTO_TERMINAL:
                rule = inputs._TERMINAL_RULE.fullmatch(_fold(body.get("description") or ""))
                question = _QUESTION.fullmatch(_fold(body.get("question", "")))
                key = (rule["venue"], question["asset"]) if rule and question else None
                if key not in crypto_sources:
                    crypto_sources[key] = self._crypto_sources(body)
                self.target_sources[condition] = crypto_sources[key]
            elif kind in inputs._SOCCER_TYPES:
                self.target_sources[condition] = self._soccer_sources(event, target, soccer_sources)

    def seal(self, conn, scope, batch_sha, manifest, entries):
        existing = pg.read_command_run_event(conn, self.operation_key, 1, "SUCCEEDED")
        if existing is not None:
            self.summary = _summary(conn, self.operation_key, existing, batch_sha, self.config_bytes)
            return
        if manifest["capture_version"] != 2 or manifest["decision_as_of"] is None:
            return
        targets, unbound = _targets(self.config, manifest, entries)
        selected = []
        news = {}
        if self.interval is not None:
            bound_interval = inputs._read_reference(
                conn, self.interval, kind=operations._INTERVAL_KIND, source="d1:operation:interval:v1")
            interval = json.loads(bound_interval["content"])
            news = interval.get("preparation_news", {})
            if type(news) is not dict:
                raise prepare.PreparationError("papersim_prepare_news_scope_invalid")
        for _, (target, _, _) in sorted(targets.items()):
            sources = self.target_sources.get(target["condition_id"])
            reference = news.get(target["condition_id"])
            if reference is not None and target["market_kind"] in inputs._SOCCER_TYPES and sources:
                sources = {**sources, "news": reference}
                reference = None
            selected.append({**target, "sources": sources, "news": reference})
        at = acquire._now_utc()
        config = _registered(conn, self.config_bytes, inputs.CONFIG_KIND, inputs.CONFIG_SOURCE, at)
        if config["source"] != inputs.CONFIG_SOURCE:
            raise prepare.PreparationError("papersim_prepare_config_binding_conflict")
        batch = snapshots.verify_snapshot(conn, batch_sha)
        document = {"version": 1, "instance_id": self.instance_id, "config": _ref(config), "capture": _ref(batch),
                    "window_start": scope["window_start"], "window_end": scope["window_end"],
                    "decision_as_of": manifest["decision_as_of"], "interval": self.interval,
                    "queues": sorted(queue.queue for queue in self.config.queues if not queue.pack),
                    "targets": selected, "model_responses": self.responses,
                    "coverage": {"unbound": unbound, "model_fetches": self.fetches,
                                 "market": manifest["coverage"]}}
        # The capture transaction already owns the receipt and manifest. Read
        # back the same references before recording preparation success.
        canonical = operations.canonical_document(document)
        inputs._read_capture(conn, None, canonical, at, sealed_batch=(manifest, entries))
        inputs._model_responses(conn, canonical["model_responses"], at)
        row = _registered(conn, operations.document_bytes(document), inputs.INPUT_SET_KIND, inputs.INPUT_SET_SOURCE, at)
        pg.record_command_run_event(conn, store.CommandRunEventSubmission(
            operation_key=self.operation_key, command=inputs.INPUT_SET_COMMAND, phase="SUCCEEDED",
            scheduled_for=inputs._utc(scope["window_start"]), occurred_at=at,
            config_digest=config["content_sha256"],
            output_evidence=operations._refs(row["content_sha256"], row["byte_length"]),
        ))
        receipt = pg.read_command_run_event(conn, self.operation_key, 1, "SUCCEEDED")
        self.summary = _summary(conn, self.operation_key, receipt, batch_sha, self.config_bytes)


def _summary(conn, key, receipt, batch_sha, config_bytes):
    document = operations._read_document(conn, receipt["output_evidence"], kind=inputs.INPUT_SET_KIND,
                                         source=inputs.INPUT_SET_SOURCE)
    config = inputs._read_reference(conn, document["config"], kind=inputs.CONFIG_KIND, source=inputs.CONFIG_SOURCE)
    if (document["capture"]["sha256"] != batch_sha or receipt["operation_key"] != key
            or config["content"] != config_bytes or receipt["command"] != inputs.INPUT_SET_COMMAND
            or receipt["config_digest"] != config["content_sha256"]
            or receipt["error_code"] is not None or receipt["phase"] != "SUCCEEDED"):
        raise prepare.PreparationError("papersim_prepare_input_set_receipt_invalid")
    return {"input_set_sha256": receipt["output_evidence"][0].removeprefix("snapshot_sha256:"),
            "input_set_receipt": operations.canonical_document(operations._receipt_ref(receipt))}


def capture_preparation_inputs(*, repo_root: Path, config_bytes: bytes, window_start, window_end,
                               tag_slugs, allow_public_fetch: bool) -> dict:
    """Seal a separately identified input set for the managed capture window."""
    config = inputs._scan_configuration(config_bytes)
    if not any(not queue.pack for queue in config.queues):
        raise prepare.PreparationError("papersim_prepare_queue_scope_invalid")
    scope = acquire.discovery_scope(window_start, window_end, tag_slugs)
    key = inputs.input_set_operation_key(inputs._digest(config_bytes), scope)
    try:
        with closing(pg.connect_managed(repo_root)) as conn:
            conn.autocommit = True
            instance = pg.d1_managed_identity(conn, repo_root)["instance_id"]
            with pg.d1_operation_lock(conn, key):
                interval = _interval_reference(conn, instance, config.team, window_start, window_end)
                capture = _Capture(conn, config, config_bytes, scope, instance, key, interval)
                result = acquire.capture_inputs(repo_root=repo_root, window_start=window_start, window_end=window_end,
                                                tag_slugs=tag_slugs, allow_public_fetch=allow_public_fetch,
                                                _preparation=capture)
                if capture.summary is None:
                    raise prepare.PreparationError("papersim_prepare_capture_cutoff_invalid")
                return {**result, **capture.summary}
    except (prepare.PreparationError, acquire.AcquisitionError, store.PapersimStoreError):
        raise
    except Exception:
        raise prepare.PreparationError("papersim_prepare_capture_failed") from None
