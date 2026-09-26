import json

import pytest

from business_documents.domain.change_stream import ChangeOperationStream


def test_completed_operation_is_available_before_the_full_change_plan_finishes():
    operation = {
        "operation_id": "op-1",
        "section_id": "2.1",
        "content": {"blocks": [{"type": "paragraph", "text": 'Цена с "скидкой"'}]},
    }
    output = json.dumps({"schema_version": "1", "operations": [operation], "acknowledged_no_change_event_ids": []}, ensure_ascii=False)
    end_of_operation = output.index('], "acknowledged_no_change_event_ids"')
    stream = ChangeOperationStream()

    assert stream.feed(output[:15]) == []
    assert stream.feed(output[15:end_of_operation]) == [operation]
    assert stream.feed(output[end_of_operation:]) == []


def test_parser_ignores_operations_word_inside_a_string_and_nested_content():
    output = '{"note":"\\"operations\\": [false]", "change_plan":{"operations":[{"content":{"blocks":[{"text":"{x}"}]},"section_id":"1"}]}}'
    stream = ChangeOperationStream()

    assert stream.feed(output) == [{"content": {"blocks": [{"text": "{x}"}]}, "section_id": "1"}]


def test_stream_limit_rejects_unbounded_model_output():
    stream = ChangeOperationStream(max_response_chars=4)

    with pytest.raises(ValueError, match="response limit"):
        stream.feed("12345")
