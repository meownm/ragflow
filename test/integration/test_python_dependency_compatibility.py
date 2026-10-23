"""Offline boundary checks for the patched third-party dependency sources."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

import pytest


@contextlib.contextmanager
def api_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            self.respond_moodle(query)

        def respond_moodle(self, query):
            if query.get("wstoken") != ["synthetic-token"]:
                payload = {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"}
            else:
                assert query["wsfunction"] == ["core_course_get_courses"]
                payload = [
                    {
                        "id": 7,
                        "shortname": "QA",
                        "categoryid": 1,
                        "fullname": "Synthetic course",
                        "displayname": "QA course",
                        "summary": "",
                        "summaryformat": 1,
                        "format": "topics",
                        "startdate": 0,
                        "enddate": 0,
                        "timecreated": 1000,
                        "courseformatoptions": [{"name": "numsections", "value": 2}],
                    }
                ]
            self.respond(payload)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Type", "application/x-www-form-urlencoded").startswith("application/x-www-form-urlencoded"):
                self.respond_moodle(parse_qs(urlsplit(self.path).query) | parse_qs(raw.decode()))
                return
            body = json.loads(raw)
            assert body["messages"][0]["content"] == "dependency fixture"
            self.respond(
                {
                    "id": "synthetic",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "compatible"}, "finish_reason": "stop"}],
                }
            )

        def respond(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_moodle_rest_structuring_and_authentication_errors():
    from moodle import Moodle, MoodleException

    with api_server() as base:
        client = Moodle(base, "synthetic-token")
        try:
            courses = client.core.course.get_courses()
            assert courses[0].id == 7
            assert courses[0].fullname == "Synthetic course"
            assert courses[0].timecreated == 1000
            assert courses[0].courseformatoptions[0].value == 2
            client.token = "invalid"
            with pytest.raises(MoodleException):
                client.core.course.get_courses()
        finally:
            client.session.close()


def test_crawl4ai_calls_the_single_litellm_implementation(monkeypatch):
    import litellm
    from crawl4ai.utils import perform_completion_with_backoff

    monkeypatch.setattr(litellm, "drop_params", litellm.drop_params)
    with api_server() as base:
        result = perform_completion_with_backoff("openai/fixture", "dependency fixture", "synthetic-key", base_url=base + "/v1", max_attempts=1, extra_args={"max_retries": 0})
    assert result.choices[0].message.content == "compatible"


@pytest.mark.asyncio
async def test_crawl4ai_browser_and_markdown(monkeypatch):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.async_database import async_db_manager

    connections = []
    connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    # RobotsParser's transaction contexts do not close SQLite connections.
    # Keep and close the real connections created by this disposable fixture.
    monkeypatch.setattr(sqlite3, "connect", track_connection)
    try:
        config = BrowserConfig(headless=True, verbose=False, extra_args=["--no-sandbox"])
        run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, word_count_threshold=1)
        async with AsyncWebCrawler(config=config) as crawler:
            result = await crawler.arun(url="raw:<html><body><h1>Dependency verification</h1><p>Bounded crawler fixture content.</p></body></html>", config=run)
    finally:
        await async_db_manager.cleanup()
        for connection in connections:
            connection.close()
    assert result.success
    assert "Dependency verification" in result.markdown
    assert "Bounded crawler fixture" in result.cleaned_html


def test_agentrun_sandbox_public_api_without_memory_extra():
    from agentrun.sandbox import CodeInterpreterSandbox, CodeLanguage, Sandbox, Template, TemplateInput, TemplateType
    from agentrun.utils.config import Config
    from agentrun.utils.exception import ServerError

    assert CodeLanguage.PYTHON.value == "python"
    assert all(callable(value) for value in (CodeInterpreterSandbox, Sandbox, Template, TemplateInput, Config, ServerError))
    assert TemplateType is not None


@pytest.mark.parametrize("publication_id", ["123456", "123456,789012"])
def test_scholarly_citation_ids_survive_python_313_regex_fix(monkeypatch, publication_id):
    from datetime import date

    from scholarly import scholarly
    from scholarly.data_types import PublicationSource

    class Results(list):
        total_results = 1

    calls = []
    expected = {"bib": {"title": "Synthetic citation"}}

    def search_citedby(**kwargs):
        calls.append(kwargs)
        return Results([expected])

    monkeypatch.setattr(scholarly, "search_citedby", search_citedby)
    year = date.today().year
    publication = {
        "container_type": "Publication",
        "num_citations": 1001,
        "bib": {"title": "Synthetic paper", "pub_year": str(year)},
        "source": PublicationSource.PUBLICATION_SEARCH_SNIPPET,
        "citedby_url": f"/scholar?cites={publication_id}&hl=en",
    }
    assert list(scholarly.citedby(publication)) == [expected]
    assert calls == [{"publication_id": publication_id, "year_low": year, "year_high": year}]
