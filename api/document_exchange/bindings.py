"""Page reservations on the product owner's transaction and connection."""

from peewee import IntegrityError

from api.db.db_models import DocumentExchangeBinding

from .identity import binding_keys


class BindingConflict(Exception):
    pass


def occupancy(scope, keys):
    model = DocumentExchangeBinding
    rows = model.select().where((model.scope == scope) & (model.page_url_key.in_(keys) | model.eva_identity_key.in_(keys)))
    result = {}
    for row in rows:
        owner = {"kind": row.owner_kind, "document_id": row.document_id}
        result[row.page_url_key] = owner
        if row.eva_identity_key:
            result[row.eva_identity_key] = owner
    return result


def reserve(scope, kind, document_id, binding):
    model = DocumentExchangeBinding
    database = model._meta.database
    if not database.in_transaction():
        raise RuntimeError("Page reservations require the caller's transaction")
    page_key, identity = binding_keys(binding)
    owner = (model.scope == scope) & (model.owner_kind == kind) & (model.document_id == document_id)
    try:
        with database.atomic():
            values = {"page_url_key": page_key, "eva_identity_key": identity}
            existing = model.get_or_none(owner)
            if existing is not None:
                if existing.page_url_key != page_key or existing.eva_identity_key != identity:
                    model.update(**values).where(owner).execute()
            else:
                model.create(scope=scope, owner_kind=kind, document_id=document_id, **values)
    except IntegrityError as error:
        raise BindingConflict("Eva page is already linked to another document") from error


def release(scope, kind, document_id):
    model = DocumentExchangeBinding
    if not model._meta.database.in_transaction():
        raise RuntimeError("Page reservations require the caller's transaction")
    model.delete().where((model.scope == scope) & (model.owner_kind == kind) & (model.document_id == document_id)).execute()
