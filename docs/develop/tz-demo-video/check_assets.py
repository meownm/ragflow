"""Read-only checks of filming assets and strict comparison of an observed export.

This is an operator tool, not a semantic model evaluator or a live acceptance run.
Run from the repository with its Python environment; no credentials are required.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from api.technical_specifications.domain import HEADINGS, SpecificationError, markdown, merge_sections, parse_markdown  # noqa: E402


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def changed_sections(before, after):
    return {key for key in before if before[key] != after[key]}


def check_packet():
    checks = []
    source = read_json(HERE / "source-manifest.json")
    for name, expected_hash in source["files"].items():
        require(digest(REPO / name) == expected_hash, f"Source drift: {name}; review the packet against the current source")
    checks.append("source_hashes")

    assets = read_json(HERE / "assets-manifest.json")
    for name, expected_hash in assets["files"].items():
        require(digest(HERE / name) == expected_hash, f"Asset drift: {name}")
    require(
        set(assets["files"]) == {str(p.relative_to(HERE)).replace("\\", "/") for p in HERE.rglob("*") if p.is_file() and p.name not in {"assets-manifest.json", "validation.json"}},
        "Asset inventory changed",
    )
    checks.append("asset_hashes_and_inventory")

    stages = read_json(HERE / "stages.json")
    states = {name: parse_markdown((HERE / path).read_text(encoding="utf-8")) for name, path in stages.items()}
    for name, state in states.items():
        require(list(state) == list(map(str, range(1, 10))), f"Section order: {name}")
        require(parse_markdown(markdown(state)) == state, f"Parser roundtrip: {name}")
        require("### 4.1." in state["4"] and "### 4.2." in state["4"], f"Subsections missing: {name}")
    checks.append("ten_snapshots_production_parser_roundtrip")

    bt = (HERE / "inputs/01-business-requirements.md").read_text(encoding="utf-8")
    template = read_json(REPO / "agent/business_requirements/templates/business_requirements.v1.json")
    observed_headings = re.findall(r"^#{2,3} ([\d.]+)\. (.+)$", bt, re.MULTILINE)
    expected_headings = [(row["id"], row["title"]) for row in template["sections"]]
    require(observed_headings == expected_headings, "BT headings do not match the current template")
    ids = re.findall(r"^(BR-\d{2})\.", bt, re.MULTILINE)
    require(ids == [f"BR-{i:02}" for i in range(1, 8)], "BT requirement IDs must be unique and complete")
    require(bt.count("@startuml") == bt.count("@enduml") == 2, "BT diagram fence inventory")
    checks.append("bt_outline_seven_requirements_and_diagram_delimiters")

    initial, selection, complete = (states[name] for name in ("initial", "selection", "complete"))
    require(initial["5"].count("Выберите CSV.") == 1, "Selection quote must be unique")
    require(changed_sections(initial, selection) == {"5"}, "Selection changed another section")
    require(selection["5"] == initial["5"].replace("Выберите CSV.", "Выберите файл CSV с заявками."), "Selection changed other text")
    require(changed_sections(selection, complete) == {"2", "4", "5", "6", "7", "8", "9"}, "Completion scope changed")
    require("обработка повторов в текущей версии не определена" in initial["2"], "Initial gap marker missing")
    require("IDEMPOTENCY_TTL_SECONDS" not in markdown(initial), "Initial document already specifies replay TTL")
    for key in ("4", "5", "8"):
        require("20 МБ" in complete[key] and "50 МБ" not in complete[key], f"Complete limit: section {key}")
    for key, phrases in {
        "2": ["24 часа", "processing"],
        "4": ["IDEMPOTENCY_TTL_SECONDS = 86400", "CSV_MAX_BYTES = 20000000"],
        "5": ["UC-05", "UC-06", "UC-07", "Выберите файл CSV с заявками."],
        "6": ["SHA-256", "организации", "86400", "processing", "409", "202", "атомарно"],
        "7": ["UploadKey", "UNIQUE(`organization_id`, `key`)", "file_sha256", "expires_at"],
        "8": ["Idempotency-Key", "IDEMPOTENCY_KEY_REUSED", "INVALID_IDEMPOTENCY_KEY", "24 часов", "Location"],
        "9": ["не журналировать", "upload_replay", "CSV", "токены"],
    }.items():
        for phrase in phrases:
            require(phrase in complete[key], f"Expected fact not found in section {key}: {phrase}")
    checks.append("main_story_snapshots_and_fixed_facts")

    negative, recovered = (states[name] for name in ("negative-candidate", "negative-recovered"))
    require(changed_sections(complete, negative) == {"8"}, "Negative candidate changes more than section 8")
    require(negative["8"] == complete["8"].replace("Допускается размер файла до 20 МБ включительно.", "Допускается размер файла до 50 МБ включительно."), "Negative candidate has extra edits")
    require(changed_sections(complete, recovered) == {"8"}, "Recovery changes more than section 8")
    require(recovered["8"] == complete["8"].replace("Файл превышает допустимый размер.", "Размер CSV превышает 20 МБ."), "Recovery has extra edits")
    require(recovered != complete, "Recovery must create a real text change")
    require(recovered["8"].count("Размер CSV превышает 20 МБ.") == 2, "Recovery must fix table and JSON")
    checks.append("negative_candidate_and_non_noop_recovery")

    base, local, remote = (states[name] for name in ("merge-base", "merge-local", "merge-eva"))
    require(base == complete, "Merge base differs from completed document")
    raw = merge_sections(base, local, remote)
    require(raw["conflicts"], "Prepared B/L/R does not produce a real text conflict")
    for chosen, limit in (("merge-choose-local", "35"), ("merge-choose-eva", "50")):
        state = states[chosen]
        for key in ("4", "5", "8"):
            require(f"{limit} МБ" in state[key], f"Chosen limit missing in {chosen}/{key}")
            require(f"{'50' if limit == '35' else '35'} МБ" not in state[key], f"Mixed limits in {chosen}/{key}")
        require(f"CSV_MAX_BYTES = {limit}000000" in state["4"], f"Byte limit mismatch in {chosen}")
        require(state["4"].count("UPLOAD_BUSY_INDICATOR = true") == 1, f"Local independent edit lost/duplicated in {chosen}")
        require(state["4"].count("LOG_INCLUDE_DURATION_MS = true") == 1, f"Remote independent edit lost/duplicated in {chosen}")
        require("duration_ms" in state["9"], f"Remote journal edit lost in {chosen}")
        require(changed_sections(base, state) == {"4", "5", "8", "9"}, f"Unrelated merge changes in {chosen}")
    checks.append("production_three_way_conflict_and_both_authored_resolutions")

    prompts = read_json(HERE / "prompts.json")
    require([item["id"] for item in prompts] == [f"Q{i:02}" for i in range(1, 10)], "Prompt inventory")
    prose = (HERE / "requests.md").read_text(encoding="utf-8")
    for item in prompts:
        require(item["text"] in prose, f"Prompt JSON/Markdown mismatch: {item['id']}")
    session = read_json(HERE / "session-template.json")
    require(session["status"] == "NOT_RUN" and session["recording_authorized"] is False, "Template must not imply a real run or recording permission")
    require(set(session["acceptance"].values()) == {"NOT_RUN"}, "Template contains fabricated acceptance")
    require(not session["events"], "Template contains fabricated events")
    for filename in ("scenario.md", "runbook.md", "evidence.md"):
        require((HERE / filename).is_file(), f"Missing operator document: {filename}")
    checks.append("prompts_and_no_fabricated_acceptance")
    return checks, states, stages, len(observed_headings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", help="Named control snapshot from stages.json")
    parser.add_argument("--observed", type=Path, help="Export or captured proposal; read only")
    args = parser.parse_args()
    try:
        require(bool(args.stage) == bool(args.observed), "Use --stage and --observed together")
        checks, states, stages, bt_heading_count = check_packet()
        result = {
            "static_status": "PASS",
            "checks": checks,
            "tz_snapshots": len(stages),
            "tz_sections_each": len(HEADINGS),
            "bt_headings": bt_heading_count,
            "live_acceptance": "NOT_RUN",
            "recording": "NOT_RUN",
            "diagram_rendering": "NOT_RUN",
        }
        if args.observed:
            require(args.stage in stages, f"Unknown stage: {args.stage}")
            observed = parse_markdown(args.observed.read_text(encoding="utf-8-sig"))
            differences = sorted(changed_sections(states[args.stage], observed), key=int)
            require(not differences, f"Observed snapshot differs in sections {', '.join(differences)}; this is strict text comparison, not a semantic verdict")
            result.update(observed_status="MATCH", stage=args.stage)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (SpecificationError, ValueError, KeyError, OSError) as error:
        print(json.dumps({"static_status": "FAIL", "error": str(error), "live_acceptance": "NOT_RUN", "recording": "NOT_RUN"}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
