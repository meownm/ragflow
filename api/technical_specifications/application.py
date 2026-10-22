"""Working TЗ scenarios, composed with value-only repository and AI ports."""

import asyncio
import re
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlsplit

from api.document_exchange.content import content_hash

from .domain import SpecificationError, assert_context, context, draft, empty_sections, eva_identity, fail, identifier, markdown, merge_sections, parse_markdown, sections, text, version


def _eva_baseline(base, remote, hashes):
    """Keep the local spelling of sections unchanged in Eva's representation."""
    return {key: base[key] if content_hash(value) == hashes.get(key, content_hash(base[key])) else value for key, value in remote.items()}


class Specifications:
    def __init__(self, repository, ai, run_sync, eva=None, alignment=None):
        self.repository, self.ai, self.run, self.eva = repository, ai, run_sync, eva
        self.alignment = alignment

    async def alignment_sources(self, actor, workspace, query="", page=1):
        return await self.alignment.catalogue(actor, workspace, query, page)

    async def alignment_report(self, actor, workspace, document_id):
        return await self.alignment.get(actor, workspace, document_id)

    async def check_alignment(self, actor, workspace, document_id, data):
        return await self.alignment.check(actor, workspace, document_id, data)

    async def list(self, actor, workspace):
        return await self.run(self.repository.list, actor, workspace)

    async def create(self, actor, workspace, data):
        return await self.run(self.repository.create, actor, workspace, text(data.get("title"), "Название", 255), empty_sections())

    async def get(self, actor, workspace, document_id):
        return await self.run(self.repository.get, actor, workspace, document_id)

    async def export_markdown(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        if snapshot["revision"] != version(data.get("revision")):
            fail("VERSION_CONFLICT", "ТЗ изменилось. Обновите документ перед выгрузкой", 409)
        title = " ".join(snapshot["title"].splitlines())
        filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip(" .")[:100] or "Документ"
        return {
            "filename": f"ТЗ-{filename}-r{snapshot['revision']}.md",
            "mime_type": "text/markdown;charset=utf-8",
            "content": markdown(snapshot["sections"]),
            "revision": snapshot["revision"],
        }

    async def eva_pages(self, actor, workspace, query):
        await self.run(self.repository.access, actor, workspace, "edit")
        maximum = 2000 if isinstance(query, str) and query.strip().casefold().startswith(("http://", "https://")) else 500
        result = await self.run(self.eva.search_sources, actor, text(query, "Поиск Eva", maximum, empty=True), 20)
        identities = [eva_identity({**page, "document_id": page["id"]}) for page in result["items"]]
        occupied = await self.run(self.repository.origin_matches, actor, workspace, identities)
        items = [{**page, **occupied.get(identity, {"linked_document": None, "binding_available": True})} for page, identity in zip(result["items"], identities, strict=True)]
        return {**result, "items": items}

    @staticmethod
    def _import_request(data):
        selected = data.get("selection")
        if selected is not None and (
            not isinstance(selected, dict) or set(selected) != {"id", "project_id", "version"} or any(not isinstance(value, str) or not value or len(value) > 255 for value in selected.values())
        ):
            fail("INVALID_INPUT", "Выберите страницу Eva заново")
        copy = data.get("copy", False)
        if type(copy) is not bool:
            fail("INVALID_INPUT", "Укажите, нужна ли независимая копия")
        url = text(data.get("url"), "Адрес страницы", 2000).strip()
        try:
            parsed = urlsplit(url)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Invalid page address")
            _ = parsed.port
        except ValueError:
            fail("INVALID_INPUT", "Укажите HTTP(S) адрес страницы Eva без учётных данных")
        return {"action": "import_eva", "url": parsed._replace(query="", fragment="").geturl().rstrip("/"), "copy": copy, **({"selection": selected} if selected is not None else {})}

    async def import_operation(self, actor, workspace, request_id):
        return await self.run(self.repository.import_operation, actor, workspace, text(request_id, "Идентификатор импорта", 32))

    async def cancel_import(self, actor, workspace, data):
        return await self.run(self.repository.cancel_import, actor, workspace, text(data.get("operation_id"), "Идентификатор импорта", 32), self._import_request(data))

    async def import_eva(self, actor, workspace, data):
        await self.run(self.repository.access, actor, workspace, "edit")
        request = self._import_request(data)
        request_id = identifier() if data.get("operation_id") is None else text(data["operation_id"], "Идентификатор импорта", 32)
        started = await self.run(self.repository.begin_import, actor, workspace, request_id, request)
        if started["operation"]["state"] == "completed":
            if started["operation"].get("document_unavailable"):
                fail("IMPORT_RESULT_UNAVAILABLE", "Импорт завершён, но результат сейчас недоступен", 409)
            return started["operation"]["document"]
        attempt = started["attempt"]
        try:
            selected, copy = request.get("selection"), request["copy"]
            binding = await self.run(self.eva.resolve_page_url, actor, request["url"])
            if selected and (selected["id"], selected["project_id"]) != (binding.get("document_id"), binding.get("project_id")):
                fail("EVA_CHANGED", "По ссылке открывается другая страница. Найдите и выберите её заново", 409)
            origin = eva_identity(binding)
            if not copy:
                existing = await self.run(self.repository.find_origin, actor, workspace, origin)
                if existing:
                    return await self.run(self.repository.save_import, actor, workspace, request_id, attempt, {"id": existing["id"], "workspace_id": existing["workspace_id"]})
            binding, remote = await self.run(self.eva.read_connected_page, actor, binding)
            if selected is not None and (selected["id"], selected["project_id"], selected["version"]) != (binding.get("document_id"), binding.get("project_id"), binding.get("remote_version")):
                fail("EVA_CHANGED", "Страница Eva изменилась. Найдите и выберите её заново", 409)
            content = parse_markdown(remote) if remote.strip() else empty_sections()
            title = text(binding.get("document_name"), "Название страницы", 255)
            if copy:
                title = title[:245] + " (копия)"
            else:
                binding = {**binding, "base": content}
            values = {
                "title": title,
                "sections": content,
                "binding": None if copy else binding,
                "origin_key": None if copy else origin,
                "provenance": {key: binding.get(key) for key in ("connector_id", "eva_origin", "project_id", "document_id", "page_url", "remote_version")},
            }
            return await self.run(self.repository.save_import, actor, workspace, request_id, attempt, values)
        except (Exception, asyncio.CancelledError) as error:
            failure = {"code": error.code if isinstance(error, SpecificationError) else "IMPORT_FAILED", "message": "Не удалось завершить импорт. Можно повторить запрос"}
            try:
                await asyncio.shield(self.run(self.repository.fail_import, actor, workspace, request_id, attempt, failure))
            except Exception:  # noqa: BLE001 - preserve the initial failure if receipt storage is unavailable
                # A storage outage leaves the expiring claim recoverable.
                raise error from None
            raise

    async def connect_eva(self, actor, workspace, document_id, data):
        await self.run(self.repository.access, actor, workspace, "edit")
        request = self._import_request({"url": data.get("url")})
        binding = await self.run(self.eva.resolve_page_url, actor, request["url"])
        binding, remote = await self.run(self.eva.read_connected_page, actor, binding)
        baseline = parse_markdown(remote) if remote.strip() else empty_sections()
        return await self.run(self.repository.bind_eva, actor, workspace, document_id, data.get("version"), {**binding, "base": baseline})

    async def publish_eva(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        await self.run(self.repository.access, actor, workspace, "edit")
        if not snapshot["eva"]:
            fail("EVA_NOT_BOUND", "Сначала свяжите ТЗ со страницей Eva", 409)
        active = snapshot.get("eva_export") or {}
        if active.get("status") == "publishing" and (datetime.now(UTC) - datetime.fromisoformat(active["started_at"])).total_seconds() < 360:
            fail("EVA_EXPORT_BUSY", "Обновление Eva ещё выполняется. Дождитесь результата", 409)
        attempt = identifier()

        def reserve(doc):
            doc["eva_export"] = {"id": attempt, "status": "publishing", "revision": snapshot["revision"], "started_at": datetime.now(UTC).isoformat()}

        await self.run(self.repository.change, actor, workspace, document_id, data.get("version"), reserve, "eva_export")
        try:
            binding, published = await self.run(self.eva.publish_connected_page, actor, snapshot["eva"], markdown(snapshot["sections"]), snapshot["title"])
            published_sections = parse_markdown(published) if published.strip() else empty_sections()
        except asyncio.CancelledError:
            # A synchronous network call can still finish after HTTP cancellation.
            raise
        except Exception as error:

            def failed(doc):
                if (doc.get("eva_export") or {}).get("id") == attempt:
                    doc["eva_export"]["status"] = "failed"

            try:
                await asyncio.shield(self.run(self.repository.change, actor, workspace, document_id, None, failed, "eva_export", latest=True))
            except Exception:  # noqa: BLE001 - preserve the external error if local storage is unavailable
                raise error from None
            raise

        def complete(doc):
            if (doc.get("eva_export") or {}).get("id") != attempt:
                fail("EVA_EXPORT_BUSY", "Состояние выгрузки изменилось. Проверьте страницу Eva", 409)
            doc["eva"] = {**binding, "base": snapshot["sections"], "section_hashes": {key: content_hash(value) for key, value in published_sections.items()}}
            doc["eva_export"].update(status="published", published_at=datetime.now(UTC).isoformat())

        return await self.run(self.repository.change, actor, workspace, document_id, None, complete, "eva_export", latest=True)

    async def pull(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        await self.run(self.repository.access, actor, workspace, "edit")
        if snapshot["version"] != version(data.get("version")):
            fail("VERSION_CONFLICT", "ТЗ изменилось", 409)
        if not snapshot["eva"]:
            fail("EVA_NOT_BOUND", "ТЗ не связано со страницей Eva", 409)
        binding, remote = await self.run(self.eva.read_connected_page, actor, snapshot["eva"])
        content = parse_markdown(remote) if remote.strip() else empty_sections()
        # Eva's verified hash uses its decoded representation. The local source
        # may be equivalent HTML, so comparing it to decoded Markdown is not a
        # reliable test for whether the published remote content changed.
        remote_hash = binding.get("remote_content_hash")
        unchanged = (bool(remote_hash) and remote_hash == snapshot["eva"].get("remote_content_hash")) or content == snapshot["eva"]["base"]
        if unchanged:
            if binding.get("remote_version") == snapshot["eva"].get("remote_version") and (snapshot["eva"].get("section_hashes") or content == snapshot["eva"]["base"]):
                return snapshot

            def refresh(doc):
                doc["eva"] = {**doc["eva"], **binding, "base": doc["eva"]["base"], "section_hashes": {key: content_hash(value) for key, value in content.items()}}

            return await self.run(self.repository.change, actor, workspace, document_id, snapshot["version"], refresh, "pull")
        base_hashes = snapshot["eva"].get("section_hashes", {})
        merged = merge_sections(snapshot["eva"]["base"], snapshot["sections"], _eva_baseline(snapshot["eva"]["base"], content, base_hashes))
        proposal = {
            "id": identifier(),
            "source": "eva",
            "author_id": actor,
            "base_revision": snapshot["revision"],
            "sections": merged["sections"],
            "conflicts": merged["conflicts"],
            "base": snapshot["eva"]["base"],
            "base_hashes": base_hashes,
            "remote": content,
            "binding": binding,
            "status": "needs_resolution" if merged["conflicts"] else "ready",
        }

        def update(doc):
            same = next(
                (
                    item
                    for item in doc["proposals"]
                    if item.get("source") == "eva" and item["base_revision"] == doc["revision"] and item.get("binding") == binding and item["status"] in {"ready", "needs_resolution"}
                ),
                None,
            )
            if not same:
                doc["proposals"].append(proposal)

        return await self.run(self.repository.change, actor, workspace, document_id, snapshot["version"], update, "pull")

    async def resolve_eva(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        await self.run(self.repository.access, actor, workspace, "edit")
        proposal = next((item for item in snapshot["proposals"] if item["id"] == data.get("proposal_id") and item.get("source") == "eva"), None)
        if not proposal or proposal["base_revision"] != snapshot["revision"] or snapshot["version"] != version(data.get("version")):
            fail("STALE_PROPOSAL", "Подготовьте объединение заново", 409)
        if not isinstance(data.get("choice"), str) or data["choice"] not in {"local", "remote"}:
            fail("INVALID_INPUT", "Выберите, какие значения сохранить")
        if proposal["status"] == "applied":
            return snapshot
        await self._check_eva(actor, proposal)
        candidate = sections(await self.ai.resolve_merge(actor, proposal["base"], snapshot["sections"], proposal["remote"], data["choice"]))
        await self._check_eva(actor, proposal)

        def update(doc):
            item = next(item for item in doc["proposals"] if item["id"] == proposal["id"])
            if doc["revision"] != snapshot["revision"] or item != proposal:
                fail("STALE_PROPOSAL", "Предложение изменилось. Обновите объединение", 409)
            item.update(sections=candidate, status="ready", resolution=data["choice"], issues=[])

        return await self.run(self.repository.change, actor, workspace, document_id, None, update, "resolve", latest=True)

    async def edit(self, actor, workspace, document_id, data):
        content = sections(data.get("sections"))
        title = text(data.get("title"), "Название", 255)

        def update(doc):
            doc.update(sections=content, title=title)

        return await self.run(self.repository.change, actor, workspace, document_id, data.get("version"), update, "edit")

    async def save_draft(self, actor, workspace, document_id, data):
        return await self.run(self.repository.save_draft, actor, workspace, document_id, data.get("version"), draft(data.get("draft"), document_id))

    async def get_draft(self, actor, workspace, document_id):
        return await self.run(self.repository.get_draft, actor, workspace, document_id)

    async def history(self, actor, workspace, document_id, number=None):
        return await self.run(self.repository.history, actor, workspace, document_id, number)

    async def restore(self, actor, workspace, document_id, data):
        revision = await self.history(actor, workspace, document_id, version(data.get("revision")))

        def update(doc):
            doc["sections"] = revision["sections"]
            doc["title"] = revision["title"]

        return await self.run(self.repository.change, actor, workspace, document_id, data.get("version"), update, "restore")

    async def comment(self, actor, workspace, document_id, data):
        action = data.get("action", "add")
        if not isinstance(action, str) or action not in {"add", "reply", "resolve"}:
            fail("INVALID_INPUT", "Неизвестное действие с комментарием")
        item_id = data.get("comment_id")
        content = text(data.get("text"), "Комментарий", 10_000) if action in {"add", "reply"} else None
        scope = context(data.get("context"), document_id) if action == "add" else None
        message_id = text(data.get("message_id"), "Идентификатор комментария", 64) if action in {"add", "reply"} else None
        if message_id:
            snapshot = await self.get(actor, workspace, document_id)
            await self.run(self.repository.access, actor, workspace, "edit")
            for parent in snapshot["comments"]:
                for item in [parent, *parent["replies"]]:
                    if item["id"] != message_id:
                        continue
                    right_target = (action == "add" and item is parent and item["context"] == scope) or (action == "reply" and item is not parent and parent["id"] == item_id)
                    if right_target and item["author_id"] == actor and item["text"] == content:
                        return snapshot
                    fail("IDEMPOTENCY_KEY_REUSED", "Идентификатор комментария уже использован", 409)

        def update(doc):
            if action == "add":
                assert_context(scope, doc["sections"])
                doc["comments"].append({"id": message_id, "author_id": actor, "created_at": datetime.now(UTC).isoformat(), "context": scope, "text": content, "resolved": False, "replies": []})
                return
            item = next((item for item in doc["comments"] if item["id"] == item_id), None)
            if item is None:
                fail("NOT_FOUND", "Комментарий не найден", 404)
            if action == "reply":
                item["replies"].append({"id": message_id, "author_id": actor, "created_at": datetime.now(UTC).isoformat(), "text": content})
            elif action == "resolve" and type(data.get("resolved")) is bool:
                if item["resolved"] != data["resolved"]:
                    item["resolved"] = data["resolved"]
                    item["resolved_at"] = datetime.now(UTC).isoformat() if data["resolved"] else None
                    item["resolved_by"] = actor if data["resolved"] else None
            else:
                fail("INVALID_INPUT", "Неизвестное действие с комментарием")

        return await self.run(self.repository.change, actor, workspace, document_id, data.get("version"), update, "comment")

    async def chat(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        # A read-only member cannot consume a model and then create a proposal.
        await self.run(self.repository.access, actor, workspace, "edit")
        message = draft(data.get("draft"), document_id)
        existing = next((item for item in snapshot["messages"] if item.get("id") == message["message_id"]), None)
        if existing:
            if existing.get("role") != "user" or existing.get("author_id") != actor or any(existing.get(key) != message[key] for key in ("text", "context")):
                fail("IDEMPOTENCY_KEY_REUSED", "Идентификатор сообщения уже использован с другим содержимым", 409)
            if existing.get("status", "completed") == "completed":
                return snapshot
            if existing.get("status") == "processing" and (datetime.now(UTC) - datetime.fromisoformat(existing["started_at"])).total_seconds() < 360:
                return snapshot
            if existing["source_revision"] != snapshot["revision"]:
                fail("STALE_MESSAGE", "ТЗ изменилось. Отправьте новый вопрос; исходное сообщение осталось в чате", 409)
        elif version(data.get("version")) != snapshot["version"]:
            fail("VERSION_CONFLICT", "ТЗ изменилось. Обновите его перед отправкой", 409)
        text(message["text"], "Сообщение", 10_000)
        assert_context(message["context"], snapshot["sections"])
        attempt = identifier()

        def accept(doc):
            item = next((item for item in doc["messages"] if item.get("id") == message["message_id"]), None)
            if item is None:
                item = {
                    "id": message["message_id"],
                    "role": "user",
                    "text": message["text"],
                    "context": message["context"],
                    "author_id": actor,
                    "source_revision": snapshot["revision"],
                    "created_at": datetime.now(UTC).isoformat(),
                }
                doc["messages"].append(item)
            item.update(status="processing", attempt=attempt, started_at=datetime.now(UTC).isoformat(), error=None)

        await self.run(self.repository.change, actor, workspace, document_id, snapshot["version"], accept, "chat-accepted")
        result_ready = False
        try:
            result = await self.ai.chat(actor, snapshot, message)
            answer = text(result.get("answer"), "Ответ помощника", 30_000)
            candidate = self._chat_candidate(snapshot, message, result)
            proposal = None
            if candidate != snapshot["sections"]:
                proposal = {
                    "id": identifier(),
                    "base_revision": snapshot["revision"],
                    "sections": candidate,
                    "source": "chat",
                    "message_id": message["message_id"],
                    "author_id": actor,
                    "status": "ready",
                }

            def complete(doc):
                item = next(item for item in doc["messages"] if item.get("id") == message["message_id"])
                if item.get("attempt") != attempt or item.get("status") != "processing":
                    return
                item.update(status="completed", error=None)
                doc["messages"].append(
                    {"id": identifier(), "reply_to": message["message_id"], "role": "assistant", "text": answer, "source_revision": snapshot["revision"], "created_at": datetime.now(UTC).isoformat()}
                )
                if proposal:
                    doc["proposals"].append(proposal)

            result_ready = True
            return await self.run(self.repository.change, actor, workspace, document_id, None, complete, "chat-completed", latest=True)
        except (Exception, asyncio.CancelledError) as error:
            code = "RESULT_SAVE_FAILED" if result_ready else "MODEL_FAILED"
            explanation = "Не удалось сохранить ответ. Повторите сообщение" if result_ready else "Не удалось получить ответ. Можно повторить сообщение"
            failure = {"code": error.code[:64] if isinstance(error, SpecificationError) else code, "message": error.message[:500] if isinstance(error, SpecificationError) else explanation}

            def failed(doc):
                item = next(item for item in doc["messages"] if item.get("id") == message["message_id"])
                if item.get("attempt") == attempt and item.get("status") == "processing":
                    item.update(status="failed", error=failure)

            await asyncio.shield(self.run(self.repository.change, actor, workspace, document_id, None, failed, "chat-failed", latest=True))
            raise

    @staticmethod
    def _chat_candidate(snapshot, message, result):
        patches = result.get("patches", {})
        if not isinstance(patches, dict) or set(patches) - set(snapshot["sections"]):
            fail("MODEL_INVALID_OUTPUT", "Помощник вернул некорректные разделы", 502)
        scope = message["context"]["scope"]
        if scope not in {"all", "selection"} and set(patches) - {scope}:
            fail("OUTSIDE_SCOPE", "Изменение требует других разделов. Явно выберите контекст «Всё ТЗ»", 409)
        candidate = sections({**snapshot["sections"], **patches})
        if scope == "selection" and patches:
            anchor = message["context"]["anchor"]
            key, quote = anchor["section"], anchor["quote"]
            if set(patches) != {key}:
                fail("OUTSIDE_SCOPE", "Правка выходит за выделенный фрагмент", 409)
            original = snapshot["sections"][key]
            start = original.index(anchor["prefix"] + quote + anchor["suffix"]) + len(anchor["prefix"])
            before, after = original[:start], original[start + len(quote) :]
            if not candidate[key].startswith(before) or not candidate[key].endswith(after) or len(candidate[key]) < len(before) + len(after):
                fail("OUTSIDE_SCOPE", "Правка выходит за выделенный фрагмент", 409)
        return candidate

    async def _check_eva(self, actor, proposal):
        fresh, remote = await self.run(self.eva.read_connected_page, actor, proposal["binding"])
        content = parse_markdown(remote) if remote.strip() else empty_sections()
        if content != proposal["remote"] or fresh.get("remote_version") != proposal["binding"].get("remote_version"):
            fail("EVA_CHANGED", "Страница Eva изменилась. Получите изменения заново", 409)

    async def apply(self, actor, workspace, document_id, data):
        snapshot = await self.get(actor, workspace, document_id)
        await self.run(self.repository.access, actor, workspace, "edit")
        requested = text(data.get("proposal_id"), "Предложение", 32)
        proposal = next((item for item in snapshot["proposals"] if item["id"] == requested), None)
        if proposal is None:
            fail("NOT_FOUND", "Предложение не найдено", 404)
        if proposal["status"] == "applied":
            return snapshot
        if proposal["status"] != "ready":
            fail("UNRESOLVED_CONFLICT", "Выберите результат объединения связанных изменений", 409)
        if snapshot["version"] != version(data.get("version")) or proposal["base_revision"] != snapshot["revision"]:
            fail("STALE_PROPOSAL", "ТЗ изменилось. Подготовьте правку заново", 409)
        candidate = sections(proposal["sections"])
        if proposal.get("source") == "eva":
            await self._check_eva(actor, proposal)
        # Edited resolutions are checked as a whole, including unaffected sections.
        if proposal.get("source") == "eva":
            verification = await self.ai.verify(actor, snapshot["sections"], candidate, merge={"base": proposal["base"], "remote": proposal["remote"], "choice": proposal.get("resolution")})
        else:
            change_request = None
            if proposal.get("message_id"):
                history = []
                for message in snapshot["messages"]:
                    if message.get("role") != "user" or message.get("status", "completed") != "completed":
                        continue
                    if message["id"] == proposal["message_id"] and message.get("author_id") == proposal["author_id"] and message["source_revision"] == proposal["base_revision"]:
                        change_request = {"text": message["text"], "context": message["context"], "history": history}
                        break
                    history.append({"text": message["text"], "context": message["context"]})
                if change_request is None:
                    fail("INVALID_PROPOSAL", "Исходный запрос предложения недоступен", 409)
            verification = await self.ai.verify(actor, snapshot["sections"], candidate, change_request=change_request)
        if not isinstance(verification.get("issues"), list) or any(not isinstance(issue, str) for issue in verification["issues"]):
            fail("MODEL_INVALID_OUTPUT", "Помощник вернул некорректную проверку", 502)
        if proposal.get("source") == "eva":
            await self._check_eva(actor, proposal)
        if verification.get("valid") is not True or verification["issues"]:

            def blocked(doc):
                item = next(item for item in doc["proposals"] if item["id"] == requested)
                if doc["revision"] != snapshot["revision"] or item != proposal:
                    fail("STALE_PROPOSAL", "Предложение изменилось. Подготовьте проверку заново", 409)
                item.update(status="needs_resolution", issues=verification["issues"] or ["Не удалось подтвердить согласованность ТЗ"])

            return await self.run(self.repository.change, actor, workspace, document_id, None, blocked, "proposal-blocked", latest=True)

        def update(doc):
            item = next(item for item in doc["proposals"] if item["id"] == requested)
            if item["status"] == "applied":
                return
            if item["base_revision"] != doc["revision"] or item != proposal:
                fail("STALE_PROPOSAL", "Подготовьте правку заново", 409)
            changed = candidate != doc["sections"]
            doc["sections"] = deepcopy(candidate)
            item.update(status="applied", applied_revision=doc["revision"] + int(changed))
            if item.get("source") == "eva":
                doc["eva"] = {
                    **item["binding"],
                    "base": _eva_baseline(item["base"], item["remote"], item.get("base_hashes", {})),
                    "section_hashes": {key: content_hash(value) for key, value in item["remote"].items()},
                }

        return await self.run(self.repository.change, actor, workspace, document_id, None, update, "apply", latest=True)
