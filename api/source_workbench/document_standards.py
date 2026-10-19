"""Content requirements from the architecture and technical specification standards."""

DOCUMENT_STANDARDS = {
    "architecture": """Стандарт «Архитектура» (приложение 3):
1. Назначение и цель.
2. Концептуальная архитектура: таблица описания продукта и диаграммы:
2.1. Уровень C1 (контекст): пользователи, внешние системы, цели взаимодействий.
2.2. Уровень C2 (контейнеры): обязательное представление подсистем/модулей.
2.3. Уровень C3 (компоненты): состав контейнеров.""",
    "technical_specification": """Стандарт «Техническое задание», приложение 4:
1. Назначение и цель.
2. Общие сведения: термины, сокращения, ссылки, сведения из бизнес-требований.
3. Компоненты подлежащие доработки: перечень и изменения.
4. Конфигурирование (настройки).
4.1. Конфигурации сервисов (дополнительные): функциональные настройки.
4.2. Права (ролевые модели): субъекты, ограничения, назначение/изменение/отзыв.
5. Прототипы интерфейсов и сценарии использования: ID, название, лица, предусловия, шаги/действия/реакции,
макеты; позитивные и негативные сценарии, запросы/ответы/отображение.
6. Логика работы: диаграммы последовательностей с кратким пояснением.
7. Модель данных: таблицы/поля, формат, обязательность, уникальность, описание, ER-диаграмма при наличии данных.
8. Сигнатура взаимодействия: endpoint-ы, форматы/примеры запросов/ответов, поля, коды/значения ошибок, алгоритмы.
Вместо описания допустим OpenAPI/XSD во внутренней сети банка на версию данного ТЗ, а не latest.
9. Журналирование: дополнительные требования; полные тела запросов/ответов при обоснованной необходимости.""",
}

STANDARD_EDIT_RULES = """
Standards define structure, not facts. Follow section order for new documents. Examples are not requirements.
Mark missing facts «Требует уточнения». Use Mermaid with source-supported facts.
Preserve unaffected text, section IDs and authorized scope. Do not restructure existing documents just for compliance;
pre-existing gaps outside the changed scope do not block partial edits.
"""

ARTICLE_DOCUMENT_STANDARDS = (
    "\nApply only the standard explicitly requested by the user: architecture or technical specification, regardless of source types. "
    "Для обычной статьи, обзора, ответа и других типов документов эти структуры не требуются. "
    "\n" + "\n\n".join(DOCUMENT_STANDARDS.values()) + STANDARD_EDIT_RULES
)

STANDARD_EVIDENCE_RULES = (
    " Preserve configuration, permissions, scenarios, data fields, API errors, logging, components and interactions when relevant to a technical specification or architecture task."
)
