"""Finite tests for the handoff arithmetic helper, not tests of the project."""
import contextlib
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('capacity_plan', Path(__file__).with_name('capacity_plan.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.names = [f's{i:02d}' for i in range(11)]
        self.kinds = {n: 'process' for n in self.names}

    def test_fixed_distribution(self):
        self.assertEqual(list(m.distribution(self.names, 2026092103, 864).values()),
                         [74, 82, 84, 82, 83, 59, 75, 75, 82, 89, 79])

    def test_order_independent(self):
        self.assertEqual(m.distribution(self.names, 2026092103, 864),
                         m.distribution(self.names[::-1], 2026092103, 864))

    def test_sum_and_seed_change(self):
        counts = m.distribution(self.names, 2026092104, 864)
        self.assertEqual(sum(counts.values()), 864)
        self.assertNotEqual(counts, m.distribution(self.names, 2026092103, 864))

    def test_arithmetic_is_not_evidence(self):
        report = m.plan(self.names, self.kinds, 'a'*64, seed=2026092103,
                        rounds=864, compute_name='s05', subinputs=2048)
        self.assertEqual(report['planned_generated_slots'], 120832)
        self.assertTrue(report['arithmetic_reachable_if_all_complete_and_distinct'])
        self.assertIsNone(report['actual_completed_distinct_inputs'])
        self.assertIsNone(report['observation_seconds_verified'])
        self.assertFalse(report['activation_authorized'])

    def test_insufficient(self):
        report = m.plan(self.names, self.kinds, 'a'*64, seed=2026092103,
                        rounds=864, compute_name='s05', subinputs=1)
        self.assertFalse(report['arithmetic_reachable_if_all_complete_and_distinct'])

    def test_booleans_and_limits_rejected(self):
        for seed, rounds in ((True, 864), (0, 864), (2**63, 864), (1, True), (1, 0), (1, 100001)):
            with self.subTest(seed=seed, rounds=rounds), self.assertRaises(ValueError):
                m.distribution(self.names, seed, rounds)
        for count in (True, 0, 4097):
            with self.subTest(count=count), self.assertRaises(ValueError):
                m.plan(self.names, self.kinds, 'a'*64, seed=1, rounds=864, compute_name='s05', subinputs=count)

    def test_names_rejected(self):
        for names in ([], ['x', 'x'], ['x/y'], [None], ['x']*257):
            with self.subTest(names=names[:2]), self.assertRaises(ValueError):
                m.distribution(names, 1, 864)

    def test_wrong_compute(self):
        for name, kinds in (('missing', self.kinds), ('s05', dict(self.kinds, s05='pytest'))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.plan(self.names, kinds, 'a'*64, seed=1, rounds=864, compute_name=name, subinputs=2048)

    def test_capped_duplicate_invalid_and_no_code_execution(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'manifest.json'
            for raw in (b'{"scenarios":[],"scenarios":[]}', b'{"scenarios":NaN}', b'[]', b'\xff'):
                p.write_bytes(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    m.read_manifest(p)
            raw = json.dumps({'scenarios':[{'name':'safe', 'kind':'process', 'code':'raise AssertionError("MUST_NOT_EXECUTE")'}]}).encode()
            p.write_bytes(raw)
            self.assertEqual(m.read_manifest(p), (['safe'], {'safe':'process'}, sha256(raw).hexdigest()))
            before = m.MAX_MANIFEST_BYTES
            try:
                m.MAX_MANIFEST_BYTES = 1
                with self.assertRaises(ValueError): m.read_manifest(p)
            finally:
                m.MAX_MANIFEST_BYTES = before

    def test_cli_does_not_change_input(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'manifest.json'
            raw = json.dumps({'scenarios':[{'name':n, 'kind':'process','code':'print(1)'} for n in self.names]}).encode()
            p.write_bytes(raw)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = m.main(['--manifest', str(p), '--compute-name','s05'])
            self.assertEqual(code,0)
            self.assertEqual(json.loads(output.getvalue())['compute_rounds'],59)
            self.assertEqual(p.read_bytes(),raw)
            self.assertEqual(list(Path(td).iterdir()),[p])


if __name__ == '__main__':
    unittest.main()
