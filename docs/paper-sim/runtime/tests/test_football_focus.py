"""Isolated tests for football focus and scheduled lineup evidence."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, redirect_stdout
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
import json
from threading import Event
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from offline_postgres import scratch_database

import football_daily as daily
import football_focus as focus
import football_feed as feed
import football_forward as forward
import football_learning as learning
import football_store as store
import football_shortlist as shortlist
import sports_portfolio as portfolio
import weixin_delivery


NOW = datetime(2026, 11, 23, 1, tzinfo=UTC)
VERSION = feed.MODEL_VERSION_SOCCER


def make_fixture(number=1, kickoff=NOW + timedelta(hours=4)):
    info = {"id": f"gamma:epl:{number}", "kickoff": feed.utc_iso(kickoff), "state": "scheduled",
            "league": "epl", "fixture_id": str(200 + number), "home": "Brentford FC", "away": "Everton FC",
            "fotmob_league_id": "47", "fotmob_home_id": "9937", "fotmob_away_id": "8668"}
    info["model"] = {
        "fixture_id": info["fixture_id"], "kickoff": info["kickoff"], "team_ids": ["9937", "8668"],
        "observed_at": feed.utc_iso(NOW - timedelta(minutes=2)), "sha256": f"{number:064x}",
        "lambdas": [[1.65, 1.55]] * 10, "variant_names": list(feed.soccer_model.SWEEP_NAMES), "over_2_5_hits": 8,
    }
    return {"id": info["id"], "revision": 1, "kickoff": kickoff, "info": info, "fixture_state": "scheduled",
            "last_seen": NOW - timedelta(minutes=3), "missing_scans": 0, "entered": False}


def make_market(number):
    over = feed.Selection("总进球大于2.5", "Over", str(9000 + number * 2), "over", 0.51)
    under = feed.Selection("总进球小于2.5", "Under", str(9001 + number * 2), "under", 0.49)
    event = "epl-bre-eve-2026-11-23-more-markets"
    return feed.Market(event, {"slug": "epl-bre-eve-2026-11-23-total-2pt5", "liquidityNum": 50000,
                               "feesEnabled": True, "feeSchedule": {"rate": .05, "takerOnly": True, "exponent": 1}},
                       str(6000 + number), f"0x{number:064x}", "total", 2.5, None, (over, under))


def make_books(market, *, observed=NOW - timedelta(seconds=5), asks=(.51, .50), size=1000):
    view = {}
    for index, option in enumerate(market.options):
        ask = asks[index]
        view[option.token_id] = ({
            "asset_id": option.token_id, "market": market.condition_id,
            "timestamp": str(int(observed.timestamp() * 1000)),
            "bids": [{"price": str(round(1 - asks[1 - index], 4)), "size": str(size)}],
            "asks": [{"price": str(ask), "size": str(size)}],
        }, NOW - timedelta(seconds=1))
    return view


def make_match(row, *, observed=NOW, confirmed=False, injury=False):
    info = row["info"]
    status = {"utcTime": info["kickoff"], "started": False, "finished": False, "cancelled": False}
    general = {"matchTimeUTCDate": info["kickoff"]}
    home_starters = [{"id": str(i), "positionId": 11 if i == 101 else 15} for i in range(101, 112)]
    away_starters = [{"id": str(i), "positionId": 11 if i == 201 else 15} for i in range(201, 212)]
    home = {"id": info["fotmob_home_id"], "formation": "4-3-3", "starters": home_starters,
            "unavailable": [{"id": "101", "unavailability": {"type": "injury"}}] if injury else []}
    away = {"id": info["fotmob_away_id"], "formation": "4-4-2", "starters": away_starters, "unavailable": []}
    lineup = {"matchId": info["fixture_id"], "lineupType": "confirmed" if confirmed else "predicted", "source": "Opta",
              "homeTeam": home, "awayTeam": away}
    return {"ongoing": False, "content": {"lineup": lineup}}, general, status, observed


class FocusTests(unittest.TestCase):
    def test_pair_rejects_old_identity_and_incomplete_books(self):
        market = make_market(1)
        now, kickoff = NOW, NOW + timedelta(hours=4)
        paired = focus._paired_books(market, make_books(market), now, kickoff)
        self.assertEqual([leg["filled_usd"] for leg in paired.values()], [250, 250])
        self.assertIsNone(focus._paired_books(market, make_books(market, size=100), now, kickoff))
        aged = make_books(market, observed=now - feed.BOOK_AGE - timedelta(seconds=1))
        self.assertIsNone(focus._paired_books(market, aged, now, kickoff))
        wrong = make_books(market)
        wrong[market.options[1].token_id][0]["market"] = "0x" + "9" * 64
        self.assertIsNone(focus._paired_books(market, wrong, now, kickoff))
        self.assertIsNone(focus._paired_books(market, wrong, now, now))

    def test_screen_uses_standard_fee_spread_and_worst_variant_gates(self):
        row, market = make_fixture(), make_market(1)
        projection = feed._model_projection(row["info"], NOW)
        pair = focus._paired_books(market, make_books(market), NOW, row["kickoff"])
        over, under = market.options
        selected, failures = focus._screen(market, over, pair[over.token_id], projection, market.raw)
        gate, quote, fee, cost = selected
        self.assertEqual(failures, ())
        self.assertEqual(gate.decision.failed, ("news_concordant",))
        self.assertEqual(cost, .05 * .51 * .49)
        self.assertAlmostEqual(fee, .05)
        self.assertEqual(len(quote["qs_variants"]), 10)
        self.assertGreater(gate.worst_variant_edge - cost, 0)
        self.assertFalse(focus._screen(market, under, pair[under.token_id], projection, market.raw)[0])
        self.assertIn("liq", focus._screen(market, over, pair[over.token_id], projection,
                                          market.raw | {"liquidityNum": 4999})[1])

    def test_lineup_conflict_is_not_an_early_focus_and_verified_formal_window_is_separate(self):
        row = make_fixture()
        fixture = focus.Fixture(row, (), {})
        context, reason = focus._fixture_lineup(fixture, make_match(row), lambda: NOW)
        self.assertIn("预测首发", reason)
        self.assertEqual(context["lineup_type"], "predicted")
        self.assertEqual(focus._fixture_lineup(fixture, make_match(row, injury=True), lambda: NOW),
                         (None, "lineup_conflict"))
        self.assertEqual(focus._fixture_lineup(fixture, None, lambda: NOW),
                         (None, "independent_fixture_unverified"))

        row = make_fixture(kickoff=NOW + timedelta(minutes=25))
        opening = row["kickoff"] - timedelta(minutes=30)
        earlier = make_match(row, observed=opening - timedelta(minutes=10), confirmed=True)
        row["info"][feed.CONFIRMED_BASELINE] = feed._lineup_evidence(
            earlier[0], row["info"], earlier[3], focus.model.details_url(row["info"]["fixture_id"]))
        fixture = focus.Fixture(row, (), {})
        self.assertEqual(focus._fixture_lineup(fixture, make_match(row, confirmed=True), lambda: NOW),
                         (None, "current_formal_review"))

    def test_scan_reads_all_seven_models_and_rechecks_fixture_revision(self):
        rows = [make_fixture(n) for n in range(1, 8)]
        markets = {row["info"]["fixture_id"]: make_market(n) for n, row in enumerate(rows, 1)}
        books = {token: order_book for market in markets.values() for token, order_book in make_books(market).items()}
        current = [row if row != rows[0] else row | {"revision": 2} for row in rows]
        scope = focus.admission.scope_key("epl", "total", 2.5, None, VERSION)
        by_id = {row["info"]["fixture_id"]: row for row in rows}

        def select(sql, _):
            self.assertTrue(sql.lstrip().startswith("SELECT"))
            return SimpleNamespace(fetchall=lambda: current if "f.id=ANY" in sql else rows)

        with (
            patch.object(focus.admission, "active_scopes", return_value={scope}),
            patch.object(forward, "_markets", side_effect=lambda info, *_: ([markets[info["fixture_id"]]], True)) as listed,
            patch.object(forward, "_books", return_value=books) as captured,
            patch.object(forward, "_get_match", side_effect=lambda identity, *_: make_match(by_id[identity[0]])),
            patch.object(feed, "_verify_live_contract",
                         side_effect=lambda market, *_: market.raw | {"liquidityNum": Decimal("50000")}),
        ):
            report = focus.scan(SimpleNamespace(execute=select), NOW, NOW + timedelta(hours=24))
        self.assertEqual(listed.call_count, 7)
        self.assertEqual(captured.call_count, 1)
        self.assertEqual(report["fresh_model_fixtures"], 7)
        self.assertEqual(report["status_counts"], {"expired_or_changed_on_recheck": 1, "focus_candidate": 6})
        self.assertEqual(len(report["candidates"]), 6)
        self.assertEqual({c["direction"] for c in report["candidates"]}, {"总进球大于2.5"})
        self.assertTrue(all(c["stake_fen"] == 0 and c["home_display"] == "布伦特福德" for c in report["candidates"]))
        self.assertTrue(all(len(c["market_terms_sha256"]) == 64 for c in report["candidates"]))

    def test_scope_denial_and_network_deadline_are_reported_per_fixture(self):
        row = make_fixture()
        connection = SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchall=lambda: [row]))
        scope = focus.admission.scope_key("epl", "total", 3.5, None, VERSION)
        with (
            patch.object(focus.admission, "active_scopes", return_value={scope}),
            patch.object(forward, "_markets", return_value=([make_market(1)], True)),
            patch.object(forward, "_books") as captured,
        ):
            report = focus.scan(connection, NOW, NOW + timedelta(hours=24))
            self.assertEqual(report["status_counts"], {"no_verified_contract_in_scope": 1})
            captured.assert_not_called()
        with (
            patch.object(focus.admission, "active_scopes", return_value={scope}),
            patch.object(focus, "MAX_SCAN_SECONDS", 0),
            patch.object(forward, "_markets") as listed,
        ):
            report = focus.scan(connection, NOW, NOW + timedelta(hours=24))
            self.assertEqual(report["status_counts"], {"http_budget_exhausted": 1})
            listed.assert_not_called()


class ScheduledLineupTests(unittest.TestCase):
    def setUp(self):
        self.database = scratch_database(self)
        self.socket = self.database.socket_dir
        self.connection = self.connect()
        self.addCleanup(self.connection.close)
        self.now = store.db_now(self.connection).replace(second=0, microsecond=0)
        self.connection.execute("DROP SCHEMA IF EXISTS football_notify CASCADE")
        self.connection.execute(store.SCHEMA)
        self.connection.execute(
            "INSERT INTO binding(target_digest, marker_digest, legacy_receipt_prefix) VALUES (%s, %s, %s)",
            ("a" * 64, "b" * 64, "c" * 16),
        )

    def connect(self):
        return self.database.connect()

    def add_fixture(self, number=1, *, lead=60, research=False):
        row = make_fixture(number, kickoff=self.now + timedelta(minutes=lead))
        info = row["info"] | {"watch": not research}
        if research:
            info["league"] = "bra"
        saved = daily.replace_fixture(self.connection, info, row["kickoff"], self.now - timedelta(minutes=3))
        if research:
            enriched = info | {"research": {"competition": "bra"}}
            self.assertEqual(learning.publish_research(self.connection, [saved], [enriched], self.now), 1)
        return row | {"info": info}

    def lineup(self, row, at, *, confirmed=True):
        return feed._lineup_evidence(make_match(row, confirmed=confirmed)[0], row["info"], at,
                                     focus.model.details_url(row["info"]["fixture_id"]))

    def history(self, row, revision=1):
        result = self.connection.execute(
            "SELECT detail FROM job WHERE fixture_key = %s AND revision = %s AND stage = 't30'",
            (row["id"], revision),
        ).fetchone()
        return result["detail"].get("confirmed_lineup_attempts", {})

    def tick(self, at, command=("tick",)):
        stdout = StringIO()
        with (
            patch.object(sys, "argv", ["football_daily.py", *command]),
            patch.object(store, "connect", return_value=nullcontext(self.connection)),
            patch.object(store, "db_now", return_value=at),
            patch.object(store, "LEGACY", self.socket / "no-historical-data"),
            patch.object(daily, "start_workers", return_value=[]),
            patch.object(daily, "login_lingering", return_value=None),
            redirect_stdout(stdout),
        ):
            self.assertEqual(daily.main(), 0)
        return json.loads(stdout.getvalue())

    def test_tick_records_each_minute_on_the_current_database_fixture(self):
        row = self.add_fixture(research=True, lead=75)
        saved = self.connection.execute("SELECT watch, info FROM fixture WHERE id = %s", (row["id"],)).fetchone()
        self.assertTrue(saved["watch"])
        self.assertFalse(saved["info"]["watch"])
        late = self.add_fixture(2, lead=30)
        stale = self.add_fixture(3)
        historical = self.add_fixture(4, lead=61)
        self.connection.execute("UPDATE fixture SET missing_scans = 2 WHERE id = %s", (stale["id"],))
        self.connection.execute("UPDATE fixture SET legacy_id = 'historical' WHERE id = %s", (historical["id"],))
        next_minute = self.now + timedelta(minutes=1)
        first_context = self.lineup(row, self.now, confirmed=False)
        second_context = self.lineup(row, next_minute)
        with patch.object(feed, "_fotmob_evidence", side_effect=[("verified", first_context, None),
                                                               ("verified", second_context, None)]) as reader:
            self.tick(self.now, ("tick", "--preview"))
            self.assertEqual(self.tick(self.now)["confirmed_lineups"], {
                "attempted": 1, "captured": 0, "rejected": {"lineups_unconfirmed": 1},
            })
            self.assertEqual(self.tick(self.now + timedelta(seconds=35))["confirmed_lineups"]["attempted"], 0)
            self.assertEqual(self.tick(next_minute)["confirmed_lineups"], {"attempted": 1, "captured": 1, "rejected": {}})
            self.assertEqual([call.args[0]["fixture_id"] for call in reader.call_args_list], [row["info"]["fixture_id"]] * 2)
        history = self.history(row)
        rejected, accepted = history[self.now.isoformat()], history[next_minute.isoformat()]
        self.assertEqual((rejected["attempted_at"], rejected["status"], rejected["reason"]),
                         (self.now.isoformat(), "rejected", "lineups_unconfirmed"))
        self.assertEqual((accepted["recorded_at"], accepted["status"], accepted["reason"]),
                         (next_minute.isoformat(), "captured", "lineups_confirmed"))
        self.assertEqual(accepted["evidence"], second_context)
        self.assertEqual(accepted["team_ids"], second_context["team_ids"])
        self.assertEqual(accepted["fixture_id"], second_context["fixture_id"])
        refreshed = daily.replace_fixture(self.connection, row["info"], row["kickoff"], next_minute)
        self.assertEqual(refreshed["info"][feed.CONFIRMED_BASELINE], second_context)
        t30 = row["kickoff"] - timedelta(minutes=30)
        news, state = feed._news(refreshed["info"], self.lineup(row, t30), t30, lambda: t30)
        self.assertTrue(news.concordant)
        self.assertEqual(state, "verified")
        self.assertEqual([self.history(other) for other in (late, stale, historical)], [{}, {}, {}])
        self.assertEqual(self.connection.execute("SELECT count(*) AS count FROM attempt").fetchone()["count"], 0)

    def test_overlapping_tick_is_idempotent_and_revision_change_blocks_publication(self):
        row = self.add_fixture()
        fetch_started, finish_fetch = Event(), Event()
        context = self.lineup(row, self.now)

        def fetch(*_):
            fetch_started.set()
            self.assertTrue(finish_fetch.wait(5))
            return "verified", context, None

        with patch.object(store, "db_now", return_value=self.now), patch.object(feed, "_fotmob_evidence", side_effect=fetch) as reader:
            with self.connect() as concurrent, ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(daily.capture_scheduled_lineups, concurrent)
                try:
                    self.assertTrue(fetch_started.wait(5))
                    self.assertEqual(daily.capture_scheduled_lineups(self.connection)["attempted"], 0)
                    moved = row["kickoff"] + timedelta(minutes=5)
                    with self.connection.transaction():
                        store.lock_queue(self.connection)
                        daily.replace_fixture(self.connection, row["info"], moved, self.now)
                finally:
                    finish_fetch.set()
                self.assertEqual(result.result(timeout=5)["rejected"], {"fixture_revision_or_identity_changed": 1})
                reader.assert_called_once()
        attempt = self.history(row)[self.now.isoformat()]
        self.assertEqual(attempt["status"], "rejected")
        self.assertNotIn("evidence", attempt)
        saved = self.connection.execute("SELECT revision, info FROM fixture WHERE id = %s", (row["id"],)).fetchone()
        self.assertEqual(saved["revision"], 2)
        self.assertNotIn(feed.CONFIRMED_BASELINE, saved["info"])

    def test_confirmed_domestic_xi_reopens_first_job_and_keeps_minute_and_revision_boundaries(self):
        row = self.add_fixture(7, lead=61)
        info, kickoff = row["info"], row["kickoff"]
        self.connection.execute(
            """UPDATE job SET state='suppressed', reason='thresholds_unmet',
                              detail='{"pregame_round":"closed-90","pregame_review_history":[{"round_key":"closed-90"}]}'
               WHERE fixture_key=%s AND stage='t90'""", (row["id"],),
        )

        def job():
            return self.connection.execute(
                """SELECT j.*,f.kickoff,f.fixture_state,f.legacy_id,f.info FROM job j JOIN fixture f
                    ON f.id=j.fixture_key WHERE f.id=%s AND j.revision=f.revision AND j.stage='t90'""",
                (row["id"],),
            ).fetchone()

        with patch.object(feed, "_gamma_event_refs", side_effect=AssertionError("unconfirmed market scan")):
            self.assertEqual(feed.review(info, self.now, "t90")["reason_code"], "lineups_unconfirmed")
        with patch.object(feed, "_fotmob_evidence", return_value=("verified", self.lineup(row, self.now), None)):
            self.assertEqual(self.tick(self.now)["confirmed_lineups"]["reviews_activated"], 1)
        opened = job()
        self.assertEqual(opened["state"], "pending")
        self.assertNotIn("pregame_round", opened["detail"])
        self.assertEqual(opened["detail"]["pregame_review_history"], [{"round_key": "closed-90"}])
        self.assertEqual(feed.confirmed_review_opening(opened["info"]), self.now)
        self.assertIn(opened["key"], [x["key"] for x in store.due_pregame_rows(self.connection, self.now)])
        self.assertEqual(shortlist._paper_window(opened, legacy=False, at=self.now),
                         (self.now, kickoff - timedelta(minutes=30)))
        self.assertTrue(daily.regular_window_open(opened, self.now))
        self.assertFalse(daily.regular_window_open(opened, self.now - timedelta(seconds=1)))
        self.assertFalse(daily.regular_window_open(opened, kickoff - timedelta(minutes=30)))
        self.assertFalse(daily.regular_window_open(opened | {"info": info | {"sport": "basketball"}}, self.now))
        self.assertEqual(shortlist._paper_window(opened, legacy=True, at=self.now),
                         (kickoff - timedelta(minutes=90), kickoff - timedelta(minutes=30)))
        self.assertEqual([item[3]["stage"] for item in shortlist._scheduled_windows(
            {"fixture_key": row["id"], "revision": 1, "watch": True, "legacy_id": None, "kickoff": kickoff,
             "fixture_state": "scheduled", "info": opened["info"]}, {}, {})], ["T−90", "首发公布后", "T−30"])

        later = self.now + timedelta(minutes=1)
        self.connection.execute("UPDATE job SET state='suppressed', reason='fixture_already_booked' WHERE key=%s",
                                (opened["key"],))
        with patch.object(feed, "_fotmob_evidence", return_value=("verified", self.lineup(row, later), None)):
            self.assertNotIn("reviews_activated", self.tick(later)["confirmed_lineups"])
        refreshed = job()
        self.assertEqual(refreshed["state"], "suppressed")
        self.assertEqual(feed.confirmed_review_opening(refreshed["info"]), self.now)
        self.assertEqual(feed.stamp(refreshed["info"][feed.CONFIRMED_BASELINE]["observed_at"]), later)
        daily.replace_fixture(self.connection, info, kickoff, later)
        self.assertEqual(feed.confirmed_review_opening(job()["info"]), self.now)
        moved = daily.replace_fixture(self.connection, info, kickoff + timedelta(minutes=5), later)
        self.assertNotIn(feed.CONFIRMED_OPENED_AT, moved["info"])

    def test_confirmed_window_books_one_position_after_original_job_closed(self):
        portfolio.restart(self.connection)
        self.connection.execute("INSERT INTO native_binding(session_id) VALUES ('sess_lineup-sandbox')")
        row = self.add_fixture(8, lead=62)
        opening = self.now + timedelta(minutes=1)
        now = opening + timedelta(minutes=1)
        predicted = self.lineup(row, self.now - timedelta(minutes=40), confirmed=False)
        confirmed = self.lineup(row, opening)
        market = make_market(8)
        base = market.event_slug.removesuffix("-more-markets")
        # Emulate a stored confirmation captured by an older worker before there was an additional interval.
        self.connection.execute("UPDATE fixture SET info = info || %s WHERE id = %s", (daily.Jsonb({
            "review_context": predicted, feed.CONFIRMED_BASELINE: confirmed, "base_slug": base,
            "model": row["info"]["model"] | {"observed_at": feed.utc_iso(self.now - timedelta(minutes=2))},
        }), row["id"]))
        self.connection.execute("UPDATE job SET state='suppressed', reason='thresholds_unmet' "
                                "WHERE fixture_key = %s AND stage = 't90'", (row["id"],))
        with patch.object(feed, "_fotmob_evidence", return_value=("fotmob_unavailable", None, None)):
            summary = self.tick(opening)["confirmed_lineups"]
        self.assertEqual(summary["reviews_activated"], 1)
        self.assertEqual(summary["rejected"], {"fotmob_unavailable": 1})
        job = next(r for r in store.due_pregame_rows(self.connection, now) if r["stage"] == "t90")
        contract = market.raw | {
            "active": True, "closed": False, "acceptingOrders": True, "enableOrderBook": True,
            "id": market.id, "conditionId": market.condition_id, "gameStartTime": row["info"]["kickoff"],
            "description": "This market covers the first 90 minutes of regular play plus stoppage time.",
            "sportsMarketType": "totals", "groupItemTitle": "O/U 2.5", "line": 2.5,
            "question": "Brentford vs Everton total goals?", "resolutionSource": "official league result",
            "outcomes": ["Over", "Under"], "clobTokenIds": [option.token_id for option in market.options],
        }
        book_time = now - timedelta(seconds=1)

        def public(url, *_):
            if url.endswith("/books"):
                return [data for data, _ in make_books(market, observed=book_time).values()], now
            if url.endswith(f"/markets/{market.id}"):
                return contract, now
            slug = url.rsplit("/", 1)[-1]
            self.assertIn(slug, (base, market.event_slug))
            return {"slug": slug, "title": "Brentford FC vs. Everton FC", "active": True, "closed": False,
                    "startTime": row["info"]["kickoff"], "markets": [contract] if slug == market.event_slug else []}, now

        def sandbox_handoff(body, *, on_submit, **_):
            self.assertIn("含费赔率", body)
            self.assertIn("模拟下注", body)
            attempt = "d" * 32
            self.assertTrue(on_submit(attempt_key=attempt, body_variant="primary", body_digest=store.digest(body)))
            return {"state": "accepted", "attempt_key": attempt, "body_variant": "primary", "platform": "weixin",
                    "target_verified": True, "confirmed_at": now.isoformat(), "message_id_sha256": "e" * 64,
                    "native_session_verified": True}

        with (
            patch.object(store, "db_now", return_value=now) as db_clock,
            patch.object(feed, "_clock", return_value=lambda: now),
            patch.object(feed, "_public", side_effect=public),
            patch.object(feed, "_quoted_markets", wraps=feed._quoted_markets) as capture_books,
            patch.object(feed, "_fotmob_evidence", return_value=("verified", self.lineup(row, now, confirmed=False), None)) as lineup,
            patch.object(weixin_delivery, "deliver", side_effect=sandbox_handoff) as handoff,
        ):
            incomplete = daily.pregame_decisions(self.connection, [job])[job["key"]]
            self.assertEqual(incomplete["reason_code"], "lineups_unconfirmed")
            capture_books.assert_not_called()
            def signed(doc):
                return doc | {"sha256": feed.document_digest({k: v for k, v in doc.items() if k != "sha256"})}

            for missing in ("provider", "source", "absences", "overlap", "goalkeeper", "formation"):
                with self.subTest(missing=missing):
                    rejected = self.lineup(row, now)
                    home, away = rejected["lineups"]
                    if missing == "provider":
                        rejected["lineup_provider"] = ""
                    elif missing == "source":
                        rejected["source"] = "https://example.test/alternate"
                    elif missing == "absences":
                        home["absences_complete"] = False
                    elif missing == "overlap":
                        away["starters"][3] = home["starters"][3]
                    elif missing == "goalkeeper":
                        away["goalkeeper"] = None
                    else:
                        home["formation"] = "4-5-4"
                    lineup.return_value = ("verified", signed(rejected), None)
                    self.assertEqual(daily.pregame_decisions(self.connection, [job])[job["key"]]["reason_code"],
                                     "lineups_unconfirmed")
            capture_books.assert_not_called()
            lineup.return_value = ("verified", self.lineup(row, now), None)
            decisions = daily.pregame_decisions(self.connection, [job])
            result = decisions[job["key"]]
            self.assertEqual(result["reason_code"], "qualified")
            self.assertEqual(result["signal"]["review_phase"], "confirmed_lineups")
            ranked = shortlist.select_round(self.connection, [job], decisions, now)
            self.assertEqual(len(ranked.allocations), 1)
            self.assertGreater(ranked.allocations[0].stake_fen, 0)
            for field, invalid in (("book_observed_at", feed.utc_iso(opening - timedelta(seconds=1))),
                                   ("lineup_window_opened_at", feed.utc_iso(opening + timedelta(seconds=1))),
                                   ("review_phase", "t90")):
                with self.subTest(field=field):
                    edited = result["signal"] | {field: invalid}
                    self.assertEqual(shortlist._preferred_direction(
                        job, result | {"signals": [edited], "signal": edited}, now), (None, "invalid_paper_evidence"))
            book_time = opening - timedelta(seconds=1)
            self.assertEqual(daily.pregame_decisions(self.connection, [job])[job["key"]]["reason_code"], "books_unavailable")
            book_time = now - timedelta(seconds=1)
            def correct_xi_before_handoff(body, *, on_submit, **_):
                corrected = self.lineup(row, now)
                corrected["lineups"][0]["formation"] = "4-2-3-1"
                self.connection.execute("UPDATE fixture SET info=info || %s WHERE id=%s", (daily.Jsonb({
                    feed.INITIAL_CONFIRMED_LINEUP: confirmed, feed.CONFIRMED_BASELINE: signed(corrected),
                }), row["id"]))
                self.assertFalse(on_submit(attempt_key="f" * 32, body_variant="primary", body_digest=store.digest(body)))
                return {"state": "unconfirmed", "reason": "inputs_updated"}

            with patch.object(weixin_delivery, "deliver", side_effect=correct_xi_before_handoff) as raced:
                self.assertEqual(daily.worker(self.connection, "routine-1")["state"], "suppressed")
                raced.assert_called_once()
            self.assertEqual(portfolio.status(self.connection)["open_positions"], 0)
            retry = self.connection.execute("SELECT state, reason, next_try FROM job WHERE key=%s", (job["key"],)).fetchone()
            self.assertEqual((retry["state"], retry["reason"]), ("pending", "news_changed"))
            now = retry["next_try"]
            db_clock.return_value = now
            book_time = now - timedelta(seconds=1)
            lineup.return_value = ("verified", self.lineup(row, now), None)
            self.tick(now)
            self.assertEqual(daily.worker(self.connection, "routine-1")["state"], "accepted")
            handoff.assert_called_once()
            position = portfolio.open_positions(self.connection)[0]
            self.assertEqual(position["stake_fen"], ranked.allocations[0].stake_fen)
            self.assertEqual(position["settlement_evidence"]["signal"]["review_phase"], "confirmed_lineups")
            self.assertEqual(self.connection.execute("SELECT count(*) AS n FROM football_forward_recommendation"
                                                    ).fetchone()["n"], 1)
            self.assertEqual(portfolio.enter(self.connection, ranked.quotes, round_key="later-paper-stage"), [])
            db_clock.return_value = job["kickoff"] - timedelta(minutes=30)
            self.connection.execute("UPDATE fixture SET last_seen=%s WHERE id=%s", (db_clock.return_value, row["id"]))
            self.assertEqual(daily.worker(self.connection, "urgent-1")["state"], "suppressed")
            self.assertEqual(self.connection.execute("SELECT reason FROM job WHERE fixture_key=%s AND stage='t30'",
                                                    (row["id"],)).fetchone()["reason"], "fixture_already_booked")
            handoff.assert_called_once()
            self.assertEqual(portfolio.status(self.connection)["open_positions"], 1)


if __name__ == "__main__":
    unittest.main()
