"""Create the initial superuser and idempotently configure internal T-One ASR."""

import json
import os

from common import settings


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    settings.init_settings()

    from api.db.db_models import DB
    from api.db.init_data import init_superuser
    from api.db.services import UserService
    from api.db.services.tenant_model_instance_service import TenantModelInstanceService
    from api.db.services.tenant_model_provider_service import TenantModelProviderService
    from api.db.services.tenant_model_service import TenantModelService
    from api.db.services.user_service import TenantService
    from common.constants import LLMType

    email = required("BOOTSTRAP_ADMIN_EMAIL")
    password = required("BOOTSTRAP_ADMIN_PASSWORD")
    nickname = os.environ.get("BOOTSTRAP_ADMIN_NICKNAME", "RAGFlow Admin").strip() or "RAGFlow Admin"

    with DB.connection_context():
        init_superuser(nickname=nickname, email=email, password=password)
        users = list(UserService.query(email=email))
        if len(users) != 1:
            raise RuntimeError(f"Expected one bootstrap user for {email}, found {len(users)}")
        user = users[0]
        if not user.is_superuser:
            raise RuntimeError("Bootstrap user exists but is not a superuser")

        provider_name = "New API"
        instance_name = "t-one-local"
        model_name = "t-one"
        provider = TenantModelProviderService.get_by_tenant_id_and_provider_name(user.id, provider_name)
        if provider is None:
            TenantModelProviderService.insert(tenant_id=user.id, provider_name=provider_name)
            provider = TenantModelProviderService.get_by_tenant_id_and_provider_name(user.id, provider_name)
        if provider is None:
            raise RuntimeError("Failed to create T-One model provider")

        instance = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider.id, instance_name)
        instance_extra = json.dumps({"base_url": "http://t-one-asr:9011/v1"})
        if instance is None:
            TenantModelInstanceService.create_instance(
                provider_id=provider.id,
                instance_name=instance_name,
                api_key="x",
                extra=instance_extra,
            )
            instance = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider.id, instance_name)
        else:
            TenantModelInstanceService.update_by_id(instance.id, {"api_key": "x", "extra": instance_extra, "status": "active"})
        if instance is None:
            raise RuntimeError("Failed to create T-One model instance")

        model = TenantModelService.get_by_provider_id_and_instance_id_and_model_type_and_model_name(
            provider.id,
            instance.id,
            LLMType.SPEECH2TEXT.value,
            model_name,
        )
        if model is None:
            TenantModelService.insert(
                model_name=model_name,
                provider_id=provider.id,
                instance_id=instance.id,
                model_type=LLMType.SPEECH2TEXT.value,
                status="active",
                extra=json.dumps({"max_tokens": 0}),
            )
        else:
            TenantModelService.update_by_id(model.id, {"status": "active"})

        asr_id = f"{model_name}@{instance_name}@{provider_name}"
        TenantService.update_by_id(user.id, {"asr_id": asr_id})

        exists, tenant = TenantService.get_by_id(user.id)
        if not exists or tenant.asr_id != asr_id:
            raise RuntimeError("Failed to set T-One as the tenant default ASR model")

    print(f"bootstrap_admin={email}")
    print("bootstrap_superuser=true")
    print("bootstrap_asr=t-one@t-one-local@New API")


if __name__ == "__main__":
    main()
