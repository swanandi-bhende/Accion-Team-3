"""The demo script runs end to end in offline rehearsal mode (no LLM)."""

import asyncio
import contextlib
import io
import unittest
from unittest.mock import patch

import demo_decision_engine

_real_sleep = asyncio.sleep


async def _fast_sleep(_seconds):
    await _real_sleep(0)


class DemoOfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_rehearsal_shows_both_cases(self):
        output = io.StringIO()
        with patch("runtime.runtime.asyncio.sleep", new=_fast_sleep), \
                contextlib.redirect_stdout(output):
            exit_code = await demo_decision_engine.run(offline=True, timeout_seconds=10)

        text = output.getvalue()
        self.assertEqual(exit_code, 0, text)
        self.assertIn("OFFLINE REHEARSAL", text)
        # Case 1: passes the capability check and is approved.
        self.assertIn("capability check (code): PASS", text)
        self.assertIn("APPROVED", text)
        # Case 2: fails the capability check; no private LLM call.
        self.assertIn("capability check (code): FAIL", text)
        self.assertIn("Step 2  skipped", text)
        # Memory: the second cycle re-judges nothing.
        self.assertIn("all already decided", text)
        self.assertIn("LLM requests: 2 (shared reading: 1, private judgement: 1)", text)
        self.assertIn("Handed to bidding: SIM-TEXT-001", text)
        self.assertIn("Final agent state: INACTIVE", text)


if __name__ == "__main__":
    unittest.main()
