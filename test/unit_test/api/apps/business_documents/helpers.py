from contextlib import contextmanager
import sys
from types import ModuleType
from typing import Iterator

import common


def append_document_event(*args, **kwargs):
    """Insert a fixture event through the same transactional writer as runtime."""
    from api.apps.business_documents.runtime import document_writer

    with document_writer.transaction():
        return document_writer.append_event(*args, **kwargs)


def record_confirmed_proposal(document, proposal_id):
    """Seed a pre-upgrade confirmed decision for migration and saved-plan tests."""
    from api.apps.business_documents.runtime import document_queries, document_writer
    from common.misc_utils import get_uuid

    with document_writer.transaction():
        current = document_writer.lock_document(document["document_id"])
        decision_id, event_id = get_uuid(), get_uuid()
        document_writer.insert_decision(id=decision_id, document_id=current["id"], proposal_id=proposal_id, actor_id=current["owner_id"], decision="ACCEPTED")
        current = document_writer.update_document(current, {"state_version": current["state_version"] + 1})
        document_writer.append_event(
            current["id"],
            current["state_version"],
            "ProposalDecided",
            "USER",
            current["owner_id"],
            {"proposal_id": proposal_id, "decision_id": decision_id, "decision": "ACCEPTED"},
            event_id,
            event_id=event_id,
        )
    return {"accepted": True, "state_version": current["state_version"], "document": document_queries.get_document(current["id"], current["owner_id"])}


@contextmanager
def temporary_common_settings(settings_module: ModuleType) -> Iterator[None]:
    """Install a collection-time settings stub without leaking it to other tests."""

    missing = object()
    previous_module = sys.modules.get(settings_module.__name__, missing)
    previous_attribute = getattr(common, "settings", missing)
    sys.modules[settings_module.__name__] = settings_module
    common.settings = settings_module
    try:
        yield
    finally:
        if previous_module is missing:
            sys.modules.pop(settings_module.__name__, None)
        else:
            sys.modules[settings_module.__name__] = previous_module
        if previous_attribute is missing:
            delattr(common, "settings")
        else:
            common.settings = previous_attribute


VALID_ACTIVITY_SCENARIO = """@startuml
start
:Основное действие;
if (Проверка успешна?) then (Да)
  :Успешный результат;
else (Нет)
  :Обработка отказа;
endif
stop
@enduml"""


def required_section_blocks(section_id: str, text: str) -> list[dict]:
    blocks: list[dict] = [{"type": "paragraph", "text": text}]
    if section_id == "4.1":
        blocks.append({"type": "plantuml", "source": "@startuml\nActor -> System: request\n@enduml"})
    elif section_id == "4.3":
        blocks.append({"type": "plantuml", "source": VALID_ACTIVITY_SCENARIO})
    return blocks
