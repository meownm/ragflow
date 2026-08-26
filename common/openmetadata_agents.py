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

"""Definitions and Canvas DSL for managed OpenMetadata Agent Apps."""

from __future__ import annotations

import hashlib
from typing import Any

MANAGED_BY = "openmetadata_copilot"

OPENMETADATA_AGENT_ROLES = (
    {
        "id": "catalog_copilot",
        "title": "OpenMetadata · Catalog Copilot",
        "mode": "read",
        "description": (
            "Что делает: служит единой точкой входа для вопросов о каталоге данных — ищет таблицы, "
            "показывает зависимости и качество, направляет запросы на безопасное изменение метаданных.\n"
            "Как работает: определяет намерение вопроса, использует доступную пользователю проекцию "
            "OpenMetadata и семантический индекс RAGFlow, сохраняет контекст сущностей последних восьми ходов и "
            "возвращает ссылки на источники.\n"
            "Для чего предназначен: для общих вопросов по каталогу, когда не требуется заранее выбирать "
            "узкоспециализированного агента.\n"
            "Примеры вопросов: «В какой таблице домена MeetingsScheduling хранятся meeting_id, start_at_utc, "
            "attendees_snapshot и lifecycle_status?», «Найди таблицу домена Telephony с полями src, dst, "
            "billsec, disposition и linkedid», «Какие проверки качества настроены для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs?»"
        ),
        "prologue": (
            "Здравствуйте! Я — единая точка входа для работы с каталогом OpenMetadata: нахожу таблицы "
            "и их структуру, показываю lineage и проверки качества, а запросы на изменение направляю в "
            "безопасный Governance-процесс.\n\n"
            "Примеры вопросов:\n"
            "- «В какой таблице домена MeetingsScheduling хранятся meeting_id, start_at_utc, "
            "attendees_snapshot и lifecycle_status?»\n"
            "- «Найди таблицу домена Telephony с полями src, dst, billsec, disposition и linkedid»\n"
            "- «Какие проверки качества настроены для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs?»"
        ),
    },
    {
        "id": "dataset_retrieval",
        "title": "OpenMetadata · Dataset Retrieval",
        "mode": "read",
        "description": (
            "Что делает: находит таблицы по смыслу их описаний в приватном OpenMetadata Dataset.\n"
            "Как работает: выполняет семантический поиск по индексированным документам, затем сопоставляет "
            "каждый результат с текущей доступной проекцией OpenMetadata и её updatedAt; удалённые, устаревшие "
            "или недоступные таблицы отбрасывает, а при недоступности Dataset явно переходит к live-поиску OMD.\n"
            "Для чего предназначен: для поиска данных, когда точное имя или FQN таблицы неизвестны, но известно "
            "её назначение.\n"
            "Примеры вопросов: «Найди таблицу с полями weekly_rules, date_overrides и booking_horizon_days», "
            "«Где хранятся model, input_json, output_json, success и error для LLM-запросов?», «Найди структуру "
            "OCR-заданий с полями errors, failed_pages, ocr и markdown_content»"
        ),
        "prologue": (
            "Здравствуйте! Я выполняю смысловой поиск по OpenMetadata Dataset, когда точное имя таблицы "
            "неизвестно. Каждый результат сверяю с актуальной доступной проекцией OpenMetadata.\n\n"
            "Примеры вопросов:\n"
            "- «Найди таблицу с полями weekly_rules, date_overrides и booking_horizon_days»\n"
            "- «Где хранятся model, input_json, output_json, success и error для LLM-запросов?»\n"
            "- «Найди структуру OCR-заданий с полями errors, failed_pages, ocr и markdown_content»"
        ),
    },
    {
        "id": "discovery",
        "title": "OpenMetadata · Discovery",
        "mode": "read",
        "description": (
            "Что делает: находит и перечисляет сущности каталога по имени, FQN, описанию, владельцу, сервису, "
            "домену и тегам.\n"
            "Как работает: объединяет поиск OpenMetadata, локальную актуальную проекцию и семантические "
            "результаты Dataset, ранжирует совпадения и применяет права доступа и фильтры пользователя.\n"
            "Для чего предназначен: для адресного поиска таблиц и обзора доступной части каталога.\n"
            "Примеры вопросов: «Покажи docker_postgres_meets.meets.public.calendar_connections, её владельца, "
            "домен и теги», «Найди все таблицы владельца owner_telephony в домене Telephony», «Какие таблицы "
            "домена ApplicationDocs содержат поле request_id?»"
        ),
        "prologue": (
            "Здравствуйте! Я нахожу доступные сущности каталога по имени, FQN, описанию, владельцу, "
            "сервису, домену, тегам и полям, а затем показываю подтверждённые метаданные.\n\n"
            "Примеры вопросов:\n"
            "- «Покажи docker_postgres_meets.meets.public.calendar_connections, её владельца, домен и теги»\n"
            "- «Найди все таблицы владельца owner_telephony в домене Telephony»\n"
            "- «Какие таблицы домена ApplicationDocs содержат поле request_id?»"
        ),
    },
    {
        "id": "impact_quality",
        "title": "OpenMetadata · Impact & Quality",
        "mode": "read",
        "description": (
            "Что делает: показывает зарегистрированные upstream/downstream-зависимости и проверки качества "
            "для выбранной таблицы.\n"
            "Как работает: однозначно определяет сущность, читает lineage на глубину до трёх уровней и table/column "
            "test cases непосредственно из OpenMetadata; отсутствующие связи не придумывает.\n"
            "Для чего предназначен: для анализа влияния изменений, поиска источников данных и контроля "
            "настроенных проверок качества.\n"
            "Примеры вопросов: «Покажи upstream и downstream для "
            "docker_postgres_meets.meets.public.meetings», «Зарегистрирован ли lineage для "
            "docker_postgres_asterisk.asterisk.asterisk.cdr?», «Какие test cases настроены для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs?»"
        ),
        "prologue": (
            "Здравствуйте! Я анализирую зарегистрированные upstream/downstream-зависимости таблиц и "
            "настроенные test cases, чтобы оценить влияние изменений и состояние качества данных.\n\n"
            "Примеры вопросов:\n"
            "- «Покажи upstream и downstream для docker_postgres_meets.meets.public.meetings»\n"
            "- «Зарегистрирован ли lineage для docker_postgres_asterisk.asterisk.asterisk.cdr?»\n"
            "- «Какие test cases настроены для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs?»"
        ),
    },
    {
        "id": "starter_questions",
        "title": "OpenMetadata · Starter Questions",
        "mode": "read",
        "description": (
            "Что делает: предлагает до пяти полезных вопросов, на которые текущий каталог действительно может "
            "ответить.\n"
            "Как работает: анализирует доступный пользователю снимок OpenMetadata — описания, домены, lineage, "
            "дату обновления и test cases — и показывает только вопросы, подтверждённые имеющимися данными.\n"
            "Для чего предназначен: для первого знакомства с каталогом и быстрого выбора осмысленного сценария "
            "работы.\n"
            "Примеры вопросов: «Предложи пять вопросов по текущему каталогу», «Сформируй стартовые вопросы "
            "для домена MeetingsScheduling», «Какие сценарии доступны для анализа lineage и качества?»"
        ),
        "prologue": (
            "Здравствуйте! Я предлагаю проверяемые стартовые вопросы по реальным сущностям текущего "
            "снимка OpenMetadata, чтобы быстро выбрать сценарий поиска, lineage или контроля качества.\n\n"
            "Примеры запросов:\n"
            "- «Предложи вопросы по таблицам домена MeetingsScheduling»\n"
            "- «Что можно выяснить о docker_postgres_meets.meets.public.meetings?»\n"
            "- «Подскажи сценарии анализа lineage и качества для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs»"
        ),
    },
    {
        "id": "governance",
        "title": "OpenMetadata · Governance",
        "mode": "write",
        "description": (
            "Что делает: помогает найти нужную таблицу и безопасно изменить её description или displayName.\n"
            "Как работает: запрос в чате сам ничего не меняет; агент находит сущность и направляет в отдельную "
            "форму Governance, где обязательны предварительный diff, подписанное подтверждение и повторная "
            "проверка версии перед записью в OpenMetadata.\n"
            "Для чего предназначен: для контролируемого редактирования метаданных суперпользователем без "
            "случайных или скрытых изменений.\n"
            "Примеры вопросов: «Добавь таблице docker_postgres_docs.docs.public.llm_logs описание: журнал "
            "LLM-запросов с model, input_json, output_json, success и error», «Добавь таблице "
            "docker_postgres_meets.meets.public.availability_profiles описание профилей доступности с "
            "weekly_rules, date_overrides и booking_horizon_days», «Установи displayName “OCR Jobs” для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs»"
        ),
        "prologue": (
            "Здравствуйте! Я помогаю подготовить контролируемое изменение description или displayName "
            "таблицы. Чат сам ничего не записывает: изменение выполняется только через Governance-форму с "
            "обязательными preview и confirm.\n\n"
            "Примеры запросов:\n"
            "- «Добавь таблице docker_postgres_docs.docs.public.llm_logs описание: журнал LLM-запросов с "
            "model, input_json, output_json, success и error»\n"
            "- «Добавь таблице docker_postgres_meets.meets.public.availability_profiles описание профилей "
            "доступности с weekly_rules, date_overrides и booking_horizon_days»\n"
            "- «Установи displayName “OCR Jobs” для "
            "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs»"
        ),
    },
)

