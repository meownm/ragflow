from __future__ import annotations

from pathlib import Path


def test_pyproject_contains_required_runtime_dependencies() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    required = [
        'fastapi = "^0.115.0"',
        'pydantic = "^2.8.0"',
        'pydantic-settings = "^2.4.0"',
        'numpy = "^1.26.0"',
        'torch = "^2.4.0"',
        'transformers = "^4.44.0"',
    ]
    for dep in required:
        assert dep in text


def test_pyproject_keeps_optional_extras_for_tone_and_gigaam() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    assert '[tool.poetry.extras]' in text
    assert 'tone = ["tone"]' in text
    assert 'gigaam = ["gigaam"]' in text
