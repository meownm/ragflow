"""Conservative three-way text merge, without positional block identities."""

from collections import Counter
from difflib import SequenceMatcher


def merge_text(base: str, local: str, remote: str) -> dict:
    """Merge disjoint line edits; never guess when edit intervals overlap.

    Insertions at the same boundary conflict unless identical. Moving, deleting,
    or replacing a range conflicts with an edit inside that range. Conflict
    output retains all three complete texts for an explicit resolution.
    """
    if local == remote or remote == base:
        return {"text": local, "conflict": False}
    if local == base:
        return {"text": remote, "conflict": False}
    lines = base.splitlines(keepends=True)
    repeated = Counter(line.rstrip("\r\n") for line in lines if line.strip())
    if repeated and all(count > 1 for count in repeated.values()):
        return {"conflict": True, "base": base, "local": local, "remote": remote}

    def changes(value):
        target = value.splitlines(keepends=True)
        return [(a, b, target[c:d]) for op, a, b, c, d in SequenceMatcher(None, lines, target, autojunk=False).get_opcodes() if op != "equal"]

    left, right = changes(local), changes(remote)
    # A deletion among equal occurrences has no stable identity. In particular,
    # SequenceMatcher may describe a replacement as an insertion plus deletion.
    # Deduplicating that deletion against the other side would lose its intent.
    if any(any(repeated[line.rstrip("\r\n")] > 1 for line in lines[start:end] if line.strip()) for start, end, replacement in [*left, *right] if end - start != len(replacement)):
        return {"conflict": True, "base": base, "local": local, "remote": remote}
    edits = list(left)
    for candidate in right:
        if candidate in edits:
            continue
        start, end, _ = candidate
        for a, b, _ in left:
            overlaps = max(a, start) < min(b, end)
            insertion = (a == b and start <= a <= end) or (start == end and a <= start <= b)
            if overlaps or insertion:
                return {"conflict": True, "base": base, "local": local, "remote": remote}
        edits.append(candidate)
    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        lines[start:end] = replacement
    return {"text": "".join(lines), "conflict": False}
