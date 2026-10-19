import dayjs from 'dayjs';
import i18next from 'i18next';
import i18n, { changeLanguageAsync, initLanguage } from './config';
import en from './en';
import fr from './fr';
import ru from './ru';

function strings(value: object, prefix = ''): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, item]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return typeof item === 'string'
        ? [[path, item]]
        : Object.entries(strings(item, path));
    }),
  );
}

afterEach(() => localStorage.clear());

test('Russian covers the English dictionary and preserves interpolation variables', () => {
  const english = strings(en.translation);
  const russian = strings(ru.translation);
  const variables = (value: string) =>
    [...value.matchAll(/{{\s*([^{}]+?)\s*}}/g)].map((match) => match[1]).sort();
  expect(Object.keys(english).filter((key) => !(key in russian))).toEqual([]);
  for (const [key, value] of Object.entries(english)) {
    expect({ key, variables: variables(russian[key]) }).toEqual({
      key,
      variables: variables(value),
    });
  }
});

test('starts in Russian and translates previously missing labels and errors', async () => {
  localStorage.clear();
  await initLanguage();
  expect(i18n.resolvedLanguage).toBe('ru');
  expect(document.documentElement.lang).toBe('ru');
  expect(i18n.t('common.viewMore')).toBe('Показать больше');
  expect(i18n.t('setting.listModels')).toBe('Список моделей');
  expect(i18n.t('skills.title')).toBe('Навыки');
  expect(i18n.t('knowledgeDetails.processDuration')).toBe('Продолжительность');
  expect(i18n.t('error_boundary.title')).toBe('Что-то пошло не так');
  expect(i18n.t('message.error', { error: '503' })).toBe('Ошибка: 503');
  expect(dayjs('2026-10-19').format('MMMM')).toBe('октябрь');
});

test('honors a saved language and changes back to Russian', async () => {
  localStorage.setItem('lng', 'en');
  await initLanguage();
  expect(i18n.t('common.viewMore')).toBe('View more');
  expect(document.documentElement.lang).toBe('en');
  await changeLanguageAsync('ru');
  expect(localStorage.getItem('lng')).toBe('ru');
  expect(i18n.t('common.viewMore')).toBe('Показать больше');
});

test('ships Russian resources and falls back through Russian to English', async () => {
  expect(i18n.hasResourceBundle('ru', 'translation')).toBe(true);
  const isolated = i18next.createInstance();
  await isolated.init({
    lng: 'fr',
    fallbackLng: i18n.options.fallbackLng,
    resources: {
      fr,
      ru,
      en: {
        translation: { ...en.translation, regressionLabel: 'English fallback' },
      },
    },
  });
  expect(isolated.t('regressionLabel')).toBe('English fallback');
  expect(isolated.t('header.openMetadata')).toBe(
    ru.translation.header.openMetadata,
  );
});
