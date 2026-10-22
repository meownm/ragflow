"""Compile the model's syntax examples using an explicitly selected local JVM renderer.

Set RAGFLOW_PLANTUML_JAR to the real PlantUML jar and JAVA to its Java executable.
No application database, model substitution or shared data is involved.
"""

import os
from pathlib import Path
import re
import subprocess

import pytest


@pytest.fixture
def compile_source():
    jar = os.environ.get("RAGFLOW_PLANTUML_JAR")
    if not jar:
        pytest.skip("Requires RAGFLOW_PLANTUML_JAR and a local Java runtime")
    assert Path(jar).is_file(), "Configured PlantUML jar is missing"

    def compile_(source):
        return subprocess.run(
            [os.environ.get("JAVA", "java"), "-jar", jar, "-charset", "UTF-8", "-syntax"],
            input=source.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )

    return compile_


@pytest.mark.parametrize("prompt", ["draft", "change_planner"])
def test_prompt_diagram_examples_compile(prompt, compile_source):
    root = Path(__file__).resolve().parents[2]
    text = (root / "agent/business_requirements/prompts" / f"{prompt}.v1.md").read_text(encoding="utf-8")
    examples = re.findall(r"```plantuml\n(.*?)\n```", text, re.DOTALL)
    assert examples
    for source in examples:
        if not source.startswith("@startuml"):
            source = "@startuml\nstart\n" + source + "\n@enduml"
        result = compile_source(source)
        assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")


def test_mixed_component_and_sequence_is_detected_by_real_renderer(compile_source):
    # The failed BT rehearsal mixed component declarations with alt/else.
    declarations = '@startuml\nactor "Читатель" as Reader\ncomponent "Портал" as Portal\n'
    mixed = declarations + "alt Одобрение\nReader --> Portal : Запрос\nelse Возврат\nPortal --> Reader : Данные\nend\n@enduml"
    assert compile_source(mixed).returncode != 0
    component = declarations + "Reader --> Portal : Запрос\nPortal --> Reader : Данные\n@enduml"
    assert compile_source(component).returncode == 0
