import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

import peewee
import pytest
from werkzeug.security import check_password_hash, generate_password_hash


@pytest.fixture
def password_store(monkeypatch, tmp_path):
    user_db = peewee.SqliteDatabase(tmp_path / "users.sqlite")

    class User(peewee.Model):
        id = peewee.CharField(primary_key=True)
        password = peewee.CharField()
        access_token = peewee.CharField()
        update_time = peewee.BigIntegerField(null=True)
        update_date = peewee.DateTimeField(null=True)

        class Meta:
            database = user_db

    user_db.create_tables([User])
    User.create(id="user", password=generate_password_hash("old"), access_token="a" * 32)
    user_db.close()
    models = ModuleType("api.db.db_models")
    models.DB, models.User, models.Tenant, models.UserTenant = user_db, User, User, User
    monkeypatch.setitem(sys.modules, models.__name__, models)
    service = ModuleType("api.db.services.common_service")
    service.CommonService = type("CommonService", (), {})
    monkeypatch.setitem(sys.modules, service.__name__, service)
    misc = ModuleType("common.misc_utils")
    misc.get_uuid = lambda: uuid.uuid4().hex
    monkeypatch.setitem(sys.modules, misc.__name__, misc)
    settings = ModuleType("common.settings")
    monkeypatch.setitem(sys.modules, settings.__name__, settings)
    path = Path(__file__).resolve().parents[5] / "api/db/services/user_service.py"
    spec = importlib.util.spec_from_file_location("password_rotation_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        yield module.UserService, User, user_db
    finally:
        user_db.close()


def test_password_and_token_rotate_in_one_transaction(password_store):
    service, model, _db = password_store
    token = service.update_user_password("user", "new")
    stored = model.get_by_id("user")
    assert stored.access_token == token and token != "a" * 32
    assert check_password_hash(stored.password, "new")
    assert model.select().where(model.access_token == "a" * 32).count() == 0


def test_password_rotation_rolls_back_on_write_failure(password_store, monkeypatch):
    service, model, db = password_store
    db.execute_sql("CREATE TRIGGER reject_rotation BEFORE UPDATE ON user BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    with pytest.raises(peewee.IntegrityError):
        service.update_user_password("user", "new")
    stored = model.get_by_id("user")
    assert stored.access_token == "a" * 32
    assert check_password_hash(stored.password, "old")


def test_password_rotation_rejects_missing_user(password_store):
    service, _model, _db = password_store
    with pytest.raises(ValueError):
        service.update_user_password("missing", "new")
