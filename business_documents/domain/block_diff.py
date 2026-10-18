"""Stable block identity and edits against an immutable document snapshot."""

from copy import deepcopy
from typing import Any

from business_documents.domain.errors import RuleViolation
from business_documents.domain.hashing import stable_hash


BLOCK_OPERATIONS = frozenset({"INSERT_BLOCK", "REPLACE_BLOCK", "DELETE_BLOCK", "ADD_SECTION"})


def with_block_ids(document: dict[str, Any]) -> dict[str, Any]:
    """Identify old blocks deterministically; retain identity across future edits."""
    result = deepcopy(document)
    used = set()
    for section in result.get("sections", []):
        if not isinstance(section, dict) or not isinstance(section.get("id"), str) or not isinstance(section.get("blocks"), list):
            continue
        for index, block in enumerate(section.get("blocks", [])):
            if not isinstance(block, dict):
                continue
            identifier = block.get("id")
            if "id" not in block:
                identifier = "b-" + stable_hash([section["id"], index, block]).removeprefix("sha256:")[:32]
                block["id"] = identifier
            if not isinstance(identifier, str) or not identifier or len(identifier) > 80 or identifier in used:
                raise RuleViolation("INVALID_BLOCK_ID", "Block IDs must be nonempty and unique within the document")
            used.add(identifier)
    return result


def bind_block_operation(operation: dict[str, Any], base_revision_id: str) -> None:
    """The server assigns insert identity and binds replacement identity."""
    block = operation.get("block")
    if not isinstance(block, dict):
        return
    if operation.get("type") == "INSERT_BLOCK":
        identifier = "b-" + stable_hash([base_revision_id, operation.get("operation_id")]).removeprefix("sha256:")[:32]
    else:
        identifier = operation.get("block_id")
    if "id" in block and block["id"] != identifier:
        raise RuleViolation("BLOCK_ID_CONFLICT", "An operation cannot change or choose block identity")
    block["id"] = identifier


def apply_block_operations(base: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve all targets against the base, then assemble each section once."""
    result = with_block_ids(base)
    sections = {section["id"]: section for section in result["sections"]}
    hashes = {}
    section_targets = {identifier: {block["id"] for block in section["blocks"]} for identifier, section in sections.items()}
    block_ids = {block["id"] for section in sections.values() for block in section["blocks"]}
    edits, additions, operation_ids = {}, {}, set()
    for operation in operations:
        kind, section_id = operation["type"], operation["section_id"]
        operation_id = operation["operation_id"]
        if operation_id in operation_ids:
            raise RuleViolation("DUPLICATE_CHANGE_OPERATION", "Operation IDs must be unique")
        operation_ids.add(operation_id)
        section = sections.get(section_id)
        if section_id not in hashes:
            hashes[section_id] = stable_hash(section)
        expected = hashes[section_id]
        if operation["expected_section_hash"] != expected:
            raise RuleViolation("SECTION_HASH_CONFLICT", "Change plan targets stale section content", {"section_id": section_id})
        if kind == "ADD_SECTION":
            if section is not None or section_id in additions:
                raise RuleViolation("DUPLICATE_CHANGE_TARGET", "A section can only be added once")
            additions[section_id] = {"id": section_id, "title": operation["title"], "blocks": operation["content"]["blocks"], "evidence_refs": operation.get("evidence_refs", [])}
            continue
        if section is None:
            raise RuleViolation("CHANGE_SECTION_NOT_FOUND", "Change plan targets a missing section", {"section_id": section_id})
        targets = section_targets[section_id]
        state = edits.setdefault(section_id, {"replace": {}, "delete": set(), "insert": {}})
        if kind == "INSERT_BLOCK":
            anchor = operation["after_block_id"]
            if anchor is not None and anchor not in targets:
                raise RuleViolation("CHANGE_BLOCK_NOT_FOUND", "Insert anchor is outside the base section")
            block = operation["block"]
            if block["id"] in block_ids:
                raise RuleViolation("INVALID_BLOCK_ID", "Inserted block ID already exists")
            block_ids.add(block["id"])
            state["insert"].setdefault(anchor, []).append(block)
        else:
            target = operation["block_id"]
            if target not in targets:
                raise RuleViolation("CHANGE_BLOCK_NOT_FOUND", "Block is outside the base section", {"section_id": section_id, "block_id": target})
            if target in state["replace"] or target in state["delete"]:
                raise RuleViolation("DUPLICATE_CHANGE_TARGET", "A block can only be edited once")
            if kind == "REPLACE_BLOCK":
                if operation["block"].get("id") != target:
                    raise RuleViolation("BLOCK_ID_CONFLICT", "A replacement must preserve block identity")
                state["replace"][target] = operation["block"]
            elif kind == "DELETE_BLOCK":
                state["delete"].add(target)
            else:
                raise RuleViolation("INVALID_CHANGE_PLAN", "Unsupported block operation")
        if "evidence_refs" in operation:
            section["evidence_refs"] = list(dict.fromkeys([*section.get("evidence_refs", []), *operation["evidence_refs"]]))
    for section_id, state in edits.items():
        section = sections[section_id]
        blocks = list(state["insert"].get(None, []))
        for block in section["blocks"]:
            identifier = block["id"]
            if identifier not in state["delete"]:
                blocks.append(state["replace"].get(identifier, block))
            blocks.extend(state["insert"].get(identifier, []))
        section["blocks"] = blocks
    result["sections"].extend(additions.values())
    return with_block_ids(result)


def block_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Align only changed block pairs by stable identity for the preview."""
    old, new = ({block["id"]: block for block in with_block_ids({"sections": [section]})["sections"][0]["blocks"]} for section in (before, after))
    return [
        {
            "block_id": identifier,
            "kind": "replace" if identifier in old and identifier in new else "insert" if identifier in new else "delete",
            "before_block": old.get(identifier),
            "after_block": new.get(identifier),
        }
        for identifier in dict.fromkeys([*old, *new])
        if stable_hash(old.get(identifier)) != stable_hash(new.get(identifier))
    ]
