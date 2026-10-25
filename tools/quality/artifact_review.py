"""Evidence-backed custody decisions in the existing lifecycle ledger.

Reviews are observations, never resource adoption or deletion authority.
"""

import json
from pathlib import Path

from tools.quality.artifact_lifecycle import Run, file_checksum, mutation, runs, utc
from tools.quality.artifact_registry import unlinked
from tools.quality.artifact_policy import resource_key


OUTCOMES = frozenset({"active", "retained", "open", "completed", "superseded", "unresolved"})


def subjects(report):
    """Include exact producer declarations rather than trusting index-only fields."""
    producers = {}
    for run in report.get("lifecycle", []):
        for item in run.get("resources", []):
            if not item.get("identity"):
                continue
            producers.setdefault(resource_key(item, run.get("engine")), []).append(
                {"run_id": run["run_id"], "task_ref": run["task_ref"], "source": run.get("source"), "data_scope": item.get("data_scope")}
            )
    engine = report.get("scope", {}).get("docker_engine")
    return {
        item["id"]: {
            "id": item["id"],
            "identity": item["identity"],
            "kind": item["kind"],
            "engine": engine if item["kind"] in {"container", "volume", "network", "image"} else None,
            "data_scope": item.get("data_scope"),
            "source": item.get("source"),
            "producers": sorted(producers.get(resource_key(item, engine), []), key=lambda entry: entry["run_id"]),
        }
        for item in report.get("records", [])
    }


def validate_closure(path, decision, records, bound_subjects):
    """Bind an explicit acceptance statement to the observed artifact generation."""
    path = Path(path)
    unlinked(path)
    with path.open("rb") as stream:
        content = stream.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError("Closure statement exceeds 1 MiB")
    proof = json.loads(content)
    item = records[decision["id"]]
    expected = bound_subjects[item["id"]]
    if not isinstance(proof, dict) or proof.get("schema") != 1 or proof.get("project") != "ragflow":
        raise ValueError("Invalid artifact closure statement")
    if proof.get("artifact") != expected or proof.get("outcome") != decision["outcome"] or proof.get("accepted") is not True:
        raise ValueError("Closure must accept this exact artifact, scope and source")
    origin = decision.get("origin_owner")
    if not isinstance(origin, str) or not origin.strip() or proof.get("task_ref") != origin:
        raise ValueError("Closure requires the original task, distinct from custody")
    if expected["producers"] and origin not in {entry["task_ref"] for entry in expected["producers"]}:
        raise ValueError("Closure task differs from the recorded producer")
    if decision["outcome"] == "superseded":
        successor = records.get(decision.get("successor"))
        if successor is None or successor["id"] == item["id"]:
            raise ValueError("Supersession requires a distinct observed successor")
        expected_successor = bound_subjects[successor["id"]]

        def scopes(subject):
            return {entry["data_scope"] for entry in subject["producers"] if entry["data_scope"] is not None} | ({subject["data_scope"]} if subject["data_scope"] is not None else set())

        if scopes(expected_successor) != scopes(expected) or expected_successor["engine"] != expected["engine"] or proof.get("successor") != expected_successor:
            raise ValueError("Successor must match the accepted generation and data scope")
    return proof


