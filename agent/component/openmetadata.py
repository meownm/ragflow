#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""Native RAGFlow Canvas component backed by the OpenMetadata Copilot runtime."""

from __future__ import annotations

from typing import Any

from agent.component.base import ComponentBase, ComponentParamBase
from common.openmetadata_agents import OPENMETADATA_AGENT_ROLE_IDS


class OpenMetadataParam(ComponentParamBase):
    def __init__(self):
        super().__init__()
        self.query = "{sys.query}"
        self.role = "catalog_copilot"
        self.locale = "ru"
        self.outputs = {
            "content": {"type": "string", "value": ""},
            "result": {"type": "object", "value": {}},
            "entity_ids": {"type": "array", "value": []},
        }

    def check(self):
        self.check_empty(self.query, "[OpenMetadata] Query")
        self.check_valid_value(self.role, "[OpenMetadata] Role", list(OPENMETADATA_AGENT_ROLE_IDS))
        self.check_valid_value(self.locale, "[OpenMetadata] Locale", ["ru", "en"])
        return True


class OpenMetadata(ComponentBase):
    component_name = "OpenMetadata"

    def get_input_elements(self) -> dict[str, Any]:
        return self.get_input_elements_from_text(self._param.query)

    def _authorize(self, service, user_id: str) -> None:
        dataset_id = str(getattr(service.config, "dataset_id", "") or "").strip()
        if dataset_id:
            from api.db.services.knowledgebase_service import KnowledgebaseService

            if KnowledgebaseService.accessible(dataset_id, user_id):
                return
        else:
            from api.db.services.user_service import UserService

            if UserService.is_admin(user_id):
                return
        raise PermissionError("OpenMetadata Catalog is not available for this RAGFlow user")

    @staticmethod
    def _entity_label(entity: dict[str, Any]) -> str:
        return str(entity.get("fqn") or entity.get("name") or entity.get("id") or "unknown")

    @staticmethod
    def _format_result(result: dict[str, Any], locale: str) -> str:
        answer = str(result.get("answer") or "").strip()
        entities = result.get("entities") or ([result["entity"]] if result.get("entity") else [])
        lines = [answer] if answer else []
        if entities:
            lines.append("\n**Сущности:**" if locale == "ru" else "\n**Entities:**")
            for entity in entities[:10]:
                label = entity.get("fqn") or entity.get("name") or entity.get("id")
                url = entity.get("url")
                lines.append(f"- [{label}]({url})" if url else f"- {label}")
                details = []
                if entity.get("owners"):
                    details.append(("владелец" if locale == "ru" else "owner") + f": {', '.join(entity['owners'])}")
                if entity.get("domains"):
                    details.append(("домен" if locale == "ru" else "domain") + f": {', '.join(entity['domains'])}")
                if entity.get("tags"):
                    details.append(("теги" if locale == "ru" else "tags") + f": {', '.join(entity['tags'])}")
                if entity.get("matched_columns"):
                    details.append(("совпавшие колонки" if locale == "ru" else "matched columns") + f": {', '.join(entity['matched_columns'])}")
                if details:
                    lines.append(f"  - {'; '.join(details)}")
        for heading_ru, heading_en, key in (
            ("Upstream lineage", "Upstream lineage", "upstream"),
            ("Downstream lineage", "Downstream lineage", "downstream"),
        ):
            edges = [edge for edge in result.get(key) or [] if isinstance(edge, dict)]
            if not edges:
                continue
            lines.append(f"\n**{heading_ru if locale == 'ru' else heading_en}:**")
            for edge in edges[:10]:
                source = OpenMetadata._entity_label(edge.get("from") or {})
                target = OpenMetadata._entity_label(edge.get("to") or {})
                lines.append(f"- {source} → {target}")
                mappings = edge.get("column_lineage") or []
                for mapping in mappings[:10]:
                    from_columns = ", ".join(mapping.get("from_columns") or [])
                    to_column = str(mapping.get("to_column") or "")
                    if from_columns and to_column:
                        lines.append(f"  - {from_columns} → {to_column}")
        foreign_keys = [edge for edge in result.get("foreign_keys") or [] if isinstance(edge, dict)]
        if foreign_keys:
            lines.append("\n**Внешние ключи:**" if locale == "ru" else "\n**Foreign keys:**")
            for edge in foreign_keys[:10]:
                source = OpenMetadata._entity_label(edge.get("from") or {})
                target = OpenMetadata._entity_label(edge.get("to") or {})
                from_columns = ", ".join(edge.get("from_columns") or [])
                to_columns = ", ".join(edge.get("to_columns") or [])
                suffix = f" ({from_columns} → {to_columns})" if from_columns or to_columns else ""
                lines.append(f"- {source} → {target}{suffix}")
        semantic_relations = [edge for edge in result.get("semantic_relations") or [] if isinstance(edge, dict)]
        if semantic_relations:
            lines.append("\n**Семантические связи:**" if locale == "ru" else "\n**Semantic relationships:**")
            for edge in semantic_relations[:10]:
                target = OpenMetadata._entity_label(edge.get("to") or {})
                terms = ", ".join(edge.get("shared_terms") or [])
                lines.append(f"- {target}" + (f" — {terms}" if terms else ""))
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        test_cases = [item for item in quality.get("test_cases") or [] if isinstance(item, dict)]
        if test_cases:
            lines.append("\n**Test cases таблицы:**" if locale == "ru" else "\n**Table test cases:**")
            for test_case in test_cases[:20]:
                label = test_case.get("name") or test_case.get("fqn") or test_case.get("id")
                details = [value for value in (test_case.get("definition"), test_case.get("status")) if value]
                lines.append(f"- {label}" + (f" — {', '.join(details)}" if details else ""))
        governance = result.get("governance_request") if isinstance(result.get("governance_request"), dict) else {}
        if governance:
            fields = ", ".join((governance.get("changes") or {}).keys()) or ("не определены" if locale == "ru" else "not detected")
            lines.append("\n**Governance:**")
            lines.append((f"- Подготовлены поля: {fields}. [Открыть Governance-форму](/openmetadata)" if locale == "ru" else f"- Prepared fields: {fields}. [Open the Governance form](/openmetadata)"))
        sources = [source for source in result.get("sources") or [] if isinstance(source, dict)]
        if sources:
            lines.append("\n**Источники:**" if locale == "ru" else "\n**Sources:**")
            for source in sources:
                label = str(source.get("label") or "source")
                url = str(source.get("url") or "")
                dataset_id = str(source.get("dataset_id") or "")
                lines.append(f"- [{label}]({url})" if url else f"- {label}: {dataset_id}" if dataset_id else f"- {label}")
        warnings = [str(warning).strip() for warning in result.get("warnings") or [] if str(warning).strip()]
        if warnings:
            lines.append("\n**Предупреждения:**" if locale == "ru" else "\n**Warnings:**")
            lines.extend(f"- {warning}" for warning in dict.fromkeys(warnings))
        return "\n".join(lines) or ("Нет данных для ответа." if locale == "ru" else "No answer data is available.")

    def _question(self, kwargs: dict[str, Any]) -> str:
        question = str(kwargs.get("query") or kwargs.get("sys.query") or self._canvas.get_sys_query() or "").strip()
        if not question:
            raise ValueError("OpenMetadata query is empty")
        return question

    def _context(self) -> list[dict[str, Any]]:
        globals_ = getattr(self._canvas, "globals", {})
        context = globals_.get("sys.openmetadata_context") if isinstance(globals_, dict) else None
        if not isinstance(context, list):
            return []
        return [turn for turn in context[-8:] if isinstance(turn, dict)]

    def _run(self, question: str, service, user_id: str, *, dataset_hits=None, dataset_warning=None, context=None):
        return service.run_agent(
            self._param.role,
            question,
            user_id=user_id,
            locale=self._param.locale,
            dataset_hits=dataset_hits,
            dataset_warning=dataset_warning,
            context=context or [],
        )

    def _store_result(self, question: str, result: dict[str, Any]) -> None:
        entities = result.get("entities") or ([result["entity"]] if result.get("entity") else [])
        self.set_output("result", result)
        entity_ids = [entity["id"] for entity in entities if entity.get("id")]
        self.set_output("entity_ids", entity_ids)
        self.set_output("content", self._format_result(result, self._param.locale))
        globals_ = getattr(self._canvas, "globals", None)
        if isinstance(globals_, dict):
            context = globals_.get("sys.openmetadata_context")
            if not isinstance(context, list):
                context = []
            context.append({"question": question, "entity_ids": entity_ids})
            globals_["sys.openmetadata_context"] = context[-8:]

    def _invoke(self, **kwargs):
        question = self._question(kwargs)

        from api.apps.services.openmetadata_copilot_service import OpenMetadataCopilotService

        user_id = str(self._canvas.get_tenant_id() or "")
        service = OpenMetadataCopilotService()
        self._authorize(service, user_id)
        self._store_result(question, self._run(question, service, user_id, context=self._context()))

    async def _invoke_async(self, **kwargs):
        question = self._question(kwargs)

        from api.apps.services.openmetadata_copilot_service import OpenMetadataCopilotService
        from api.apps.services.openmetadata_dataset_retrieval import retrieve_openmetadata_dataset_hits

        user_id = str(self._canvas.get_tenant_id() or "")
        service = OpenMetadataCopilotService()
        self._authorize(service, user_id)
        dataset_hits = dataset_warning = None
        retrieve_dataset = self._param.role in {"dataset_retrieval", "discovery", "governance"}
        if self._param.role == "catalog_copilot":
            retrieve_dataset = service.catalog.classify(question) in {"discovery", "governance"}
        if retrieve_dataset:
            dataset_hits, dataset_warning = await retrieve_openmetadata_dataset_hits(
                service,
                question,
                user_id,
            )
        self._store_result(
            question,
            self._run(
                question,
                service,
                user_id,
                dataset_hits=dataset_hits,
                dataset_warning=dataset_warning,
                context=self._context(),
            ),
        )

    def thoughts(self) -> str:
        return "OpenMetadata Copilot is querying the authorized catalog."
