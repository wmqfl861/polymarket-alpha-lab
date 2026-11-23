"""Regression assertions from the one-time focus correction."""

from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from psycopg.types.json import Jsonb

import correction_recovery as repair
from offline_postgres import scratch_database


DENIED_AT = datetime(2026, 2, 18, 12, tzinfo=UTC)
DEADLINE = DENIED_AT + timedelta(days=1)
CORRECTION_KEY = "synthetic-focus-correction"
JOB_KEY = repair.correction_job_key(CORRECTION_KEY)
STAGE_KEY = repair.daily.focus_stage_key(JOB_KEY)
NOW = DENIED_AT + timedelta(minutes=10)
AFTER = DENIED_AT + timedelta(seconds=1)


def step(*args):
    return repair.step(*args, denied_at=DENIED_AT, deadline=DEADLINE)


class GateTests(unittest.TestCase):
    def test_old_equal_ambiguous_disconnected_and_future_never_publish(self):
        for gate in (("observed", DENIED_AT), ("observed", DENIED_AT - timedelta(days=1)),
                     ("observed", NOW + timedelta(seconds=1)), ("context_ambiguous", None),
                     ("active_home_unavailable", None), ("route_unavailable", None)):
            with self.subTest(gate=gate):
                publish = Mock()
                self.assertFalse(step(lambda: NOW, lambda: "absent", lambda _: gate, publish)[1])
                publish.assert_not_called()

    def test_new_message_retries_unavailable_focus_and_an_unsubmitted_expired_job(self):
        state = ["absent"]
        publisher = Mock(side_effect=["no_focus", "focus_queue_busy", "waiting_for_fresh_quote", "pending"])
        def once():
            return step(lambda: NOW, lambda: state[0], lambda _: ("observed", AFTER), publisher)
        self.assertEqual(once(), ("awaiting_fresh_candidates", False))
        self.assertEqual(once(), ("focus_scan_in_progress", False))
        state[0] = "retry_before_submission"
        self.assertEqual(once(), ("awaiting_fresh_candidates", False))
        self.assertEqual(once(), ("existing_handoff", False))
        state[0] = "submitted"
        self.assertEqual(once(), ("awaiting_existing_receipt", False))
        state[0] = "accepted"
        self.assertEqual(once(), ("platform_accepted", True))
        self.assertEqual(publisher.call_count, 4)

    def test_committed_but_client_error_restarts_with_receipt_observation(self):
        ledger = ["absent"]
        def publish():
            ledger[0] = "unknown"
            raise TimeoutError("example exception text")
        params = (lambda: NOW, lambda: ledger[0], lambda _: ("observed", AFTER), Mock(side_effect=publish))
        self.assertEqual(step(*params), ("verify_correction_record", False))
        self.assertEqual(step(*params), ("awaiting_existing_receipt", False))
        params[-1].assert_called_once()

    def test_job_attempt_or_deadline_never_touches_remote_or_publication(self):
        remote, publish = Mock(), Mock()
        for state in ("pending","leased","submitted","accepted","unknown","ambiguous_correction"):
            step(lambda: NOW, lambda: state, remote, publish)
        db = Mock()
        self.assertEqual(step(lambda: DEADLINE, db, remote, publish), ("deadline_reached", True))
        db.assert_not_called()
        remote.assert_not_called()
        publish.assert_not_called()
        times = iter([DEADLINE - timedelta(seconds=1), DEADLINE])
        self.assertEqual(step(lambda: next(times), lambda: "absent", lambda _: ("observed", AFTER), publish),
                         ("deadline_reached", True))
        publish.assert_not_called()

    def test_windows_probe_emits_only_metadata_from_the_pinned_hop(self):
        hostname = "example-windows"
        route = Mock(return_value=("example-hub", "example-hop"))
        windows = Mock(return_value={"state": "observed", "modified_utc": "2026-02-18T12:00:01.0000000Z"})

        def read():
            return repair.metadata(DEADLINE, clock=lambda: NOW, hostname=hostname,
                                   lookup_route=route, query_status=windows)

        self.assertEqual(read(), ("observed", AFTER))
        self.assertEqual(windows.call_args.args[:2], ("example-hub", "example-hop"))
        ps = windows.call_args.args[2]
        for part in ("$env:HERMES_HOME", hostname, "*.context-tokens.json", "LastWriteTimeUtc", "-First 2"):
            self.assertIn(part, ps)
        for part in ("Get-Content", "ReadAll", "hermes.exe", "Send-", "Get-Process", "FullName"):
            self.assertNotIn(part, ps)
        windows.return_value = {"state": "context_ambiguous"}
        self.assertEqual(read(), ("context_ambiguous", None))
        windows.return_value = {"state": "observed", "modified_utc": "junk"}
        self.assertEqual(read(), ("metadata_unavailable", None))
        windows.return_value = None
        self.assertEqual(read(), ("route_unavailable", None))

    def test_publish_calls_new_fresh_correction_api_with_exact_key_and_deadline(self):
        db = Mock()
        publish = Mock(return_value={"state": "queued", "body": "example"})
        with patch.object(repair, "correction_record", side_effect=["retry_before_submission", "pending"]):
            self.assertEqual(repair.request_correction(db, correction_key=CORRECTION_KEY, deadline=DEADLINE,
                                                       get_time=lambda _: NOW, request=publish), "pending")
            publish.assert_called_once_with(db, correction_key=CORRECTION_KEY, correction_until=DEADLINE)


