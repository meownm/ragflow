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
