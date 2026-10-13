import pytest

from tools.hooks.check_files import check_eof, check_merge_conflicts, check_mixed_line_endings, check_trailing_whitespace, check_yaml


def test_check_yaml_accepts_compose_merge_tags(tmp_path):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        """services:
  app:
    depends_on: !override
      db:
        condition: service_healthy
    ports: !reset []
""",
        encoding="utf-8",
    )

    assert check_yaml([compose_file]) == 0


def test_check_yaml_rejects_unknown_tags(tmp_path):
    yaml_file = tmp_path / "invalid.yml"
    yaml_file.write_text("value: !unsupported thing\n", encoding="utf-8")

    assert check_yaml([yaml_file]) == 1


@pytest.mark.parametrize("check", [check_eof, check_trailing_whitespace, check_mixed_line_endings, check_merge_conflicts])
@pytest.mark.parametrize("fix", [False, True])
@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n\0bytes \r\n\nend", b"\xffbytes \r\n\nend"])
def test_text_checks_preserve_binary_artifacts(tmp_path, check, fix, data):
    artifact = tmp_path / "screenshot.png"
    artifact.write_bytes(data)
    assert check([artifact], fix=fix) == 0
    assert artifact.read_bytes() == data


@pytest.mark.parametrize(
    "check,data", [(check_eof, b"text"), (check_trailing_whitespace, b"text \n"), (check_mixed_line_endings, b"first\r\nsecond\n"), (check_merge_conflicts, b"<<<<<<< ours\n=======\n>>>>>>> theirs\n")]
)
def test_text_checks_still_reject_invalid_text(tmp_path, check, data):
    source = tmp_path / "source.txt"
    source.write_bytes(data)
    assert check([source]) == 1
