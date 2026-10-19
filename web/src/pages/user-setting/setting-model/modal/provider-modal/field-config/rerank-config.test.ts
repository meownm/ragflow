import { LLMFactory } from '@/constants/llm';
import { FACTORIES_WITH_BASE_URL } from './generic-api-key-config';
import { getProviderConfig } from './get-provider-config';

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));

describe.each([LLMFactory.Jina, LLMFactory.Cohere])(
  '%s custom reranker configuration',
  (factory) => {
    it('passes the exact custom URL and model to verify and save', () => {
      const config = getProviderConfig(factory);
      const values = {
        instance_name: 'local-reranker',
        api_key: 'x',
        base_url: 'http://reranker:8013/v1',
        model_name: ' model-r ',
      };
      const modelInfo = [
        { model_name: 'model-r', model_type: ['rerank'], max_tokens: 0 },
      ];
      expect(FACTORIES_WITH_BASE_URL).toContain(factory);
      expect(config.verifyTransform!(values)).toMatchObject({
        apiKey: 'x',
        baseUrl: values.base_url,
        modelInfo,
      });
      expect(config.submitTransform!(values)).toMatchObject({
        instance_name: 'local-reranker',
        api_base: values.base_url,
        model_info: modelInfo,
      });
      const field = config.fields.find((item) => item.name === 'model_name')!;
      expect(typeof field.shouldRender).toBe('function');
      if (typeof field.shouldRender === 'function') {
        expect(field.shouldRender(values)).toBe(true);
        expect(field.shouldRender({})).toBe(false);
      }
    });

    it('preserves cloud registration without choosing a custom model', () => {
      const config = getProviderConfig(factory);
      const values = { api_key: 'cloud-key', instance_name: 'cloud' };
      expect(config.verifyTransform!(values).modelInfo).toBeUndefined();
      expect(config.submitTransform!(values)).not.toHaveProperty('model_info');
      expect(config.submitTransform!(values)).toMatchObject({
        api_key: 'cloud-key',
        api_base: '',
      });
    });
  },
);
