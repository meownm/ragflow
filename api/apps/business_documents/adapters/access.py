"""Serialize user role changes without exposing User rows to the application."""

from datetime import datetime

from api.db.db_models import User
from common.time_utils import current_timestamp


class PeeweeUserRoleWriter:
    def transaction(self):
        return User._meta.database.atomic()

    def _require_transaction(self):
        if not User._meta.database.in_transaction():
            raise RuntimeError("User role changes require the caller's transaction")

    def lock_user(self, user_id):
        self._require_transaction()
        query = User.select(User.id, User.nickname, User.is_superuser).where(User.id == user_id)
        if getattr(User._meta.database, "for_update", False):
            query = query.for_update()
        user = query.first()
        return user.__data__.copy() if user is not None else None

    def set_role(self, user_id, role):
        self._require_transaction()
        User.update(business_document_role=role, update_time=current_timestamp(), update_date=datetime.now()).where(User.id == user_id).execute()
