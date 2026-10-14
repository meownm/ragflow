"""The SDK release scenario detects duplicate, missing and excess pages."""

from types import SimpleNamespace
import unittest

from ragflow_sdk import RAGFlow
from test.testcases.test_sdk_api.test_chunk_management_within_dataset.test_retrieval_chunks import TestChunksRetrieval as RetrievalScenario


class SDKRetrievalPagingContractTests(unittest.TestCase):
    def run_scenario(self, *, page_size=1, duplicate=False, missing=False, excess=False):
        client = RAGFlow("synthetic-token", "http://disposable.invalid")
        calls = []
        identities = [f"chunk-{index}" for index in range(4)]

        def post(path, *, json):
            self.assertEqual(path, "/retrieval")
            self.assertEqual(json["dataset_ids"], ["dataset"])
            calls.append(json.copy())
            page = json["page"]
            size = int(json["page_size"])
            first = (page - 1) * size
            selected = identities[first : first + size]
            if duplicate and page == 2:
                selected = [identities[0]]
            if missing and page == 3:
                selected = []
            if excess and page == 5:
                selected = ["unexpected"]
            return SimpleNamespace(json=lambda: {"code": 0, "data": {"total": len(identities), "chunks": [{"id": identity} for identity in selected]}})

        client.post = post
        chunks = [SimpleNamespace(id=identity) for identity in identities]
        RetrievalScenario().test_page_size(client, (SimpleNamespace(id="dataset"), None, chunks), {"page_size": page_size}, 1, "")
        return calls

    def test_integer_and_string_page_size_traverse_all_pages(self):
        for page_size in (1, "1"):
            with self.subTest(page_size=page_size):
                calls = self.run_scenario(page_size=page_size)
                self.assertEqual([call["page"] for call in calls], [1, 2, 3, 4, 5])
                self.assertTrue(all(call["page_size"] == page_size for call in calls))

    def test_duplicate_page_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_scenario(duplicate=True)

    def test_missing_page_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_scenario(missing=True)

    def test_page_after_exhaustion_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_scenario(excess=True)


if __name__ == "__main__":
    unittest.main()
