VALID_BPMN_SCENARIO = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="urn:ragflow:business-requirements:test">
  <bpmn:process id="Process_1" isExecutable="false">
    <bpmn:startEvent id="Start_1" />
    <bpmn:task id="Task_1" name="Основное действие" />
    <bpmn:exclusiveGateway id="Gateway_1" />
    <bpmn:task id="Success_1" name="Успешный результат" />
    <bpmn:task id="Failure_1" name="Обработка отказа" />
    <bpmn:endEvent id="End_1" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="Gateway_1" />
    <bpmn:sequenceFlow id="Flow_3" name="Успешный сценарий" sourceRef="Gateway_1" targetRef="Success_1" />
    <bpmn:sequenceFlow id="Flow_4" name="Негативный сценарий: отказ" sourceRef="Gateway_1" targetRef="Failure_1" />
    <bpmn:sequenceFlow id="Flow_5" sourceRef="Success_1" targetRef="End_1" />
    <bpmn:sequenceFlow id="Flow_6" sourceRef="Failure_1" targetRef="End_1" />
  </bpmn:process>
</bpmn:definitions>"""


def required_section_blocks(section_id: str, text: str) -> list[dict]:
    blocks: list[dict] = [{"type": "paragraph", "text": text}]
    if section_id == "4.1":
        blocks.append({"type": "plantuml", "source": "@startuml\nActor -> System: request\n@enduml"})
    elif section_id == "4.3":
        blocks.append({"type": "bpmn", "source": VALID_BPMN_SCENARIO})
    return blocks
