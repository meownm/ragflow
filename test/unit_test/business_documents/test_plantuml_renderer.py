import httpx
import pytest

from business_documents.adapters.plantuml import PlantUmlRenderer


@pytest.fixture
def response(monkeypatch):
    original = httpx.Client
    calls = []

    def configure(status, headers, body=b"<svg><g/></svg>"):
        def serve(request):
            calls.append(request)
            return httpx.Response(status, headers=headers, content=body)

        monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(serve), **kwargs))
        return calls

    return configure


def test_success_is_actual_svg_not_just_http_200(response):
    calls = response(200, {"content-type": "image/svg+xml; charset=utf-8", "x-powered-by": "PlantUML 1.2026.6"})
    result = PlantUmlRenderer("http://renderer").check("@startuml\nstart\nstop\n@enduml")
    assert result.status == "valid" and result.version == "PlantUML 1.2026.6"
    assert calls[0].url == "http://renderer/svg/" and calls[0].method == "POST"


@pytest.mark.parametrize("status", [400, 401, 403, 413, 429, 500, 502, 503])
def test_generic_http_error_is_never_classified_as_bad_model_syntax(response, status):
    response(status, {})
    assert PlantUmlRenderer("http://renderer").check("@startuml\n@enduml").status == "unavailable"


@pytest.mark.parametrize("line,expected", [("0", 1), ("1", 2), ("2", None), ("bad", None), ("-1", None), ("99999999999999999999", None)])
def test_standard_syntax_diagnostics_are_bounded_and_one_based(response, line, expected):
    response(400, {"x-plantuml-diagram-error": "Cannot find if", "x-plantuml-diagram-error-line": line})
    result = PlantUmlRenderer("http://renderer").check("@startuml\n@enduml")
    assert result.status == "syntax_error" and result.line == expected


@pytest.mark.parametrize(
    "mime,body",
    [("text/html", b"<html/>"), ("image/svg+xml", b"garbage"), ("image/svg+xml", b"<svg/>"), ("image/svg+xml", b"<html><body/></html>"), ("image/svg+xml", b"x" * 2_000_001)],
    ids=["html", "malformed", "empty", "wrong-root", "oversized"],
)
def test_false_success_and_oversized_body_are_rejected(response, mime, body):
    response(200, {"content-type": mime}, body)
    assert PlantUmlRenderer("http://renderer").check("@startuml\n@enduml").status == "unavailable"


@pytest.mark.parametrize("directive", ["include", "includeurl", "includesub", "include_once", "include_many", "IMPORT", "InClUdEuRl"])
def test_include_and_oversized_source_are_not_sent_to_renderer(response, directive):
    calls = response(200, {"content-type": "image/svg+xml"})
    renderer = PlantUmlRenderer("http://renderer")
    assert renderer.check(f"@startuml\n!{directive} https://invalid.invalid/x\n@enduml").status == "unavailable"
    assert renderer.check("x" * 50_001).status == "unavailable"
    assert not calls


def test_renderer_outage_is_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.Client, "stream", fail)
    assert PlantUmlRenderer("http://renderer").check("@startuml\n@enduml").status == "unavailable"