OPENMETADATA_AGENT_ROLE_IDS = frozenset(role["id"] for role in OPENMETADATA_AGENT_ROLES)


def public_agent_roles() -> list[dict[str, str]]:
    return [{"id": role["id"], "mode": role["mode"], "description": role["description"]} for role in OPENMETADATA_AGENT_ROLES]


def managed_agent_id(owner_id: str, role_id: str) -> str:
    return hashlib.sha256(f"{owner_id}:{MANAGED_BY}:{role_id}".encode()).hexdigest()[:32]


def build_openmetadata_agent_dsl(role: dict[str, str]) -> dict[str, Any]:
    role_id = role["id"]
    component_id = f"OpenMetadata:{role_id.replace('_', '')}"
    message_id = f"Message:{role_id}"
    note_id = f"Note:{role_id}"
    return {
        "components": {
            "begin": {
                "obj": {
                    "component_name": "Begin",
                    "params": {"mode": "conversational", "prologue": role["prologue"]},
                },
                "downstream": [component_id],
                "upstream": [],
            },
            component_id: {
                "obj": {
                    "component_name": "OpenMetadata",
                    "params": {
                        "query": "{sys.query}",
                        "role": role_id,
                        "locale": "ru",
                        "outputs": {
                            "content": {"type": "string", "value": ""},
                            "result": {"type": "object", "value": {}},
                            "entity_ids": {"type": "array", "value": []},
                        },
                    },
                },
                "downstream": [message_id],
                "upstream": ["begin"],
            },
            message_id: {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": [f"{{{component_id}@content}}"]},
                },
                "downstream": [],
                "upstream": [component_id],
            },
        },
        "history": [],
        "retrieval": [],
        "path": [],
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
            "sys.history": [],
            "sys.date": "",
            "sys.openmetadata_context": [],
        },
        "variables": {},
        "meta": {"managed_by": MANAGED_BY, "role_id": role_id, "schema_version": 3},
        "graph": {
            "nodes": [
                {
                    "id": note_id,
                    "type": "noteNode",
                    "position": {"x": 40, "y": -320},
                    "width": 980,
                    "height": 420,
                    "measured": {"width": 980, "height": 420},
                    "dragging": False,
                    "selected": False,
                    "dragHandle": ".note-drag-handle",
                    "sourcePosition": "right",
                    "targetPosition": "left",
                    "data": {
                        "label": "Note",
                        "name": "Примечания к агенту",
                        "form": {"text": role["description"]},
                    },
                },
                {
                    "id": "begin",
                    "type": "beginNode",
                    "position": {"x": 40, "y": 160},
                    "data": {
                        "label": "Begin",
                        "name": "begin",
                        "form": {
                            "mode": "conversational",
                            "enablePrologue": True,
                            "prologue": role["prologue"],
                        },
                    },
                },
                {
                    "id": component_id,
                    "type": "ragNode",
                    "position": {"x": 340, "y": 160},
                    "data": {
                        "label": "OpenMetadata",
                        "name": "OpenMetadata",
                        "form": {"query": "{sys.query}", "role": role_id, "locale": "ru"},
                    },
                },
                {
                    "id": message_id,
                    "type": "messageNode",
                    "position": {"x": 650, "y": 160},
                    "data": {
                        "label": "Message",
                        "name": "Message",
                        "form": {"content": [f"{{{component_id}@content}}"]},
                    },
                },
            ],
            "edges": [
                {
                    "id": f"edge-begin-{component_id}",
                    "source": "begin",
                    "sourceHandle": "start",
                    "target": component_id,
                    "targetHandle": "end",
                },
                {
                    "id": f"edge-{component_id}-{message_id}",
                    "source": component_id,
                    "sourceHandle": "start",
                    "target": message_id,
                    "targetHandle": "end",
                },
            ],
        },
    }
