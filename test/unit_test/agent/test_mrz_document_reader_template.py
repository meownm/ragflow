import json
from pathlib import Path

import pytest


TEMPLATE = Path(__file__).parents[3] / "agent" / "templates" / "mrz_document_reader.json"


def load_validator():
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    script = template["dsl"]["components"]["CodeExec:MRZValidate"]["obj"]["params"]["script"]
    namespace = {}
    exec(script, namespace)
    return template, namespace["main"]


def test_template_is_wired_as_vision_ocr_then_deterministic_validation():
    template, _ = load_validator()
    assert template["dsl"]["path"] == []
    assert template["dsl"]["history"] == []
    assert template["dsl"]["retrieval"] == []
    components = template["dsl"]["components"]
    assert components["Agent:MRZOCR"]["obj"]["params"]["visual_files_var"] == "sys.files"
    assert components["CodeExec:MRZValidate"]["obj"]["params"]["arguments"] == {"ocr_result": "Agent:MRZOCR@content"}
    assert components["Message:MRZResult"]["obj"]["params"]["content"] == ["{CodeExec:MRZValidate@result}"]


def test_valid_td3_returns_attributes():
    _, validate = load_validator()
    lines = [
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
        "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
    ]
    result = json.loads(validate(json.dumps({"mrz_lines": lines})))
    assert result["valid"] is True
    assert result["format"] == "TD3"
    assert result["attributes"]["document_number"] == "L898902C3"
    assert result["attributes"]["surname"] == "ERIKSSON"
    assert result["attributes"]["given_names"] == "ANNA MARIA"


@pytest.mark.parametrize(
    ("expected_format", "lines"),
    [
        (
            "TD1",
            [
                "I<UTOD231458907<<<<<<<<<<<<<<<",
                "7408122F1204159UTO<<<<<<<<<<<6",
                "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
            ],
        ),
        (
            "TD2",
            [
                "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<",
                "D231458907UTO7408122F1204159<<<<<<<6",
            ],
        ),
    ],
)
def test_valid_td1_and_td2_examples(expected_format, lines):
    _, validate = load_validator()
    result = json.loads(validate(json.dumps({"mrz_lines": lines})))
    assert result["valid"] is True
    assert result["format"] == expected_format
    assert result["attributes"]["document_number"] == "D23145890"


def test_failed_checksum_never_returns_attributes():
    _, validate = load_validator()
    lines = [
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
        "L898902C30UTO7408122F1204159ZE184226B<<<<<10",
    ]
    result = json.loads(validate("OCR output:\n" + json.dumps({"mrz_lines": lines})))
    assert result["valid"] is False
    assert result["error"] == "checksum_failed"
    assert "document_number" in result["failed_checks"]
    assert result["attributes"] is None
    assert "mrz_lines" not in result


def test_rejects_incomplete_or_unsupported_layout():
    _, validate = load_validator()
    missing = json.loads(validate('{"mrz_lines":[]}'))
    malformed = json.loads(validate('{"mrz_lines":["ABC"]}'))
    assert missing == {"valid": False, "error": "mrz_not_found", "attributes": None}
    assert malformed["valid"] is False
    assert malformed["error"] == "unsupported_mrz_layout"
    assert malformed["attributes"] is None
    assert "mrz_lines" not in malformed


@pytest.mark.parametrize(
    ("expected_format", "lines"),
    [
        (
            "TD1",
            [
                "I<UTOD23145890<AB0<<<<<<<<<<<<",
                "7408122F1204159UTO<<<<<<<<<<<8",
                "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
            ],
        ),
        (
            "TD2",
            [
                "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<",
                "D23145890<UTO7408122F1204159AB0<<<<0",
            ],
        ),
    ],
)
def test_long_document_number_encoding(expected_format, lines):
    _, validate = load_validator()
    result = json.loads(validate(json.dumps({"mrz_lines": lines})))
    assert result["valid"] is True
    assert result["format"] == expected_format
    assert result["attributes"]["document_number"] == "D23145890AB"


def test_graph_forms_are_self_contained_and_match_component_params():
    template, _ = load_validator()
    components = template["dsl"]["components"]
    for node in template["dsl"]["graph"]["nodes"]:
        assert node["data"]["form"] == components[node["id"]]["obj"]["params"]
