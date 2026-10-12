"""An unavailable second encoder must fail before changing the tested dataset."""

import importlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test/testcases"))
try:
    scenario = importlib.import_module("test.testcases.restful_api.test_datasets").test_dataset_update_embedding_model_with_existing_chunks_contract
finally:
    sys.path.pop(0)


CURRENT = "bge-m3:latest@Local@Ollama"
ALTERNATIVE = "BAAI/bge-small-en-v1.5@default@Builtin"


class Response:
    status_code = 200

    def __init__(self, data=None, code=0):
        self.payload = {"code": code, "data": data}

    def json(self):
        return self.payload


class EmbeddingClient:
    def __init__(self, *, alternative=True, encoder_available=True):
        self.alternative = alternative
        self.encoder_available = encoder_available
        self.models = {"original": CURRENT, "probe": CURRENT}
        self.encoded = []

    def get(self, path, **kwargs):
        if path == "/models":
            models = [{"name": "bge-m3:latest", "instance_name": "Local", "provider_name": "Ollama"}]
            # A second instance of the same weights does not satisfy the test.
            models.append({"name": "bge-m3:latest", "instance_name": "Alias", "provider_name": "Ollama"})
            if self.alternative:
                models.append({"name": "BAAI/bge-small-en-v1.5", "instance_name": "default", "provider_name": "Builtin"})
            return Response(models)
        if path == "/datasets/original":
            return Response({"embedding_model": self.models["original"]})
        raise AssertionError(path)

    def post(self, path, **kwargs):
        if path == "/datasets/probe/documents":
            return Response([{"id": "probe_doc"}])
        if path.endswith("/chunks"):
            dataset = path.split("/")[2]
            if dataset == "probe" and not self.encoder_available:
                return Response(code=102)
            self.encoded.append(self.models[dataset])
            return Response({"chunk": {"id": "chunk_id"}})
        raise AssertionError(path)

    def put(self, path, *, json):
        dataset = path.split("/")[2]
        self.models[dataset] = json["embedding_model"]
        return Response({"embedding_model": json["embedding_model"]})


class EmbeddingSwitchContractTests(unittest.TestCase):
    def run_scenario(self, client):
        with tempfile.TemporaryDirectory() as directory:
            scenario(client, lambda name: ("original", "original_doc"), lambda name: "probe", Path(directory))

    def test_distinct_real_encoder_is_probed_before_original_switch(self):
        client = EmbeddingClient()
        self.run_scenario(client)
        self.assertEqual(client.encoded, [CURRENT, ALTERNATIVE])
        self.assertEqual(client.models["original"], ALTERNATIVE)

    def test_only_alias_of_same_model_fails_prerequisite(self):
        client = EmbeddingClient(alternative=False)
        with self.assertRaisesRegex(AssertionError, "two distinct available embedding models"):
            self.run_scenario(client)
        self.assertEqual(client.models["original"], CURRENT)

    def test_unavailable_encoder_fails_before_original_switch(self):
        client = EmbeddingClient(encoder_available=False)
        with self.assertRaises(AssertionError):
            self.run_scenario(client)
        self.assertEqual(client.models["original"], CURRENT)


if __name__ == "__main__":
    unittest.main()
