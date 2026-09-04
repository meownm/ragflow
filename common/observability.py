#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#

import contextlib
import contextvars
import logging
import os
import time
import uuid
from collections.abc import Iterator, Mapping
from typing import Any


_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("ragflow_observability_context", default={})
_provider = None


def _clean_id(value: Any, limit: int = 128) -> str:
    return str(value or "").strip()[:limit]


def new_id() -> str:
    return uuid.uuid4().hex


def get_log_context() -> dict[str, str]:
    result = dict(_context.get())
    try:
        from opentelemetry import trace

        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            result["trace_id"] = format(span_context.trace_id, "032x")
            result["span_id"] = format(span_context.span_id, "016x")
    except ImportError:
        pass
    return result


@contextlib.contextmanager
def bind_context(**values: Any) -> Iterator[dict[str, str]]:
    merged = dict(_context.get())
    merged.update({key: _clean_id(value) for key, value in values.items() if value})
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


def telemetry_headers() -> dict[str, str]:
    context = get_log_context()
    headers = {
        "x-request-id": context.get("request_id", ""),
        "x-correlation-id": context.get("correlation_id", ""),
        "x-interaction-id": context.get("interaction_id", ""),
        "x-session-id": context.get("session_id", ""),
    }
    try:
        from opentelemetry import propagate

        propagate.inject(headers)
    except ImportError:
        pass
    return {key: value for key, value in headers.items() if value}


def inject_queue_context(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    result = dict(message)
    result["_telemetry"] = telemetry_headers()
    return result


@contextlib.contextmanager
def consume_queue_context(message: Mapping[str, Any], span_name: str) -> Iterator[None]:
    carrier = message.get("_telemetry") if isinstance(message, Mapping) else None
    carrier = carrier if isinstance(carrier, Mapping) else {}
    values = {
        "request_id": carrier.get("x-request-id"),
        "correlation_id": carrier.get("x-correlation-id"),
        "interaction_id": carrier.get("x-interaction-id"),
        "session_id": carrier.get("x-session-id"),
        "job_id": message.get("id"),
    }
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.trace import SpanKind

        parent = propagate.extract(carrier)
        with bind_context(**values), trace.get_tracer("ragflow.queue").start_as_current_span(
            span_name, context=parent, kind=SpanKind.CONSUMER
        ):
            yield
    except ImportError:
        with bind_context(**values):
            yield


def configure_otel(service_name: str, service_version: str = "unknown"):
    """Configure OTLP tracing when OTEL_EXPORTER_OTLP_ENDPOINT is present."""
    global _provider
    os.environ["OTEL_SERVICE_NAME"] = service_name
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if not endpoint or _provider is not None:
        return _provider
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=Resource.create(
                {
                    "service.name": service_name,
                    "service.version": service_version,
                    "service.namespace": "ragflow",
                }
            ),
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
        trace.set_tracer_provider(provider)
        _provider = provider
        logging.info("OpenTelemetry tracing enabled for %s", service_name)
    except Exception:
        logging.exception("OpenTelemetry setup failed; service will continue without exporting traces")
    return _provider


def install_quart_instrumentation(app, service_name: str) -> None:
    from quart import g, request

    @app.before_request
    async def _observability_before_request():
        from opentelemetry import propagate, trace
        from opentelemetry.trace import SpanKind

        request_id = _clean_id(request.headers.get("X-Request-ID")) or new_id()
        correlation_id = _clean_id(request.headers.get("X-Correlation-ID")) or request_id
        interaction_id = _clean_id(request.headers.get("X-Interaction-ID"))
        session_id = _clean_id(request.headers.get("X-Session-ID"))
        g.observability_started_at = time.perf_counter()
        g.observability_context_manager = bind_context(
            request_id=request_id,
            correlation_id=correlation_id,
            interaction_id=interaction_id,
            session_id=session_id,
        )
        g.observability_context_manager.__enter__()
        parent = propagate.extract(dict(request.headers))
        g.observability_span = trace.get_tracer(service_name).start_span(
            f"{request.method} {request.path}", context=parent, kind=SpanKind.SERVER
        )
        g.observability_span_context = trace.use_span(g.observability_span, end_on_exit=False)
        g.observability_span_context.__enter__()
        g.request_id = request_id
        g.correlation_id = correlation_id
        g.interaction_id = interaction_id

    @app.after_request
    async def _observability_after_request(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "")
        span = getattr(g, "observability_span", None)
        if span:
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("url.path", request.path)
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR))
        logging.info(
            "http.request.complete method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.path,
            response.status_code,
            (time.perf_counter() - getattr(g, "observability_started_at", time.perf_counter())) * 1000,
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} or response.status_code >= 400:
            try:
                from api.db.services.audit_service import record_audit_event

                user = getattr(g, "user", None)
                record_audit_event(
                    action=f"http.{request.method.lower()}",
                    outcome="failure" if response.status_code >= 400 else "success",
                    actor_id=getattr(user, "id", None),
                    actor_type="USER" if user else "ANONYMOUS",
                    auth_type=getattr(g, "auth_type", None),
                    object_type="http_route",
                    object_id=request.path,
                    reason_code=f"HTTP_{response.status_code}" if response.status_code >= 400 else None,
                    error_id=getattr(g, "error_id", None),
                    metadata={
                        "method": request.method,
                        "path": request.path,
                        "status_code": response.status_code,
                        "duration_ms": round(
                            (time.perf_counter() - getattr(g, "observability_started_at", time.perf_counter())) * 1000,
                            2,
                        ),
                    },
                )
            except Exception:
                logging.exception("Failed to append request audit event")
        return response

    @app.teardown_request
    async def _observability_teardown_request(exception):
        span = getattr(g, "observability_span", None)
        if span:
            if exception:
                span.record_exception(exception)
            span.end()
        span_context = getattr(g, "observability_span_context", None)
        if span_context:
            span_context.__exit__(None, None, None)
        context_manager = getattr(g, "observability_context_manager", None)
        if context_manager:
            context_manager.__exit__(None, None, None)


