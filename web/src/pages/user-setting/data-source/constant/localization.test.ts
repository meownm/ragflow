import en from '@/locales/en';
import ru from '@/locales/ru';
import i18next from 'i18next';
import {
  DataSourceKey,
  getDataSourceBaseFields,
  getDataSourceFieldsWithExtras,
} from './index';
import { S3Constant } from './s3-constant';

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));
jest.mock('../component/box-token-field', () => () => null);
jest.mock('../component/gmail-token-field', () => () => null);
jest.mock('../component/google-drive-token-field', () => () => null);

test.each([
  ['ru', 'Укажите ARN роли AWS'],
  ['en', 'AWS Role ARN is required'],
])(
  'S3 role validation identifies the missing ARN in %s',
  async (language, message) => {
    const i18n = i18next.createInstance();
    await i18n.init({ lng: language, resources: { en, ru } });
    const field = S3Constant(i18n.t).find(
      (field) => field.name === 'config.credentials.aws_role_arn',
    );
    const values = {
      config: {
        bucket_type: 's3',
        credentials: { authentication_method: 'iam_role' },
      },
    };
    expect(field?.shouldRender?.(values)).toBe(true);
    expect(field?.customValidate?.('', values)).toBe(message);
    expect(
      field?.customValidate?.('arn:aws:iam::123456789012:role/Test', values),
    ).toBe(true);
  },
);

test('all connector forms retain their submitted field names and option values across languages', async () => {
  const i18n = i18next.createInstance();
  await i18n.init({ lng: 'ru', resources: { en, ru } });
  const russian = i18n.getFixedT('ru');
  const english = i18n.getFixedT('en');
  for (const source of Object.values(DataSourceKey)) {
    const ruFields = getDataSourceFieldsWithExtras(source, russian);
    const enFields = getDataSourceFieldsWithExtras(source, english);
    const contract = (fields: typeof ruFields) =>
      fields.map((field) => ({
        name: field.name,
        type: field.type,
        required: field.required,
        defaultValue: field.defaultValue,
        options: field.options?.map((option) => option.value),
        min: field.validation?.min,
        max: field.validation?.max,
      }));
    expect({ source, fields: contract(ruFields) }).toEqual({
      source,
      fields: contract(enFields),
    });
    expect(ruFields.some((field) => field.label.startsWith('setting.'))).toBe(
      false,
    );
  }
  expect(
    getDataSourceBaseFields(russian).find((field) => field.name === 'name')
      ?.label,
  ).toBe('Название');
  const fields = getDataSourceFieldsWithExtras(DataSourceKey.REST_API, russian);
  const auth = fields.find((field) => field.name === 'config.auth_type');
  expect(auth?.label).toBe('Тип аутентификации');
  expect(auth?.options?.map((option) => option.value)).toEqual([
    'none',
    'api_key_header',
    'bearer',
    'basic',
  ]);
  const requiredKey = fields.find(
    (field) => field.name === 'config.auth_config.header_name',
  );
  expect(
    requiredKey?.customValidate?.('', {
      config: { auth_type: 'api_key_header' },
    }),
  ).toMatch(/[А-Яа-я]/);
});
