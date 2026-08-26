from tools.hooks.check_files import check_yaml


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