def record(store, inventory, decisions):
    if not inventory.get("complete"):
        raise ValueError("A complete inventory is required")
    records = {item["id"]: item for item in inventory["records"]}
    if len(records) != len(inventory["records"]) or not isinstance(decisions, list) or not decisions:
        raise ValueError("Review requires unique inventory identities and nonempty decisions")
    assessments = []
    seen = set()
    existing_runs = {run["run_id"]: run for run in inventory.get("lifecycle", [])}
    existing_runs.update({run["run_id"]: run for run in runs(store)})
    bound_subjects = subjects({**inventory, "lifecycle": list(existing_runs.values())})
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(decision.get("id"), str) or decision["id"] not in records:
            raise ValueError("Review must reference an observed artifact")
        item = records[decision["id"]]
        if decision["id"] in seen or not isinstance(decision.get("outcome"), str) or decision["outcome"] not in OUTCOMES:
            raise ValueError("Duplicate artifact or invalid review outcome")
        seen.add(decision["id"])
        if (
            not all(isinstance(decision.get(key), str) and decision[key].strip() for key in ("owner", "purpose", "reason"))
            or not isinstance(decision.get("evidence"), list)
            or not decision["evidence"]
        ):
            raise ValueError("Review requires owner, purpose, reason and evidence")
        if decision["outcome"] == "superseded" and (not isinstance(decision.get("successor"), str) or not decision["successor"].strip()):
            raise ValueError("Supersession requires an explicit successor")
        paths = decision["evidence"][:]
        if decision["outcome"] in {"completed", "superseded"}:
            if not isinstance(decision.get("closure"), str) or not decision["closure"].strip():
                raise ValueError("Completed/superseded review requires subject-bound closure evidence")
            validate_closure(decision["closure"], decision, records, bound_subjects)
            paths.append(decision["closure"])
        if not all(isinstance(path, str) and path.strip() for path in paths):
            raise ValueError("Evidence paths must be nonempty strings")
        evidence = [{"path": str(Path(path).absolute()), "sha256": file_checksum(Path(path))} for path in dict.fromkeys(paths)]
        assessments.append(
            {
                **decision,
                "closure": str(Path(decision["closure"]).absolute()) if decision.get("closure") else None,
                "identity": item["identity"],
                "kind": item["kind"],
                "evidence": evidence,
                "reviewed_at": utc(),
            }
        )
    with mutation(store):
        previous = runs(store)
        graph = {
            entry["id"]: entry["successor"]
            for entry in declarations({"lifecycle": previous, "records": inventory["records"]}).values()
            if entry.get("outcome") == "superseded" and entry.get("successor")
        }
        graph.update({entry["id"]: entry["successor"] for entry in assessments if entry["outcome"] == "superseded"})
        for entry in assessments:
            visited, current = set(), entry["id"]
            while current in graph:
                if current in visited:
                    raise ValueError("Supersession cycle is not a replacement")
                visited.add(current)
                current = graph[current]
        sequence = max((run.get("review_sequence", 0) for run in previous), default=0) + 1
        with Run("artifact-queue-review", store=store, category="metadata") as run:
            run.data.update(assessments=assessments, review_sequence=sequence, purpose="Historical artifact custody and relevance review")
    return run.data["run_id"]


def declarations(report):
    result = {}
    checked = {}
    records = {item["id"]: item for item in report.get("records", [])}
    bound_subjects = subjects(report)
    orders = {}
    decisions = {}
    for run in sorted(report.get("lifecycle", []), key=lambda item: (item.get("review_sequence", 0), item["created_at"], item["run_id"])):
        for decision in run.get("assessments") or []:
            valid = True
            for evidence in decision["evidence"]:
                path = evidence["path"]
                if path not in checked:
                    try:
                        checked[path] = file_checksum(Path(path))
                    except (OSError, ValueError):
                        checked[path] = None
                valid &= checked[path] == evidence["sha256"]
            closure_valid = False
            if decision.get("closure") and valid:
                try:
                    validate_closure(decision["closure"], decision, records, bound_subjects)
                    closure_valid = True
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            order = ("sequence", run["review_sequence"]) if run.get("review_sequence") else ("legacy", run["created_at"])
            conflict = orders.get(decision["id"]) == order and (result[decision["id"]]["order_conflict"] or decisions[decision["id"]] != decision)
            result[decision["id"]] = {**decision, "evidence_valid": bool(valid and not conflict), "closure_valid": closure_valid and not conflict, "order_conflict": conflict}
            orders[decision["id"]] = order
            decisions[decision["id"]] = decision
    return result


def main():
    import argparse

    from tools.quality.artifact_lifecycle import store_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--state", type=Path, default=store_path())
    args = parser.parse_args()
    inventory = json.loads((args.state / "inventory.json").read_bytes())
    decisions = json.loads(args.plan.read_bytes())
    print(json.dumps({"run_id": record(args.state, inventory, decisions), "reviewed": len(decisions)}))


if __name__ == "__main__":
    main()
