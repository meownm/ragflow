"""Run mocked browser journeys against the built SPA without a live backend."""

import argparse
import os
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUITES = [
    "test/playwright/e2e/test_document_constructor_ui.py",
    "test/playwright/e2e/test_sql_revision_ui.py",
    "test/playwright/e2e/test_sql_workspace_ui.py",
    "test/playwright/e2e/test_sql_candidate_graph_ui.py",
    "test/playwright/e2e/test_sql_view_continuity_ui.py",
    "test/playwright/e2e/test_sql_graph_races_ui.py",
    "test/playwright/e2e/test_sql_dialogue_ui.py",
    "test/playwright/e2e/test_sql_exit_cancellation_ui.py",
    "test/playwright/e2e/test_sql_connection_ui.py",
    "test/playwright/e2e/test_sql_prequery_connection_ui.py",
    "test/playwright/e2e/test_sql_project_registry_ui.py",
    "test/playwright/e2e/test_sql_history_ui.py",
    "test/playwright/e2e/test_business_documents_access_ui.py",
    "test/playwright/e2e/test_business_documents_ui.py",
    "test/playwright/e2e/test_business_documents_background_ui.py",
    "test/playwright/e2e/test_linked_documents_ui.py",
    "test/playwright/e2e/test_technical_specifications_ui.py",
    "test/playwright/e2e/test_technical_specifications_visual_workspace_ui.py",
    "test/playwright/e2e/test_alignment_ui.py",
    "test/playwright/e2e/test_document_quality_ui.py",
    "test/playwright/e2e/test_navigation_visibility_admin.py",
    "test/playwright/e2e/test_section_access_ui.py",
    "test/playwright/e2e/test_auth_boundaries_ui.py",
    "test/playwright/e2e/test_managed_datasets_access_ui.py",
    "test/playwright/e2e/test_managed_settings_access_ui.py",
    "test/playwright/e2e/test_upload_boundaries_ui.py",
]


class SPAServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class SPAHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith(("/api/", "/v1/")):
            self.send_error(503, "Browser regression requires mocked API responses")
            return
        if not Path(self.translate_path(path)).is_file() and not Path(path).suffix:
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    parser.add_argument("--dist-root", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--suite", action="append", choices=SUITES)
    parser.add_argument("pytest_args", nargs="*")
    args = parser.parse_args()
    dist = args.dist_root.resolve()
    if not (dist / "index.html").is_file():
        parser.error("Build the frontend first: cd web && pnpm build")
    server = SPAServer(("127.0.0.1", 0), partial(SPAHandler, directory=str(dist)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "RAGFLOW_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "PW_BROWSER": args.browser,
        "SQL_BROWSER_BUILD_ROOT": str(dist),
        "PW_ARTIFACTS_DIR": os.getenv("PW_ARTIFACTS_DIR", str(ROOT / "test/playwright/artifacts" / args.browser)),
    }
    pytest_args = list(args.pytest_args)
    if not any(value == "--output" or value.startswith("--output=") for value in pytest_args):
        pytest_args = ["--output", str(Path(env["PW_ARTIFACTS_DIR"]) / "playwright"), *pytest_args]
    try:
        return subprocess.call([sys.executable, "-m", "pytest", *(args.suite or SUITES), *pytest_args], cwd=ROOT, env=env)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
