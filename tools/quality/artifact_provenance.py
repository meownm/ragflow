"""Bounded evidence lookup; a transcript hit never closes or adopts an artifact."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from tools.quality.artifact_registry import save, unlinked


def decoded_chunks(stream, compressed):
    if not compressed:
        while chunk := stream.read(65536):
            yield chunk
        return
    import zstandard

    decoder = zstandard.ZstdDecompressor().decompressobj()
    # Small input blocks bound expansion per call; eof is checked explicitly.
    # stream_reader alone can accept a truncated frame as an ordinary EOF.
    try:
        while chunk := stream.read(256):
            pending = chunk
            while pending:
                if decoder.eof:
                    decoder = zstandard.ZstdDecompressor().decompressobj()
                output = decoder.decompress(pending)
                pending = decoder.unused_data if decoder.eof else b""
                if output:
                    yield output
    except zstandard.ZstdError as error:
        raise ValueError("invalid-zstd-stream") from error
    if not decoder.eof:
        raise ValueError("incomplete-zstd-frame")


def lookup(paths, identifiers, *, max_bytes=64 * 1024 * 1024, max_line_bytes=2 * 1024 * 1024, max_matches=500, max_files=128):
    if (
        not identifiers
        or any(not isinstance(value, str) or not re.fullmatch(r"[\w-]{3,128}", value) for value in identifiers)
        or any(type(limit) is not int or limit <= 0 for limit in (max_bytes, max_line_bytes, max_matches, max_files))
    ):
        raise ValueError("Positive budgets and explicit bounded identifiers are required")
    result = {
        "schema": 1,
        "mode": "evidence-locator-only",
        "deletion_authorized": False,
        "complete": True,
        "identifiers": list(identifiers),
        "sources": [],
        "matches": [],
        "errors": [],
        "decoded_bytes": 0,
    }
    paths = list(dict.fromkeys(Path(path).absolute() for path in paths))
    patterns = {value: re.compile(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])") for value in identifiers}
    if not paths or len(paths) > max_files:
        result.update(complete=False, errors=[{"error": "file-budget-or-no-sources"}])
        return result
    physical = 0
    for path in paths:
        source = {"path": str(path), "complete": False}
        result["sources"].append(source)
        try:
            unlinked(path)
            before = path.stat()
            physical += before.st_size
            if physical > max_bytes:
                raise ValueError("source-byte-budget")
            if not (path.name.endswith(".jsonl.zst") or path.suffix in {".jsonl", ".json"}):
                raise ValueError("unsupported-source-format")

            def record(raw, line):
                if len(raw) > max_line_bytes:
                    raise ValueError("record-byte-budget")
                value = json.loads(raw)
                found, stack = set(), [value]
                while stack:
                    item = stack.pop()
                    if isinstance(item, dict):
                        stack.extend(item.values())
                    elif isinstance(item, list):
                        stack.extend(item)
                    elif isinstance(item, str):
                        found.update(key for key, pattern in patterns.items() if pattern.search(item))
                    elif type(item) is int and str(item) in patterns:
                        found.add(str(item))
                for key in sorted(found):
                    if len(result["matches"]) >= max_matches:
                        raise ValueError("match-budget")
                    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
                    result["matches"].append({"identifier": key, "path": str(path), "line": line, "record_sha256": hashlib.sha256(canonical).hexdigest()})

            with path.open("rb") as stream:
                pending, line = b"", 0
                for chunk in decoded_chunks(stream, path.name.endswith(".jsonl.zst")):
                    result["decoded_bytes"] += len(chunk)
                    if result["decoded_bytes"] > max_bytes:
                        raise ValueError("decoded-byte-budget")
                    if path.suffix == ".json":
                        pending += chunk
                        if len(pending) > max_line_bytes:
                            raise ValueError("record-byte-budget")
                        continue
                    parts = chunk.split(b"\n")
                    for part in parts[:-1]:
                        line += 1
                        raw = pending + part
                        pending = b""
                        if raw.strip():
                            record(raw, line)
                    pending += parts[-1]
                    if len(pending) > max_line_bytes:
                        raise ValueError("record-byte-budget")
                if pending.strip():
                    record(pending, line + 1)
            checksum = hashlib.sha256()
            count = 0
            unlinked(path)
            with path.open("rb") as stream:
                while chunk := stream.read(min(65536, max_bytes - count + 1)):
                    count += len(chunk)
                    if count > max_bytes:
                        raise ValueError("source-changed-or-byte-budget")
                    checksum.update(chunk)
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("source-changed")
            source.update(sha256=checksum.hexdigest(), complete=True)
        except (OSError, ValueError, ImportError, RecursionError) as error:
            result["errors"].append({"path": str(path), "error": str(error) if type(error) is ValueError and re.fullmatch(r"[a-z-]+", str(error)) else type(error).__name__})
    result["complete"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--id", dest="identifiers", action="append", required=True)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        from tools.quality.artifact_lifecycle import Run

        with Run("artifact-provenance", category="metadata") as run:
            run.path(args.output, "metadata")
            report = lookup(args.sources, args.identifiers, max_bytes=args.max_bytes)
            save(report, args.output)
            run.path(args.output, "metadata")
            run.failed = not report["complete"]
    else:
        report = lookup(args.sources, args.identifiers, max_bytes=args.max_bytes)
    print(json.dumps(report, ensure_ascii=False))
    return int(not report["complete"])


if __name__ == "__main__":
    raise SystemExit(main())
