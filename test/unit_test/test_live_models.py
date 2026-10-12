"""Provisioning checks use a synthetic disposable tenant and no network."""

import os
import unittest
from unittest.mock import patch

from test.live_models import configure_ollama


class LiveModelProvisioningTests(unittest.TestCase):
    def provision(self, *, mismatch=False, fail=False):
        calls = []
        defaults = {"tenant_id": "disposable"}

        def request(url, *, method="GET", payload=None, **kwargs):
            calls.append((url, method, payload))
            if fail:
                return 200, {"code": 102}
            if url.endswith("/users/me/models"):
                if method == "PATCH":
                    if not mismatch:
                        defaults.update(payload)
                    return 200, {"code": 0}
                return 200, {"code": 0, "data": defaults.copy()}
            return 200, {"code": 0}

        with patch.dict(os.environ, {"RAGFLOW_TEST_DISPOSABLE": "1"}):
            configure_ollama("http://disposable.invalid:9380", "synthetic-auth", "http://ollama.invalid:11434", request)
        return calls

    def test_both_model_contracts_and_tenant_defaults_are_configured(self):
        calls = self.provision()
        legacy = [payload for url, method, payload in calls if url.endswith("/v1/llm/add_llm")]
        self.assertEqual({item["llm_name"] for item in legacy}, {"qwen3.8:latest", "qwen3.6:27b", "bge-m3:latest"})
        self.assertEqual(calls[-1][1], "GET")
        self.assertTrue(calls[-1][0].endswith("/users/me/models"))

    def test_default_write_without_readback_match_fails(self):
        with self.assertRaisesRegex(ValueError, "did not retain"):
            self.provision(mismatch=True)

    def test_prerequisite_failure_is_not_ignored(self):
        with self.assertRaisesRegex(ValueError, "configuration failed"):
            self.provision(fail=True)

    def test_non_disposable_call_fails_before_mutation(self):
        with patch.dict(os.environ, {"RAGFLOW_TEST_DISPOSABLE": "0"}):
            with self.assertRaisesRegex(ValueError, "DISPOSABLE=1"):
                configure_ollama("http://disposable.invalid", "synthetic-auth", "http://ollama.invalid", lambda *a, **kw: self.fail("network attempted"))


if __name__ == "__main__":
    unittest.main()
