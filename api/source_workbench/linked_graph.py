"""Value-only dependency traversal and document-set readiness."""

from collections import deque

from .linked_domain import fail, source_state, input_reviewed


def pure_reference(link):
    return link["relation"] == "reference" and not link["required"] and not link["blocks_approval"] and link["maintenance"] == "manual"


def adjacency(links):
    result = {}
    for link in links:
        if not pure_reference(link):
            result.setdefault(link["source_id"], set()).add(link["target_id"])
            result.setdefault(link["target_id"], set())
    return result


def assert_acyclic_link(links, proposed, titles=None):
    if pure_reference(proposed):
        return
    graph = adjacency(links)
    source, target = proposed["source_id"], proposed["target_id"]
    parents = {target: None}
    pending = deque([target])
    while pending:
        current = pending.popleft()
        if current == source:
            path = []
            while current is not None:
                path.append(current)
                current = parents[current]
            titles = titles or {}
            fail("DEPENDENCY_CYCLE", "Связь создаёт цикл: " + " → ".join(titles.get(node, node) for node in [source, *reversed(path)]), 409)
        for following in sorted(graph.get(current, ())):
            if following not in parents:
                parents[following] = current
                pending.append(following)


def cyclic_components(links):
    """Iterative Kosaraju traversal; linear in nodes and edges, no recursion limit."""
    graph = adjacency(links)
    reverse = {node: set() for node in graph}
    for node, followers in graph.items():
        for following in followers:
            reverse[following].add(node)
    visited, order = set(), []
    for root in graph:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(graph[root])))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(graph[child]))))
    visited, result = set(), []
    for root in reversed(order):
        if root in visited:
            continue
        component, pending = [], [root]
        visited.add(root)
        while pending:
            node = pending.pop()
            component.append(node)
            for previous in reverse[node] - visited:
                visited.add(previous)
                pending.append(previous)
        if len(component) > 1 or root in graph[root]:
            result.append(sorted(component))
    return result


