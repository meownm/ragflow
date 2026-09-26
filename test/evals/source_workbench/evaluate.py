"""Score actual Source Workbench /search document rankings against fixed labels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


GOLDEN = Path(__file__).with_name("golden.json")
TOP_K = 5


def validate_gold(gold: dict) -> None:
    if gold.get("version") != 1 or not isinstance(gold.get("documents"), dict) or not isinstance(gold.get("cases"), list):
        raise ValueError("Gold must contain version 1, documents, and cases")
    aliases = set(gold["documents"])
    if len(aliases) < 2 or not all(isinstance(item, dict) and item.get("title") and item.get("text") for item in gold["documents"].values()):
        raise ValueError("Gold documents must contain at least two titled texts")
    cases = gold["cases"]
    if not cases or len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("Gold cases must have unique IDs")
    if not any(case.get("relevant") for case in cases) or not any(case.get("relevant") == [] for case in cases):
        raise ValueError("Gold needs positive and no-answer queries")
    for case in cases:
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError(f"Case {case.get('id')} has no query")
        relevant = case.get("relevant")
        if not isinstance(relevant, list) or len(relevant) != len(set(relevant)) or not set(relevant) <= aliases:
            raise ValueError(f"Case {case['id']} has invalid relevance labels")
    gates = gold.get("gates")
    if (
        not isinstance(gates, dict)
        or set(gates) != {"min_mean_reciprocal_rank_at_5", "min_mean_recall_at_5", "max_no_answer_result_rate"}
        or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in gates.values())
    ):
        raise ValueError("Gold must declare three thresholds between 0 and 1")


def validate_bindings(gold: dict, bindings: dict) -> None:
    documents = bindings.get("document_ids")
    if bindings.get("disposable") is not True or not isinstance(bindings.get("workspace_id"), str) or not bindings["workspace_id"]:
        raise ValueError("Bindings must identify an explicitly disposable workspace")
    if not isinstance(bindings.get("workspace_title"), str) or not bindings["workspace_title"].startswith("eval-source-workbench-"):
        raise ValueError("Bindings must name a dedicated eval-source-workbench-* workspace")
    if not isinstance(bindings.get("dataset_id"), str) or not bindings["dataset_id"]:
        raise ValueError("Bindings must identify the dedicated eval dataset")
    if not isinstance(documents, dict) or set(documents) != set(gold["documents"]):
        raise ValueError("Bindings must map every gold document alias exactly once")
    if any(not isinstance(item, str) or not item for item in documents.values()) or len(set(documents.values())) != len(documents):
        raise ValueError("Bound document IDs must be nonempty and unique")


def score_case(case: dict, candidates: list[dict], document_ids: dict[str, str]) -> dict:
    """Use the returned document order; duplicate chunks never get extra credit."""
    if not isinstance(candidates, list):
        raise ValueError("Search response candidates must be a list")
    ranked = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("document_id"), str) or not candidate["document_id"]:
            raise ValueError("Each candidate must contain a document_id")
        if candidate["document_id"] not in ranked:
            ranked.append(candidate["document_id"])
    relevant = {document_ids[alias] for alias in case["relevant"]}
    top = ranked[:TOP_K]
    if not relevant:
        return {"id": case["id"], "kind": "no_answer", "returned": len(ranked), "passed": not ranked}
    hits = [index for index, document_id in enumerate(top, 1) if document_id in relevant]
    return {
        "id": case["id"],
        "kind": "positive",
        "reciprocal_rank_at_5": 1 / hits[0] if hits else 0.0,
        "recall_at_5": len(hits) / len(relevant),
        "returned": len(ranked),
    }


def score_suite(gold: dict, bindings: dict, observations: dict[str, list[dict]]) -> dict:
    validate_gold(gold)
    validate_bindings(gold, bindings)
    if set(observations) != {case["id"] for case in gold["cases"]}:
        raise ValueError("Every gold case must have exactly one actual search observation")
    cases = [score_case(case, observations[case["id"]], bindings["document_ids"]) for case in gold["cases"]]
    positive = [case for case in cases if case["kind"] == "positive"]
    negative = [case for case in cases if case["kind"] == "no_answer"]
    metrics = {
        "mean_reciprocal_rank_at_5": sum(case["reciprocal_rank_at_5"] for case in positive) / len(positive),
        "mean_recall_at_5": sum(case["recall_at_5"] for case in positive) / len(positive),
        "no_answer_result_rate": sum(not case["passed"] for case in negative) / len(negative),
    }
    gates = gold["gates"]
    passed = (
        metrics["mean_reciprocal_rank_at_5"] >= gates["min_mean_reciprocal_rank_at_5"]
        and metrics["mean_recall_at_5"] >= gates["min_mean_recall_at_5"]
        and metrics["no_answer_result_rate"] <= gates["max_no_answer_result_rate"]
    )
    return {"status": "pass" if passed else "fail", "metrics": metrics, "cases": cases}


def _read_json(url: str, authorization: str, cookie: str, body: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, headers=headers, data=json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"Source Workbench HTTP request failed: {type(error).__name__}") from error
    if not isinstance(result, dict) or result.get("code") != 0 or not isinstance(result.get("data"), dict):
        raise RuntimeError("Source Workbench returned a non-success response")
    return result["data"]


def collect_live(base_url: str, gold: dict, bindings: dict, authorization: str, cookie: str) -> dict[str, list[dict]]:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Live eval accepts only a loopback disposable stack URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Base URL must contain only scheme, loopback host, and port")
    if not authorization and not cookie:
        raise ValueError("Set SOURCE_WORKBENCH_EVAL_AUTHORIZATION or SOURCE_WORKBENCH_EVAL_COOKIE")
    endpoint = f"{base_url.rstrip('/')}/api/v1/source-workspaces/{quote(bindings['workspace_id'], safe='')}"
    workspace = _read_json(endpoint, authorization, cookie)
    if workspace.get("title") != bindings["workspace_title"]:
        raise ValueError("Live workspace title does not match the dedicated eval binding")
    if workspace.get("dataset_ids") != [bindings["dataset_id"]]:
        raise ValueError("Live workspace must contain only the dedicated eval dataset")
    observations = {}
    for case in gold["cases"]:
        result = _read_json(f"{endpoint}/search", authorization, cookie, {"query": case["query"], "page": 1})
        if result.get("query") != case["query"] or result.get("page") != 1 or not isinstance(result.get("candidates"), list):
            raise RuntimeError(f"Unexpected search response for {case['id']}")
        observations[case["id"]] = result["candidates"]
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Loopback URL of a disposable RAGFlow stack")
    parser.add_argument("--bindings", type=Path, required=True, help="JSON with disposable workspace and actual document IDs")
    parser.add_argument("--output", type=Path, help="Optional JSON score report; credentials are never included")
    args = parser.parse_args()
    try:
        if os.environ.get("SOURCE_WORKBENCH_EVAL_DISPOSABLE") != "1":
            raise ValueError("Set SOURCE_WORKBENCH_EVAL_DISPOSABLE=1 only for an isolated stack")
        gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
        bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
        validate_gold(gold)
        validate_bindings(gold, bindings)
        observations = collect_live(
            args.base_url,
            gold,
            bindings,
            os.environ.get("SOURCE_WORKBENCH_EVAL_AUTHORIZATION", ""),
            os.environ.get("SOURCE_WORKBENCH_EVAL_COOKIE", ""),
        )
        report = score_suite(gold, bindings, observations)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Source Workbench live eval incomplete: {error}", file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
