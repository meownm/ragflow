import i18n, { changeLanguageAsync } from '@/locales/config';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { NextMessageInput } from './message-input/next';
import { TimePicker } from './ui/time-picker';
import { ToggleList } from './ui/toggle-list';

jest.mock('./ui/audio-button', () => ({ AudioButton: () => null }));
jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));

test('model list defaults switch language without clearing the search query', async () => {
  await changeLanguageAsync('ru');
  render(
    <ToggleList
      btnText="Модели"
      options={[{ label: 'Test model', value: 'test-model' }]}
      searchable
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Модели' }));
  fireEvent.change(screen.getByPlaceholderText('Поиск…'), {
    target: { value: 'missing' },
  });
  expect(screen.getByText('Результатов нет.')).toBeInTheDocument();
  await act(() => changeLanguageAsync('en'));
  expect(screen.getByPlaceholderText('Search...')).toHaveValue('missing');
  expect(screen.getByText('No results found')).toBeInTheDocument();
});

test('empty list translates its default and preserves explicit empty overrides', async () => {
  await changeLanguageAsync('ru');
  const { rerender } = render(<ToggleList btnText="Модели" options={[]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Модели' }));
  expect(screen.getByText('Нет вариантов')).toBeInTheDocument();
  await act(() => changeLanguageAsync('en'));
  expect(screen.getByText('No options')).toBeInTheDocument();
  rerender(<ToggleList btnText="Модели" options={[]} emptyText={null} />);
  expect(screen.queryByText('No options')).not.toBeInTheDocument();
});

test('list honors explicit search and no-match labels', async () => {
  await changeLanguageAsync('ru');
  render(
    <ToggleList
      btnText="Модели"
      options={[{ label: 'Test model', value: 'test-model' }]}
      searchable
      searchPlaceholder="Мой поиск"
      noResultsText="Ничего не подходит"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Модели' }));
  fireEvent.change(screen.getByPlaceholderText('Мой поиск'), {
    target: { value: 'missing' },
  });
  await act(() => changeLanguageAsync('en'));
  expect(screen.getByPlaceholderText('Мой поиск')).toHaveValue('missing');
  expect(screen.getByText('Ничего не подходит')).toBeInTheDocument();
});

test('time picker translates its default and honors a caller placeholder', async () => {
  await changeLanguageAsync('ru');
  const { rerender } = render(<TimePicker />);
  expect(screen.getByPlaceholderText('Выберите время')).toBeInTheDocument();
  await act(() => changeLanguageAsync('en'));
  expect(screen.getByPlaceholderText('Select time')).toBeInTheDocument();
  rerender(<TimePicker placeholder="Время запуска" />);
  await act(() => changeLanguageAsync('ru'));
  expect(screen.getByPlaceholderText('Время запуска')).toBeInTheDocument();
});

test('chat controls use Russian text and update when the language changes', async () => {
  await changeLanguageAsync('ru');
  render(
    <I18nextProvider i18n={i18n}>
      <NextMessageInput
        disabled={false}
        value="Текст"
        sendDisabled={false}
        sendLoading={false}
        conversationId="localization-test"
        onPressEnter={jest.fn()}
        onInputChange={jest.fn()}
        showReasoning
      />
    </I18nextProvider>,
  );
  expect(
    screen.getByRole('button', { name: 'Отправить сообщение' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Прикрепить файл' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Рассуждение' }),
  ).toBeInTheDocument();
  expect(screen.getByText('Перетащите файлы сюда')).toBeInTheDocument();
  await act(() => changeLanguageAsync('en'));
  expect(
    screen.getByRole('button', { name: 'Send message' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Attach file' }),
  ).toBeInTheDocument();
});
