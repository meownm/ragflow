import { z } from 'zod';
import { NameFormSchema } from './name-form-field';

jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));
jest.mock('@/components/ragflow-form', () => ({ RAGFlowFormItem: () => null }));

const schema = z.object(NameFormSchema);

it.each(['', '   ', '\n\t', 'x'.repeat(256)])(
  'rejects invalid title %p',
  (name) => {
    expect(schema.safeParse({ name }).success).toBe(false);
  },
);

it.each(['Я'.repeat(255), '🚀'.repeat(255)])(
  'counts characters rather than UTF-8 bytes or UTF-16 units',
  (name) => {
    expect(schema.parse({ name: ` ${name} ` }).name).toBe(name);
  },
);
