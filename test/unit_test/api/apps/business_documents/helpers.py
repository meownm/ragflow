from contextlib import contextmanager
import sys
from types import ModuleType
from typing import Iterator

import common


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
