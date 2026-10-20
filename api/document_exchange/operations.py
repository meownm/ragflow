"""Durable local import attempts. Callers own authorization and transactions.

Network reads happen between claim and completion. Only completion may commit
document writes. This journal does not make external publication atomic.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from peewee import IntegrityError, SqliteDatabase

from api.db.db_models import DocumentExchangeOperation as Operation


class OperationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _query(scope, consumer, actor, request_id):
    if not Operation._meta.database.in_transaction():
        raise RuntimeError("Exchange operations require the caller's transaction")
    query = Operation.select().where((Operation.scope == scope) & (Operation.consumer == consumer) & (Operation.actor_id == actor) & (Operation.request_id == request_id))
    return query if isinstance(Operation._meta.database, SqliteDatabase) else query.for_update()


def _row(scope, consumer, actor, request_id, context_id, request, initial):
    digest = hashlib.sha256(json.dumps({"context": context_id, "request": request}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    row = _query(scope, consumer, actor, request_id).first()
    if row is None:
        try:
            with Operation._meta.database.atomic():
                row = Operation.create(scope=scope, consumer=consumer, actor_id=actor, request_id=request_id, context_id=context_id, request_hash=digest, request=request, state=initial)
        except IntegrityError:
            row = _query(scope, consumer, actor, request_id).first()
            if row is None:
                raise
    if row.context_id != context_id or row.request_hash != digest:
        raise OperationError("IDEMPOTENCY_KEY_REUSED", "Идентификатор импорта уже использован с другим запросом")
    return row


def _view(row):
    return {"id": row.request_id, "state": row.state, "request": row.request, "result": row.result, "error": row.error}


def status(scope, consumer, actor, request_id, context_id):
    row = _query(scope, consumer, actor, request_id).first()
    if row is None or row.context_id != context_id:
        return None
    return _view(row)


def claim(scope, consumer, actor, request_id, context_id, request):
    row = _row(scope, consumer, actor, request_id, context_id, request, "failed")
    if row.state == "completed":
        return _view(row), None
    if row.state == "canceled":
        raise OperationError("IMPORT_CANCELED", "Этот импорт отменён. Выберите страницу для нового импорта")
    now = datetime.now(UTC).replace(tzinfo=None)
    until = row.lease_until.replace(tzinfo=None) if row.lease_until else None
    if row.state == "processing" and until and until > now:
        raise OperationError("IMPORT_IN_PROGRESS", "Импорт ещё выполняется. Проверьте результат позже")
    attempt = uuid4().hex
    row.state, row.attempt, row.lease_until, row.error = "processing", attempt, now + timedelta(minutes=6), {}
    row.save()
    return _view(row), attempt


def cancel(scope, consumer, actor, request_id, context_id, request):
    row = _row(scope, consumer, actor, request_id, context_id, request, "canceled")
    if row.state != "completed":
        row.state, row.attempt, row.lease_until = "canceled", None, None
        row.save()
    return _view(row)


def guard(scope, consumer, actor, request_id, attempt):
    row = _query(scope, consumer, actor, request_id).first()
    if row is None or row.state != "processing" or row.attempt != attempt:
        raise OperationError("IMPORT_INTERRUPTED", "Импорт отменён или уже выполняется другой попыткой")
    return row


def complete(row, result):
    if not Operation._meta.database.in_transaction():
        raise RuntimeError("Exchange completion requires the document transaction")
    row.state, row.result, row.attempt, row.lease_until, row.error = "completed", result, None, None, {}
    row.save()


def failed(scope, consumer, actor, request_id, attempt, error):
    row = _query(scope, consumer, actor, request_id).first()
    if row is not None and row.state == "processing" and row.attempt == attempt:
        row.state, row.error, row.attempt, row.lease_until = "failed", error, None, None
        row.save()
