"""Lifecycle assessment for every artifact class, independent of deletion authority."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Review intervals schedule a decision; they never authorize deletion by age.
POLICIES = {
    "disposable": (2, "task-owner", "temporary execution", "completed run, no live lease/consumer, recovery preserved", "managed-retirement"),
    "cache": (7, "build-owner", "reuse reproducible results", "unused generation, complete rebuild recipe, no consumers/pins", "owning-cache-cleaner"),
    "build": (14, "build-owner", "candidate validation and delivery", "candidate replaced or task closed, source/receipts retained, no consumers", "managed-retirement"),
    "image": (14, "image-owner", "execution or rollback", "no containers, pins or open build users; image recovery verified", "managed-retirement"),
    "release": (30, "deployment-owner", "active installation and selected rollback", "not active/fallback, replacement accepted, recovery window closed", "deployment-owner"),
    "backup": (30, "backup-owner", "restore a specific data generation", "explicit release, verified replacement for same data scope, no restore/rollback dependents", "backup-owner"),
    "archive": (30, "task-owner", "resume work and retain unique evidence", "task closed, unique material replaced or released, no recovery dependents", "archive-owner"),
    "evidence": (30, "task-owner", "acceptance and diagnosis", "acceptance/diagnosis closed, final evidence retained, explicit release", "archive-owner"),
    "recovery": (30, "recovery-owner", "restore retired resources", "explicit release plus 90 days, no shared packet/blob dependents", "recovery-gc"),
    "runtime": (30, "service-owner", "running service state", "service decommissioned, data/config exported and verified, no consumers", "service-owner"),
    "data": (30, "data-owner", "persistent application data", "data owner closes retention, verified export/replacement, no consumers", "data-owner"),
    "dataset": (30, "quality-owner", "repeatable acceptance corpus", "no supported acceptance depends on it; verified replacement or explicit retirement", "quality-owner"),
    "model": (30, "model-owner", "inference and repeatable qualification", "no active model consumer/pin; reproducible exact weights or verified archive", "model-owner"),
    "config": (30, "service-owner", "service configuration and recovery", "unused generation, verified replacement and no restore dependencies", "service-owner"),
    "log": (14, "task-owner", "diagnosis and execution evidence", "diagnosis closed and required final evidence retained", "log-owner"),
    "tool": (30, "toolchain-owner", "build and QA toolchain", "version no longer required by recipes, active jobs or recovery", "toolchain-owner"),
    "metadata": (30, "artifact-owner", "ownership and reproducibility receipts", "superseded checkpoint, no audit/recovery/cache dependents", "metadata-owner"),
    "unknown": (7, "repository-maintainer", "purpose must be established", "identify owner, purpose, dependencies and retirement authority first", "unassigned"),
}
CATEGORIES = frozenset(POLICIES)
PRESERVED_OUTPUT_CLASSES = CATEGORIES - {"disposable", "cache", "unknown"}


def artifact_class(record):
    declared = record.get("category")
    if declared in CATEGORIES and declared != "evidence":
        return declared
    location = (record.get("path") or "").replace("\\", "/").lower()
    if "/archives/artifact-lifecycle" in location:
        return "recovery"
    if declared == "evidence":
        return "log" if location.endswith((".log", ".logs.zip")) else "archive"
    if declared == "task":
        return "disposable"
    if declared == "build-output":
        return "build"
    if record["kind"] == "build-cache":
        return "cache"
    if record["kind"] == "image":
        return "release" if any(Path(p).name == "deployment.json" for p in record.get("receipts", [])) else "image"
    if record["kind"] == "volume":
        return "data"
    if record["kind"] in {"container", "network"}:
        return "runtime"
    return "unknown"


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def resource_key(resource, engine=None):
    """The same exact-generation boundary for custody and producer declarations."""
    if resource.get("path"):
        return "path", resource["path"], tuple(resource["identity"])
    identity = resource["identity"]
    if resource["kind"] == "volume":
        identity = tuple(identity.get(key) for key in ("Name", "CreatedAt", "Driver"))
    return engine, resource["kind"], identity


def assess(record, *, now, previous=None, declaration=None):
    previous, declaration = previous or {}, declaration or {}
    kind = artifact_class({**record, **({"category": declaration["category"]} if declaration.get("category") else {})})
    days, role, purpose, retirement, executor = POLICIES[kind]
    first = previous.get("first_observed_at") if previous.get("assessment_version") == 1 else now.isoformat()
    first = first or now.isoformat()
    reviewed = declaration.get("reviewed_at") or previous.get("reviewed_at")
    anchor = reviewed or record.get("created_at") or previous.get("review_anchor") or previous.get("first_observed_at") or record.get("modified_at") or first
    deadline = timestamp(anchor) + timedelta(days=days)
    owner = declaration.get("task_ref")
    blockers = list(record.get("blockers", []))
    if not owner:
        blockers.append("owner-and-purpose-review-required")
    if record.get("shared"):
        blockers.append("shared-resource-needs-all-consumers")
        role = "docker-engine-maintainer"
    incomplete = declaration.get("validation") == "incomplete" or declaration.get("run_state") == "failed"
    if incomplete:
        blockers.append("incomplete-artifact-needs-diagnosis")
    state = "retained"
    if record.get("running") or "task-pin" in blockers:
        state = "active"
    elif not owner or deadline <= now:
        state = "review-required"
    if incomplete:
        state = "diagnosis"
    if record.get("status") == "removed":
        state = "retired"
    elif record.get("source_observed_exists") is False and record.get("preserved_evidence"):
        state = "archived"
    next_action = (
        "resolve incomplete artifact and verify replacement"
        if incomplete
        else "identify owner and purpose"
        if not owner
        else "verify continued need, consumers and retirement conditions"
        if deadline <= now
        else "retain until next review or dependency change"
    )
    return {
        "class": kind,
        "assessment_version": 1,
        "state": state,
        "owner": owner,
        "origin_owner": declaration.get("origin_owner", owner if declaration.get("ownership_source") == "producer" else None),
        "custodian": declaration.get("custodian"),
        "custodians": declaration.get("custodians", []),
        "ownership_source": declaration.get("ownership_source", "unknown"),
        "responsible_role": role,
        "purpose": declaration.get("purpose") or purpose,
        "purpose_confirmed": bool(declaration.get("purpose")),
        "data_scope": declaration.get("data_scope"),
        "validation": declaration.get("validation", "unverified"),
        "task_status": declaration.get("task_status"),
        "first_observed_at": first,
        "reviewed_at": reviewed,
        "review_anchor": anchor,
        "review_after": deadline.isoformat(),
        "review_due": deadline <= now,
        "review_required": state not in {"retired", "archived"} and (incomplete or not owner or deadline <= now),
        "retention_until": retirement,
        "retirement_executor": executor,
        "deletion_authorized": False,
        "depends_on": declaration.get("depends_on", []),
        "retained_by": sorted(set(record.get("consumers", []) + record.get("receipts", []) + declaration.get("retained_by", []))),
        "next_action": next_action,
        "blockers": sorted(set(blockers)),
    }


def apply(report, previous=None):
    from tools.quality.artifact_review import declarations as reviewed_declarations

    now = timestamp(report["observed_at"])
    before = {r["id"]: r.get("lifetime", {}) for r in (previous or {}).get("records", [])}
    # Match declarations to the exact path generation; a reused path is not the old resource.
    declarations = {}
    observations = {}
    for run in sorted(report.get("lifecycle", []), key=lambda r: r["created_at"]):
        for resource in run.get("resources", []):
            policy = {
                "task_ref": run["task_ref"],
                "ownership_source": "producer",
                "purpose": run.get("purpose") or run.get("producer"),
                "category": resource.get("category"),
                "reviewed_at": run["created_at"],
                "depends_on": resource.get("depends_on", []),
                "retained_by": resource.get("retained_by", []),
            }
            policy.update(data_scope=resource.get("data_scope"), validation=resource.get("validation", "unverified"), run_state=run.get("state"), task_status=run.get("task_status"))
            if resource.get("identity"):
                declarations[resource_key(resource, run.get("engine"))] = policy
            resource["lifetime"] = assess({**resource, "created_at": run["created_at"]}, now=now, declaration=policy)
        for observed in [*(run.get("runtime_observations") or []), *(run.get("input_files") or [])]:
            if not observed.get("identity"):
                continue
            custodian = observed.get("custodian") or run.get("runtime_project")
            if not custodian:
                continue
            policy = {
                "task_ref": "runtime:" + custodian,
                "custodian": custodian,
                "ownership_source": "observation",
                "category": observed.get("category"),
                "purpose": "Persistent runtime or recovery dependency",
                "retained_by": ["runtime-run:" + run["run_id"]],
            }
            observations.setdefault(resource_key(observed, run.get("engine")), []).append(policy)
            observed["lifetime"] = assess({"created_at": run["created_at"], **observed}, now=now, declaration=policy)
    reviews = reviewed_declarations(report)
    queue = []
    for record in report["records"]:
        key = resource_key(record, report.get("scope", {}).get("docker_engine"))
        declaration = declarations.get(key)
        if not declaration and (sandbox := record.get("sandbox")):
            declaration = {
                "task_ref": "sandbox-manager:" + sandbox["manager"] + "@" + sandbox["epoch"],
                "ownership_source": "producer",
                "category": "disposable",
                "purpose": "Sandbox " + sandbox["kind"] + " child; pool " + sandbox["pool"],
                "depends_on": [sandbox["manager"]],
            }
        if users := observations.get(key):
            custodians = sorted({user["custodian"] for user in users})
            declaration = {
                **(declaration or {**users[0], "task_ref": "runtime:" + custodians[0]}),
                "custodian": (declaration or {}).get("custodian") or custodians[0],
                "custodians": custodians,
                "retained_by": sorted({ref for user in users for ref in user["retained_by"]} | set((declaration or {}).get("retained_by", []))),
            }
        review = reviews.get(record["id"])
        if review and review.get("closure_valid") and declaration and declaration.get("ownership_source") == "producer" and review.get("origin_owner") != declaration["task_ref"]:
            review = {**review, "closure_valid": False, "origin_conflict": True}
        if review and review["identity"] == record["identity"] and review["evidence_valid"]:
            declaration = {
                **(declaration or {}),
                "task_ref": (declaration or {}).get("task_ref") or review["owner"],
                "custodian": review["owner"],
                "ownership_source": (declaration or {}).get("ownership_source", "custody"),
                "purpose": review["purpose"],
                "reviewed_at": review["reviewed_at"],
            }
            if review.get("closure_valid"):
                declaration["origin_owner"] = review["origin_owner"]
        record["lifetime"] = assess(record, now=now, previous=before.get(record["id"]), declaration=declaration)
        if record.get("sandbox"):
            record["lifetime"]["retirement_executor"] = "sandbox-manager-and-stopped-orphan-cleaner"
        if review:
            record["lifetime"]["review"] = review
            if not review["evidence_valid"] or review["identity"] != record["identity"]:
                record["lifetime"].update(review_required=True, next_action="revalidate changed review evidence or resource generation")
            elif review["outcome"] == "unresolved":
                record["lifetime"].update(review_required=True, next_action=review["reason"])
            elif review["outcome"] in {"completed", "superseded"} and not review.get("closure_valid"):
                record["lifetime"].update(review_required=True, next_action="verify subject-bound closure, original task and accepted successor")
        if record["lifetime"]["review_required"]:
            queue.append(
                {
                    "id": record["id"],
                    "class": record["lifetime"]["class"],
                    "owner": record["lifetime"]["owner"],
                    "locator": record.get("path") or record.get("locator") or record.get("name") or record.get("tags") or record["id"],
                    "responsible_role": record["lifetime"]["responsible_role"],
                    "action": record["lifetime"]["next_action"],
                    "review_after": record["lifetime"]["review_after"],
                }
            )
    report["review_queue"] = sorted(queue, key=lambda item: (item["review_after"], item["id"]))
    report["lifetime_counts"] = dict(Counter(r["lifetime"]["class"] for r in report["records"]))
    return report
