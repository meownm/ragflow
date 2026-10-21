"""CI executes the authored SQL oracles on its disposable PostgreSQL service."""

from pathlib import Path

import yaml


def test_native_preflight_runs_the_complete_corpus_and_real_database_lane():
    root = Path(__file__).resolve().parents[3]
    job = yaml.safe_load((root / ".github/workflows/tests.yml").read_text(encoding="utf-8"))["jobs"]["ragflow_preflight"]
    step = next(step for step in job["steps"] if step.get("name") == "SQL project golden requirements")
    assert "if" not in step and not step.get("continue-on-error")
    assert step["env"]["SQL_PROJECT_TEST_DISPOSABLE"] == "1"
    assert step["env"]["SQL_PROJECT_TEST_POSTGRES_DSN"] == "postgresql://regression:regression-only@127.0.0.1:${{ job.services.regression-postgres.ports['5432'] }}/regression"
    assert "test/evals/sql_projects test/integration/test_sql_projects_postgres.py" in step["run"]
    assert "--junitxml=sql-project-goldens.xml" in step["run"]
    assert "set -euo pipefail" in step["run"]
    artifact = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
    assert artifact["if"] == "always()"
    assert "sql-project-goldens.xml" in artifact["with"]["path"].splitlines()
