# Playwright Test README

## One-line command (run everything)

```bash
BASE_URL=http://localhost:9222 E2E_ADMIN_EMAIL=admin@ragflow.io E2E_ADMIN_PASSWORD=admin PW_FIXTURE_DEBUG=1 uv run pytest -q test/playwright -s --junitxml=/tmp/playwright-full.xml
```

## Common commands

Run smoke subset:

```bash
BASE_URL=http://localhost:9222 E2E_ADMIN_EMAIL=admin@ragflow.io E2E_ADMIN_PASSWORD=admin uv run pytest -q test/playwright -m smoke -s --junitxml=/tmp/playwright-smoke.xml
```

Run full suite:

```bash
BASE_URL=http://localhost:9222 E2E_ADMIN_EMAIL=admin@ragflow.io E2E_ADMIN_PASSWORD=admin uv run pytest -q test/playwright -s --junitxml=/tmp/playwright-full.xml
```

Run one file in isolation:

```bash
BASE_URL=http://localhost:9222 E2E_ADMIN_EMAIL=admin@ragflow.io E2E_ADMIN_PASSWORD=admin uv run pytest -q test/playwright/e2e/test_next_apps_agent.py -s --junitxml=/tmp/playwright-agent.xml
```

Run one test case in isolation:

```bash
BASE_URL=http://localhost:9222 E2E_ADMIN_EMAIL=admin@ragflow.io E2E_ADMIN_PASSWORD=admin uv run pytest -q test/playwright/e2e/test_next_apps_chat.py::test_chat_create_select_dataset_and_receive_answer_flow -s -x --junitxml=/tmp/playwright-chat-one.xml
```

## Argument reference

- `uv run`: run `pytest` inside the project-managed Python environment.
- `pytest`: test runner.
- `-q`: quieter output.
- `test/playwright`: run the whole Playwright suite folder.
- `test/playwright/...py`: run one file only.
- `::test_name`: run one test function only.
- `-m smoke`: run tests with `@pytest.mark.smoke`.
- `-s`: show `print()` and fixture logs live.
- `-x`: stop at first failure.
- `--junitxml=/tmp/<name>.xml`: write machine-readable results to XML.

## Environment variables used in commands

- `BASE_URL`: app URL (this suite is currently run against `http://localhost:9222`).
- `E2E_ADMIN_EMAIL`: login email for authenticated flows.
- `E2E_ADMIN_PASSWORD`: login password for authenticated flows.
- `PW_FIXTURE_DEBUG=1`: optional; prints fixture provisioning details.

Authenticated journeys reuse one existing account: `qa@infiniflow.org` by default
(or `REG_EMAIL_BASE`). Set `SEEDED_USER_PASSWORD` to that account's current password.
For the persistent local service at `http://127.0.0.1:9380`, browser and API tests
also read `.env.test.local` in the repository root (ignored by Git):

```dotenv
RAGFLOW_TEST_BASE_URL=http://127.0.0.1:9380
SEEDED_USER_EMAIL=qa@infiniflow.org
SEEDED_USER_PASSWORD=<current strong password>
```

The local file is used only for that loopback service; explicit environment
credentials take precedence. API tests encrypt the password for the login request.
Also set `SEEDED_USER_EMAIL` to use another provisioned account;
`E2E_ADMIN_EMAIL` and `E2E_ADMIN_PASSWORD` supply the existing admin account
in the commands above. The fixture never registers users or generates login suffixes.
A missing password fails fixture setup. An unknown, disabled account or wrong
password must fail its login journey. The registration test password `123` is
not assumed to be the password of an existing account.

Registration journeys create accounts and require a disposable application stack
with `RAGFLOW_TEST_DISPOSABLE=1`. Set `REG_EMAIL_UNIQUE=1` for a fresh registration
address. Dispose of that stack after the run; do not run registration journeys
against the persistent local database.

## Output and artifacts

- JUnit XML files are written to `/tmp/...` from `--junitxml`.
- Screenshots and diagnostics are written under:
  - `test/playwright/artifacts/`
