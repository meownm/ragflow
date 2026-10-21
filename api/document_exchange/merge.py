"""Conservative three-way text merge, without positional block identities."""

from collections import Counter
from difflib import SequenceMatcher


def line_changes(before: str, after: str) -> list:
    """Return exact line replacements with bounded matching work.

    Large unmatched middles remain one replacement. This preserves every byte
    and makes three-way merging conservative instead of spending quadratic
    time trying to align repeated lines.
    """
    original, target = before.splitlines(keepends=True), after.splitlines(keepends=True)
    start, end, target_end = 0, len(original), len(target)
    while start < min(end, target_end) and original[start] == target[start]:
        start += 1
    while end > start and target_end > start and original[end - 1] == target[target_end - 1]:
        end, target_end = end - 1, target_end - 1
    left, right = original[start:end], target[start:target_end]
    if len(left) * len(right) > 65_536:
        return [(start, end, right)]
    return [(start + a, start + b, right[c:d]) for op, a, b, c, d in SequenceMatcher(None, left, right, autojunk=False).get_opcodes() if op != "equal"]


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
    repeated = Counter(line.rstrip("\r\n") for line in lines)
    # Whitespace is preserved data, but cannot identify repeated occurrences.
    if repeated and all(not line.strip() or count > 1 for line, count in repeated.items()):
        return {"conflict": True, "base": base, "local": local, "remote": remote}

    left, right = line_changes(base, local), line_changes(base, remote)
    # An insertion/deletion among equal occurrences has no stable identity.
    # Otherwise both sides can count the same added occurrence twice.
    # In particular,
    # SequenceMatcher may describe a replacement as an insertion plus deletion.
    # Deduplicating that deletion against the other side would lose its intent.
    if any(
        any(repeated[line.rstrip("\r\n")] > 1 for line in lines[start:end]) or (start == end and any(repeated[line.rstrip("\r\n")] for line in replacement))
        for start, end, replacement in [*left, *right]
        if end - start != len(replacement)
    ):
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
