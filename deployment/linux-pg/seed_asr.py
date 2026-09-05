"""Provision the bundled T-One service as the default ASR for tenants without one."""

import json

from common import settings
from common.constants import LLMType
from common.misc_utils import get_uuid


def main() -> None:
    settings.init_settings()

    from api.db.db_models import DB, Tenant, TenantModel, TenantModelInstance, TenantModelProvider

    configured = 0
    skipped = 0
    with DB.connection_context(), DB.atomic():
        for tenant in Tenant.select().where(Tenant.status == "1").order_by(Tenant.id).for_update():
            if tenant.asr_id:
                skipped += 1
                continue

            provider = TenantModelProvider.get_or_none(tenant_id=tenant.id, provider_name="New API")
            if provider is None:
                provider = TenantModelProvider.create(id=get_uuid(), tenant_id=tenant.id, provider_name="New API")

            instance = TenantModelInstance.get_or_none(provider_id=provider.id, instance_name="t-one-local")
            if instance is None:
                instance = TenantModelInstance.create(
                    id=get_uuid(),
                    provider_id=provider.id,
                    instance_name="t-one-local",
                    api_key="local",
                    extra=json.dumps({"base_url": "http://t-one-asr:9011/v1"}),
                )
            model = TenantModel.get_or_none(
                provider_id=provider.id,
                instance_id=instance.id,
                model_type=LLMType.SPEECH2TEXT.value,
                model_name="t-one",
            )
            if model is None:
                TenantModel.create(
                    id=get_uuid(),
                    provider_id=provider.id,
                    instance_id=instance.id,
                    model_name="t-one",
                    model_type=LLMType.SPEECH2TEXT.value,
                    extra=json.dumps({"max_tokens": 0}),
                )

            Tenant.update(asr_id="t-one@t-one-local@New API").where(Tenant.id == tenant.id).execute()
            configured += 1

    print(f"t_one_asr_configured={configured}")
    print(f"tenants_with_existing_asr={skipped}")


if __name__ == "__main__":
    main()
