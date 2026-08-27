from asr_service.jobs.worker import Worker


def test_apply_output_contract_keeps_segments_when_enabled():
    result = {"transcript": "ok", "segments": [{"start": 0.0, "end": 1.0, "text": "ok"}]}

    out = Worker._apply_output_contract(result, include_segments=True)

    assert out == result
    assert "segments" in out


def test_apply_output_contract_removes_segments_when_disabled():
    result = {"transcript": "ok", "segments": [{"start": 0.0, "end": 1.0, "text": "ok"}], "extra": 1}

    out = Worker._apply_output_contract(result, include_segments=False)

    assert "segments" not in out
    assert out["transcript"] == "ok"
    assert out["extra"] == 1
