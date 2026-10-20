"""One authorized scenario for product, HTTP and alignment regression runners."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime

from api.source_workbench.errors import SourceWorkspaceError

from .alignment import candidate_identity, fingerprint, source_snapshot
from .alignment_budget import AlignmentBudget
from .domain import SpecificationError, fail, identifier, text, version


class AlignmentApplication:
    def __init__(self, repository, sources, ai, run_sync):
        self.repository, self.sources, self.ai, self.run = repository, sources, ai, run_sync

    async def catalogue(self, actor, workspace, query="", page=1):
        await self.run(self.repository.access, actor, workspace, "edit")
        if type(page) is not int or not 1 <= page <= 1000:
            fail("INVALID_PAGINATION", "Некорректная страница оснований")
        return await self.run(self.sources.list, actor, text(query, "Поиск БТ", 255, empty=True), page)

    async def get(self, actor, workspace, document_id):
        document = await self.run(self.repository.alignment_document, actor, workspace, document_id)
        history = []
        for stored in document.get("alignment", []):
            item = deepcopy(stored)
            try:
                await self.run(self.sources.validate, actor, item["references"], fresh=False)
            except (SpecificationError, SourceWorkspaceError):
                history.append({"id": item["id"], "current": False, "qualifies": False, "reason_code": "SOURCE_UNAVAILABLE"})
                continue
            current = candidate_identity(document) == item["candidate_sha256"]
            try:
                await self.run(self.sources.validate, actor, item["references"], fresh=True)
            except (SpecificationError, SourceWorkspaceError):
                current = False
            item["current"] = current
            item["qualifies"] = current and item.get("result", {}).get("qualifies", False)
            history.append(item)
        return {"document_id": document_id, "revision": document["revision"], "checks": history, "document": {key: value for key, value in document.items() if key != "alignment"}}

    async def check(self, actor, workspace, document_id, data):
        await self.run(self.repository.access, actor, workspace, "edit")
        document = await self.run(self.repository.alignment_document, actor, workspace, document_id)
        request_id = text(data.get("request_id"), "Идентификатор проверки", 64)
        selected = data.get("business_document_ids")
        if not isinstance(selected, list) or not 1 <= len(selected) <= 16 or any(not isinstance(item, str) or not item or len(item) > 64 for item in selected) or len(set(selected)) != len(selected):
            fail("ALIGNMENT_SOURCE_MISSING", "Выберите неповторяющиеся согласованные БТ")
        request_sha = fingerprint([actor, document_id, sorted(selected), data.get("version")])
        previous = next((item for item in document.get("alignment", []) if item["id"] == request_id), None)
        if previous:
            if previous["request_sha256"] != request_sha:
                fail("IDEMPOTENCY_KEY_REUSED", "Идентификатор проверки уже использован", 409)
            # Replay revalidates source access; it never consumes another model call.
            return await self.get(actor, workspace, document_id)
        if version(data.get("version")) != document["version"]:
            fail("VERSION_CONFLICT", "ТЗ изменилось перед проверкой", 409)
        rows = await self.run(self.sources.read_many, actor, selected, body=True)
        if set(rows) != set(selected):
            fail("ALIGNMENT_SOURCE_MISSING", "Не все согласованные БТ доступны", 409)
        basis = source_snapshot(list(rows.values()))
        references = [{"document_id": row["document_id"], "revision": row["revision"]} for row in rows.values()]
        candidate = candidate_identity(document)
        attempt = identifier()
        budget = AlignmentBudget()

        def accept(doc):
            if candidate_identity(doc) != candidate:
                fail("VERSION_CONFLICT", "Содержание ТЗ изменилось", 409)
            self.sources.validate(actor, references, fresh=True, lock=True)
            doc.setdefault("alignment", []).append(
                {
                    "id": request_id,
                    "attempt": attempt,
                    "status": "PROCESSING",
                    "request_sha256": request_sha,
                    "candidate_sha256": candidate,
                    "basis_sha256": basis["sha256"],
                    "references": references,
                    "created_at": datetime.now(UTC).isoformat(),
                    "budget": {"call_limit": 1, "reserved_calls": 1, "spent_tokens": None, "reserved_tokens": None, "token_limit": 1_000_000},
                }
            )

        await self.run(self.repository.change, actor, workspace, document_id, document["version"], accept, "alignment-start")

        def pending(doc):
            item = next(row for row in doc["alignment"] if row["id"] == request_id)
            if item["attempt"] != attempt or item["status"] != "PROCESSING":
                fail("ALIGNMENT_ATTEMPT_STALE", "Исполнитель проверки устарел", 409)
            return item

        async def reserved(audit):
            def record_reservation(doc):
                item = pending(doc)
                item.update(
                    budget=budget.snapshot(),
                    request_audit={key: audit[key] for key in ("request_id", "profile_sha256", "input_sha256", "request_sha256", "context_limit", "input_estimate", "output_limit") if key in audit},
                )

            await self.run(self.repository.change, actor, workspace, document_id, None, record_reservation, "alignment-reserved", latest=True)

        try:
            result = await self.ai.check(actor, document, basis, budget=budget, on_reserved=reserved)
        except BaseException as error:
            reason = "EXECUTION_CANCELLED" if isinstance(error, asyncio.CancelledError) else "EXECUTION_INTERRUPTED"

            def aborted(doc):
                item = next(row for row in doc["alignment"] if row["id"] == request_id)
                if item["attempt"] == attempt and item["status"] == "PROCESSING":
                    item.update(status="INCOMPLETE", reason_code=reason, finished_at=datetime.now(UTC).isoformat(), budget=budget.snapshot())

            await asyncio.shield(self.run(self.repository.change, actor, workspace, document_id, None, aborted, "alignment-interrupted", latest=True))
            raise

        def finish(doc):
            item = pending(doc)
            # Same database transaction as publication of the verdict. No model
            # or network work runs here; the owner contract locks source rows.
            self.sources.validate(actor, references, fresh=True, lock=True)
            if candidate_identity(doc) != candidate:
                fail("VERSION_CONFLICT", "ТЗ изменилось во время проверки", 409)
            budget.remaining()
            item.update(status="FINISHED", finished_at=datetime.now(UTC).isoformat(), result=result, budget=budget.snapshot())

        try:
            await self.run(self.repository.change, actor, workspace, document_id, None, finish, "alignment-result", latest=True)
        except (SpecificationError, SourceWorkspaceError) as error:
            reason_code = error.code

            def interrupted(doc):
                item = next(row for row in doc["alignment"] if row["id"] == request_id)
                if item["attempt"] == attempt and item["status"] == "PROCESSING":
                    item.update(status="INCOMPLETE", reason_code=reason_code, result={**result, "qualifies": False}, finished_at=datetime.now(UTC).isoformat(), budget=budget.snapshot())

            await self.run(self.repository.change, actor, workspace, document_id, None, interrupted, "alignment-stale", latest=True)
            raise
        return await self.get(actor, workspace, document_id)
