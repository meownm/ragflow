import { FormFieldType } from '@/components/dynamic-form';
import { LLMFactory } from '@/constants/llm';
import type { IModelInfo } from '@/interfaces/request/llm';
import type { ProviderConfig } from '../types';
import { GenericApiKeyConfig } from './generic-api-key-config';
import { LocalLlmConfigs } from './local-llm-configs';
import { ProviderConfigMap } from './provider-config-map';

function customRerankModel(
  values: Record<string, any>,
): IModelInfo[] | undefined {
  const name = values.model_name?.trim();
  return values.base_url && name
    ? [{ model_name: name, model_type: ['rerank'], max_tokens: 0 }]
    : undefined;
}

/**
 * Get the configuration for the given factory
 * First look up in ProviderConfigMap, then LocalLlmConfigs, finally fall back to GenericApiKeyConfig
 */
export function getProviderConfig(llmFactory: string): ProviderConfig {
  // Check whether it is a special factory (11 in ModalMap)
  // Among which AzureOpenAI/VolcEngine/GoogleCloud/TencentCloud/XunFeiSpark/BaiduYiYan/FishAudio are in ProviderConfigMap
  // Bedrock/MinerU/PaddleOCR/OpenDataLoader are out of the merge scope and use the original modal

  if (ProviderConfigMap[llmFactory]) {
    return ProviderConfigMap[llmFactory];
  }

  if (LocalLlmConfigs[llmFactory]) {
    return LocalLlmConfigs[llmFactory];
  }

  if (llmFactory === LLMFactory.Jina || llmFactory === LLMFactory.Cohere) {
    return {
      ...GenericApiKeyConfig,
      llmFactory,
      fields: [
        ...GenericApiKeyConfig.fields,
        {
          name: 'model_name',
          label: 'modelName',
          type: FormFieldType.Text,
          required: false,
          placeholder: 'modelNameMessage',
          shouldRender: (values) => Boolean(values.base_url),
        },
      ],
      verifyTransform: (values) => ({
        ...GenericApiKeyConfig.verifyTransform!(values),
        modelInfo: customRerankModel(values),
      }),
      submitTransform: (values) => ({
        ...GenericApiKeyConfig.submitTransform!(values),
        ...(customRerankModel(values)
          ? { model_info: customRerankModel(values) }
          : {}),
      }),
    };
  }

  // Generic ApiKey modal
  return {
    ...GenericApiKeyConfig,
    llmFactory,
  };
}
