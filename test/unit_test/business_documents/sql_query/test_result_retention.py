from business_documents.sql_query.result_retention import RESULT_RETENTION_MS, result_expires_at


def test_result_deadline_is_an_absolute_thirty_day_duration():
    assert RESULT_RETENTION_MS == 2_592_000_000
    assert result_expires_at(1_800_000_000_000) == 1_802_592_000_000
    assert result_expires_at(0) == RESULT_RETENTION_MS


def test_unknown_creation_time_does_not_extend_a_result_lifetime():
    assert result_expires_at(None) == RESULT_RETENTION_MS


def test_derived_result_cannot_outlive_its_source_or_extend_its_own_deadline():
    assert result_expires_at(100, source_deadline=RESULT_RETENTION_MS) == RESULT_RETENTION_MS
    assert result_expires_at(0, source_deadline=RESULT_RETENTION_MS + 100) == RESULT_RETENTION_MS
