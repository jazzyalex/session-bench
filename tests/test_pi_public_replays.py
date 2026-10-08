"""Public Pi evidence locators stay tied to their independent sources."""
import unittest

import tempfile
from pathlib import Path

from scripts.build_pi_public_replays import _all_resolved, _metric_observer_ids, _native_locators


class PiPublicReplayTests(unittest.TestCase):
    def test_reconciliation_binds_only_the_observed_usage_total(self):
        # Reconciliation is native-attested; an RPC stats receipt is not its basis.
        observer = {
            "events": [{"id": "usage-rpc-total", "kind": "usage_total"}],
            "relations": [],
        }
        self.assertEqual(
            _metric_observer_ids(observer, "attribution.reconciliation"),
            ["usage-rpc-total"],
        )
        with self.assertRaisesRegex(ValueError, "missing or ambiguous independent observer locator"):
            _metric_observer_ids(observer, "work.actions")

    def test_every_portable_metric_binds_the_native_session_file_first(self):
        with tempfile.TemporaryDirectory() as folder:
            packet = Path(folder)
            for name in ("root-capture.json", "before-r1/inventory.json", "after-r1/inventory.json", "after-r2/inventory.json"):
                target = packet / "inputs/capture/native-root" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}")
            for metric in ("portable.complete_root", "portable.companions", "portable.isolated_decode", "portable.canonical_equality"):
                locators = _native_locators(packet, metric, b"native session bytes")
                self.assertEqual(locators[0]["artifact_id"], "native:session.jsonl")
                self.assertEqual(len(locators), 5)

    def test_a_native_absent_metric_is_a_resolved_state(self):
        metrics = [{"id": f"metric.{i}", "state": "measured"} for i in range(31)]
        metrics[0]["state"] = "native_absent"
        self.assertTrue(_all_resolved(metrics))

    def test_an_unresolved_metric_blocks_the_public_candidate(self):
        metrics = [{"id": f"metric.{i}", "state": "measured"} for i in range(31)]
        metrics[0]["state"] = "unresolved"
        self.assertFalse(_all_resolved(metrics))


if __name__ == "__main__":
    unittest.main()