class IdentifierTests(unittest.TestCase):
    def setUp(self):
        self.db = scratch_database(self).connect()
        self.addCleanup(self.db.close)
        self.db.execute("DROP SCHEMA IF EXISTS football_notify CASCADE")
        self.db.execute(repair.store.SCHEMA)

    def job(self, key, state, business):
        detail={"kind":repair.store.FOCUS_KIND, "correction_key":business}
        self.db.execute("""INSERT INTO job(key,stage,starts_at,deadline,next_try,state,detail)
                           VALUES (%s,'note',%s,%s,%s,%s,%s)""",
                        (key,NOW,DEADLINE,NOW,state,Jsonb(detail)))

    def attempt(self, identifier, stage, state):
        self.db.execute("""INSERT INTO attempt(key,stage_key,body_variant,body_digest,submitted_at,deadline,
                                               lease_until,state)
                           VALUES (%s,%s,'primary',%s,%s,%s,%s,%s)""",
                        (identifier,stage,"b"*64,NOW,DEADLINE,DEADLINE,state))

    def record(self):
        return repair.correction_record(self.db, CORRECTION_KEY)

    def test_matching_family_or_orphaned_stage_blocks_when_real_attempt_exists(self):
        self.job("0"*64,"accepted","different")
        self.assertEqual(self.record(),"absent")
        self.job(JOB_KEY,"expired",CORRECTION_KEY)
        self.assertEqual(self.record(),"retry_before_submission")
        suffix=repair.store.digest("owner-focus\n"+CORRECTION_KEY+"-2")
        self.job(suffix,"pending",CORRECTION_KEY)
        self.assertEqual(self.record(),"pending")
        self.attempt("d"*32,repair.daily.focus_stage_key(suffix),"unknown")
        self.assertEqual(self.record(),"unknown")
        self.attempt("e"*32,STAGE_KEY,"accepted")
        self.assertEqual(self.record(),"accepted")

    def audited_turn(self):
        key = JOB_KEY
        nonce = "a" * 32
        expired = self.db.execute("SELECT clock_timestamp() - interval '3 minutes' AS at").fetchone()["at"]
        self.job(key, "suppressed", CORRECTION_KEY)
        self.attempt(nonce, STAGE_KEY, "unknown")
        self.db.execute("""UPDATE attempt SET submitted_at=%s,deadline=%s,reason='verified_native_not_invoked'
                            WHERE key=%s""", (expired - timedelta(minutes=2), expired, nonce))
        self.db.execute("""UPDATE job SET starts_at=%s,deadline=%s,reason='verified_native_not_invoked',
                              attempt_key=%s,detail=detail || %s WHERE key=%s""",
                        (expired - timedelta(minutes=3), expired, nonce,
                         Jsonb({"native_non_submission":{"attempt_key":nonce}}), key))
        return key, nonce

    def test_only_the_exact_audited_expired_attempt_can_be_ignored(self):
        key, nonce = self.audited_turn()
        self.assertEqual(self.record(), "retry_before_submission")
        safe = self.db.execute("SELECT state,reason,confirmed_at,receipt_digest FROM attempt WHERE key=%s", (nonce,)).fetchone()
        self.assertEqual(safe, {"state":"unknown", "reason":"verified_native_not_invoked",
                                "confirmed_at":None, "receipt_digest":None})
        self.assertEqual(self.db.execute("SELECT attempt_key FROM job WHERE key=%s", (key,)).fetchone()["attempt_key"], nonce)
        guards = [
            ("audit_missing", "UPDATE job SET detail=detail-'native_non_submission'", "unknown"),
            ("different_nonce", "UPDATE job SET detail=jsonb_set(detail,'{native_non_submission,attempt_key}',"
                                "to_jsonb(repeat('b',32)))", "unknown"),
            ("job_link_missing", "UPDATE job SET attempt_key=NULL", "unknown"),
            ("wrong_stage", "UPDATE attempt SET stage_key='unrelated-stage'", "unknown"),
            ("job_not_closed", "UPDATE job SET state='unknown'", "unknown"),
            ("job_reason_mismatch", "UPDATE job SET reason=NULL", "unknown"),
            ("attempt_reason_mismatch", "UPDATE attempt SET reason='worker_interrupted_after_submit'", "unknown"),
            ("already_submitted", "UPDATE attempt SET state='submitted'", "submitted"),
            ("already_accepted", "UPDATE attempt SET state='accepted'", "accepted"),
            ("job_has_acceptance", "UPDATE job SET accepted_at=clock_timestamp()", "unknown"),
            ("has_confirmation", "UPDATE attempt SET confirmed_at=clock_timestamp()", "unknown"),
            ("has_receipt", "UPDATE attempt SET receipt_digest=repeat('e',64)", "unknown"),
            ("job_not_expired", "UPDATE job SET deadline=clock_timestamp()+interval '2 minutes'", "unknown"),
            ("attempt_not_expired", "UPDATE attempt SET deadline=clock_timestamp()+interval '2 minutes'", "unknown"),
        ]
        for label, sql, expected in guards:
            with self.subTest(guard=label), self.db.transaction(force_rollback=True):
                self.db.execute(sql)
                self.assertEqual(self.record(), expected)

    def test_old_audit_does_not_hide_other_attempts_or_reopen_the_latest_job(self):
        self.audited_turn()
        later = repair.store.digest("owner-focus\n" + CORRECTION_KEY + "-2")
        self.job(later, "pending", CORRECTION_KEY)
        self.assertEqual(self.record(), "pending")
        for stage in (STAGE_KEY, repair.daily.focus_stage_key(later)):
            with self.subTest(stage=stage), self.db.transaction(force_rollback=True):
                self.attempt("e"*32, stage, "unknown")
                self.assertEqual(self.record(), "unknown")
                self.db.execute("UPDATE attempt SET state='accepted' WHERE key=%s", ("e"*32,))
                self.assertEqual(self.record(), "accepted")
        self.db.execute("UPDATE job SET state='expired' WHERE key=%s", (later,))
        self.assertEqual(self.record(), "retry_before_submission")


if __name__ == "__main__":
    unittest.main()
