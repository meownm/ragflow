import { LLMFactory } from '@/constants/llm';
import i18n, { changeLanguageAsync } from '@/locales/config';
import { act, renderHook } from '@testing-library/react';
import { useProviderFields } from '../hooks/use-provider-fields';
import { getProviderConfig } from './get-provider-config';

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));

beforeEach(async () => {
  await changeLanguageAsync('ru');
});

test('all provider labels, placeholders and validation keys have Russian translations', () => {
  for (const factory of Object.values(LLMFactory)) {
    const config = getProviderConfig(factory);
    for (const field of config.fields) {
      const texts = [
        field.label,
        field.placeholder,
        field.tooltip,
        field.validation?.message,
      ];
      for (const value of texts) {
        const key = typeof value === 'function' ? value(factory) : value;
        if (key)
          expect({
            factory,
            key,
            exists: i18n.exists(`setting.${key}`, { lng: 'ru' }),
          }).toEqual({ factory, key, exists: true });
      }
      if (field.name === 'model_type') {
        for (const option of field.options ?? []) {
          expect(
            i18n.exists(`setting.modelTypes.${option.value}`, { lng: 'ru' }),
          ).toBe(true);
        }
      }
    }
  }
});

test('provider field labels and model types follow language changes without changing values', async () => {
  const { result } = renderHook(() =>
    useProviderFields({
      llmFactory: LLMFactory.GoogleCloud,
      hideWhenInstanceExists: () => true,
    }),
  );
  const options = () =>
    result.current.fields.find((field) => field.name === 'model_type')?.options;
  const values = options()?.map((option) => option.value);
  expect(options()?.some((option) => option.label === 'Чат')).toBe(true);
  expect(
    result.current.fields.find((field) => field.name === 'instance_name')
      ?.label,
  ).toBe('Название экземпляра');
  await act(() => changeLanguageAsync('en'));
  expect(options()?.map((option) => option.value)).toEqual(values);
  expect(options()?.some((option) => option.label === 'Chat')).toBe(true);
});
