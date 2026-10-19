import i18n, { changeLanguageAsync } from '@/locales/config';
import de from '@/locales/de';
import ja from '@/locales/ja';
import { act, render, renderHook, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import SchemaVisualEditor from './components/schema-editor/schema-visual-editor';
import { useTranslation } from './hooks/use-translation';
import { en } from './i18n/locales/en';
import { TranslationContext } from './i18n/translation-context';

beforeEach(async () => {
  i18n.addResourceBundle('de', 'translation', de.translation);
  i18n.addResourceBundle('ja', 'translation', ja.translation);
  await changeLanguageAsync('ru');
});

test('schema editor follows the application language without a separate provider', async () => {
  render(
    <I18nextProvider i18n={i18n}>
      <SchemaVisualEditor schema={{ type: 'object' }} onChange={jest.fn()} />
    </I18nextProvider>,
  );
  expect(
    screen.getByText('Пока не определено ни одного поля'),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: /Добавить поле/ }),
  ).toBeInTheDocument();
  await act(() => changeLanguageAsync('en'));
  expect(screen.getByRole('button', { name: /Add Field/ })).toBeInTheDocument();
  expect(
    screen.queryByText('Пока не определено ни одного поля'),
  ).not.toBeInTheDocument();
});

test('preserves an explicit translation context', async () => {
  const custom = { ...en, fieldDescription: 'Custom description' };
  const { result } = renderHook(() => useTranslation(), {
    wrapper: ({ children }) => (
      <TranslationContext.Provider value={custom}>
        {children}
      </TranslationContext.Provider>
    ),
  });
  expect(result.current).toBe(custom);
  await act(() => changeLanguageAsync('en'));
  expect(result.current).toBe(custom);
});

test('retains supported schema translations and an English fallback', async () => {
  const { result } = renderHook(() => useTranslation());
  expect(result.current.fieldDescription).toBe('Описание');
  await act(() => changeLanguageAsync('de'));
  expect(result.current.fieldDescription).not.toBe(en.fieldDescription);
  await act(() => changeLanguageAsync('ja'));
  expect(result.current).toBe(en);
});