def overview(documents, links, changes, jobs, names, actor):
    by_id = {document["id"]: document for document in documents}
    incoming, outgoing = {}, {}
    for link in links:
        incoming.setdefault(link["target_id"], []).append(link)
        outgoing.setdefault(link["source_id"], []).append(link)
    cycles = {node: component for component in cyclic_components(links) for node in component}
    latest_changes, latest_jobs = {}, {}
    for change in changes:
        latest_changes.setdefault(change["document_id"], change)
    for job in jobs:
        latest_jobs.setdefault(job["document_id"], job)
    rows, direct_unresolved = {}, set()
    for document in documents:
        key = document["id"]
        reasons, blocking = [], []
        for link in incoming.get(key, []):
            source = by_id.get(link["source_id"])
            revision, token = source_state(source, link)
            if not revision or not input_reviewed(link, revision, document["current_revision_id"], token):
                reason = {
                    "source_id": link["source_id"],
                    "dependency_id": link["id"],
                    "message": "Ожидается подтверждение реализации"
                    if not revision and link["refresh_on"] == "implemented"
                    else "Ожидается утверждение источника"
                    if not revision
                    else "Изменилось основание или редакция документа",
                    "action": "sources",
                }
                reasons.append(reason)
                if link["required"] or link["blocks_approval"]:
                    blocking.append(reason)
        state, action = ("needs_review", "sources") if reasons else ("current", "text")
        if document.get("source_update_available"):
            state, action = "needs_review", "sources"
            reason = {"message": "Доступна новая согласованная редакция Business Documents", "action": "sources"}
            reasons.append(reason)
            blocking.append(reason)
        change = latest_changes.get(key)
        job = latest_jobs.get(key)
        if change and change["document_version"] == document["version"] and change["status"] in {"ready", "blocked"}:
            state, action = ("awaiting_decision" if change["status"] == "ready" else "blocked"), "changes"
            reasons.append(
                {
                    "message": "Ожидается решение: " + names.get(document["approver_id"], "назначенный согласующий")
                    if change["status"] == "ready"
                    else "Предложение требует разбора; откройте результаты проверки",
                    "action": action,
                }
            )
        if job and job["status"] in {"queued", "running"}:
            state, action = "preparing", "changes"
        if key in cycles:
            state, action = "cycle", "sources"
            reason = {"message": "Цикл актуализации: " + " → ".join(by_id[node]["title"] for node in cycles[key]), "action": "sources"}
            reasons.insert(0, reason)
            blocking.append(reason)
        if document["provenance_state"] == "unresolved":
            state, action = "blocked", "sources"
            blocking.append({"message": "Не подтверждена исходная база документа", "action": "sources"})
        if state == "current" and document["source_kind"] == "local":
            if document["approved_revision_id"] != document["current_revision_id"]:
                state = "draft"
                reasons.append({"message": "Текущая рабочая редакция ещё не утверждена", "action": "text"})
            elif any(link["refresh_on"] == "implemented" for link in outgoing.get(key, [])) and document.get("implemented_revision_id") != document["approved_revision_id"]:
                state, action = "needs_implementation", "implementation"
                reasons.append({"message": "Связанные документы ожидают подтверждения реализации", "action": action})
        if blocking or state in {"awaiting_decision", "preparing", "blocked", "cycle"}:
            direct_unresolved.add(key)
        rows[key] = {
            **document,
            "responsible_name": names.get(document["responsible_id"], "Ответственный не назначен"),
            "state": state,
            "action": action,
            "reasons": list(reasons or blocking),
            "blockers": blocking,
            "source_ids": [link["source_id"] for link in incoming.get(key, [])],
            "dependent_ids": [link["target_id"] for link in outgoing.get(key, [])],
            "mine": actor in {document["responsible_id"], document["approver_id"]},
        }
    # Propagate waiting once; it is informational until the selected source event occurs.
    graph = adjacency(links)
    waiting = set(direct_unresolved)
    pending = deque(sorted(waiting))
    while pending:
        node = pending.popleft()
        for following in sorted(graph.get(node, ())):
            if following not in waiting:
                waiting.add(following)
                pending.append(following)
                if rows[following]["state"] == "current":
                    rows[following].update(state="waiting_upstream", action="sources")
                    rows[following]["reasons"].append({"source_id": node, "message": "Ожидаются решения по цепочке оснований", "action": "sources"})
    # Required foundations remain required through unmarked intermediate documents.
    mandatory = {}
    for link in links:
        if link["required"] or link["blocks_approval"]:
            mandatory.setdefault(link["source_id"], set()).add(link["target_id"])
    blocked = {key for key, row in rows.items() if row["blockers"]}
    pending = deque(sorted(blocked))
    while pending:
        source = pending.popleft()
        for target in sorted(mandatory.get(source, ())):
            if target not in blocked:
                blocked.add(target)
                pending.append(target)
                reason = {"source_id": source, "message": "Не разобраны обязательные основания: " + by_id[source]["title"], "action": "sources"}
                rows[target]["blockers"].append(reason)
                rows[target]["reasons"].append(reason)
    required = [document for document in documents if document.get("required_for_readiness") and document["source_kind"] == "local"]
    agreed = bool(required) and all(document["approved_revision_id"] == document["current_revision_id"] and not rows[document["id"]]["blockers"] for document in required)
    complete = agreed and all(rows[document["id"]]["state"] == "current" for document in required)
    blockers = [
        {"document_id": document["id"], "message": "Текущая редакция не утверждена", "action": "text"} for document in required if document["approved_revision_id"] != document["current_revision_id"]
    ]
    blockers.extend({"document_id": document["id"], **reason} for document in required for reason in rows[document["id"]]["reasons"])
    return {
        "items": list(rows.values()),
        "readiness": {"configured": bool(required), "documents_agreed": agreed, "maintenance_complete": complete, "required_count": len(required), "blockers": blockers},
    }
