import importlib.util
import sys
from pathlib import Path


def _load_admin_config_module():
    module_path = Path(__file__).parents[3] / "admin" / "server" / "config.py"
    spec = importlib.util.spec_from_file_location("admin_service_config_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_configurations_includes_postgres_and_asr(monkeypatch):
    config_module = _load_admin_config_module()
    monkeypatch.setattr(
        config_module,
        "read_config",
        lambda _: {
            "postgres": {"host": "postgres", "port": 5432},
            "asr": {
                "name": "t-one-asr",
                "host": "t-one-asr",
                "port": 9011,
                "health_path": "/health/ready",
            },
        },
    )

    configs = config_module.load_configurations("unused.yaml")

    assert [config.name for config in configs] == ["postgres", "t-one-asr"]
    assert configs[0].to_dict()["extra"]["meta_type"] == "postgres"
    assert configs[0].detail_func_name == "get_postgres_status"
    assert configs[1].service_type == "asr"
    assert configs[1].to_dict()["extra"]["health_path"] == "/health/ready"
    assert configs[1].detail_func_name == "check_asr_alive"