def install_flask_instrumentation(app, service_name: str) -> None:
    from flask import g, request

    @app.before_request
    def _observability_before_request():
        from opentelemetry import propagate, trace
        from opentelemetry.trace import SpanKind

        request_id = _clean_id(request.headers.get("X-Request-ID")) or new_id()
        correlation_id = _clean_id(request.headers.get("X-Correlation-ID")) or request_id
        g.observability_started_at = time.perf_counter()
        g.observability_context_manager = bind_context(
            request_id=request_id,
            correlation_id=correlation_id,
            interaction_id=_clean_id(request.headers.get("X-Interaction-ID")),
            session_id=_clean_id(request.headers.get("X-Session-ID")),
        )
        g.observability_context_manager.__enter__()
        parent = propagate.extract(dict(request.headers))
        g.observability_span = trace.get_tracer(service_name).start_span(
            f"{request.method} {request.path}", context=parent, kind=SpanKind.SERVER
        )
        g.observability_span_context = trace.use_span(g.observability_span, end_on_exit=False)
        g.observability_span_context.__enter__()
        g.request_id = request_id
        g.correlation_id = correlation_id

    @app.after_request
    def _observability_after_request(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "")
        span = getattr(g, "observability_span", None)
        if span:
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("url.path", request.path)
            span.set_attribute("http.response.status_code", response.status_code)
        logging.info(
            "http.request.complete method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.path,
            response.status_code,
            (time.perf_counter() - getattr(g, "observability_started_at", time.perf_counter())) * 1000,
        )
        return response

    @app.teardown_request
    def _observability_teardown_request(exception):
        span = getattr(g, "observability_span", None)
        if span:
            if exception:
                span.record_exception(exception)
            span.end()
        span_context = getattr(g, "observability_span_context", None)
        if span_context:
            span_context.__exit__(None, None, None)
        context_manager = getattr(g, "observability_context_manager", None)
        if context_manager:
            context_manager.__exit__(None, None, None)
