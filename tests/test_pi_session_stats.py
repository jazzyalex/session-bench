"""Synthetic validation for a read-only Pi session-stats RPC observation."""
import hashlib
import json
import unittest

from session_bench.pi_session_stats import (
    REQUEST_ID, reconciliation_candidate, validate_pi_session_stats,
)


class PiSessionStatsTests(unittest.TestCase):
    def setUp(self):
        self.session_id = "12345678-1234-4234-8234-123456789abc"
        self.filename = "2026-10-02T00-00-00-000Z_" + self.session_id + ".jsonl"
        self.session = (json.dumps({"type": "session", "version": 3, "id": self.session_id}) + "\n").encode()
        response = {
            "type": "response", "id": REQUEST_ID, "command": "get_session_stats", "success": True,
            "data": {"sessionFile": "/tmp/copy/" + self.filename, "sessionId": self.session_id,
                     "userMessages": 2, "assistantMessages": 4, "toolCalls": 2,
                     "toolResults": 2, "totalMessages": 10,
                     "tokens": {"input": 100, "output": 20, "cacheRead": 30,
                                "cacheWrite": 4, "total": 154}, "cost": 0.01,
                     "contextUsage": {"tokens": 25, "contextWindow": 1000, "percent": 2.5}},
        }
        self.stdout = (json.dumps(response) + "\n").encode()

    def test_valid_stats_are_pinned_to_session_hash_and_normalized(self):
        receipt = validate_pi_session_stats(self.stdout, session_bytes=self.session,
                                            session_filename=self.filename, pi_version="1.0.0")
        self.assertEqual(receipt["session_id"], self.session_id)
        self.assertEqual(receipt["session_sha256"], hashlib.sha256(self.session).hexdigest())
        candidate = reconciliation_candidate(receipt, session_id=self.session_id,
                    session_sha256=receipt["session_sha256"],
                    locator={"id": "stats", "sha256": "a" * 64})
        self.assertEqual(candidate["session_totals"], {
            "input_tokens": 100, "output_tokens": 20,
            "cache_read_tokens": 30, "cache_write_tokens": 4,
        })

    def test_rejects_wrong_identity_duplicate_responses_and_bad_totals(self):
        mutations = []
        wrong_id = json.loads(self.stdout)
        wrong_id["data"]["sessionId"] = "another"
        mutations.append(json.dumps(wrong_id).encode())
        mutations.append(self.stdout + self.stdout)
        wrong_total = json.loads(self.stdout)
        wrong_total["data"]["tokens"]["total"] = 999
        mutations.append(json.dumps(wrong_total).encode())
        for raw in mutations:
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                validate_pi_session_stats(raw, session_bytes=self.session,
                                          session_filename=self.filename, pi_version="1.0.0")

    def test_rejects_non_stats_commands_and_changed_native_binding(self):
        wrong_command = json.loads(self.stdout)
        wrong_command["command"] = "prompt"
        with self.assertRaises(ValueError):
            validate_pi_session_stats((json.dumps(wrong_command) + "\n").encode(),
                                      session_bytes=self.session, session_filename=self.filename,
                                      pi_version="1.0.0")
        receipt = validate_pi_session_stats(self.stdout, session_bytes=self.session,
                                            session_filename=self.filename, pi_version="1.0.0")
        with self.assertRaises(ValueError):
            reconciliation_candidate(receipt, session_id=self.session_id,
                                     session_sha256="0" * 64,
                                     locator={"id": "stats", "sha256": "a" * 64})


if __name__ == "__main__":
    unittest.main()
