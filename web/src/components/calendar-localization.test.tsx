import i18n, { changeLanguageAsync } from '@/locales/config';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { enUS } from 'date-fns/locale/en-US';
import { I18nextProvider } from 'react-i18next';
import { Calendar as OriginCalendar } from './originui/calendar';
import TimeRangePicker from './originui/time-range-picker';
import { Calendar } from './ui/calendar';
import { DatePicker } from './ui/date-picker';

beforeEach(async () => {
  await changeLanguageAsync('ru');
});

test.each([
  ['standard', Calendar],
  ['origin', OriginCalendar],
])(
  '%s calendar translates captions and accessible controls and switches language',
  async (_name, Component) => {
    render(
      <I18nextProvider i18n={i18n}>
        <Component
          mode="single"
          month={new Date(2026, 9, 1)}
          selected={new Date(2026, 9, 19)}
        />
      </I18nextProvider>,
    );
    expect(screen.getByText(/октябрь 2026/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Следующий месяц' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /19 октября 2026.*выбрано/i }),
    ).toBeInTheDocument();
    await act(() => changeLanguageAsync('en'));
    expect(screen.getByText(/October 2026/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Next month' }),
    ).toBeInTheDocument();
  },
);

test('honors an explicit calendar locale and accessible label override', () => {
  render(
    <Calendar
      month={new Date(2026, 9, 1)}
      locale={enUS}
      labels={{ labelNext: () => 'Custom next' }}
    />,
  );
  expect(screen.getByText('October 2026')).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Custom next' }),
  ).toBeInTheDocument();
});

test('month picker uses Russian month names', () => {
  render(<DatePicker picker="month" value={new Date(2026, 9, 19)} />);
  fireEvent.click(screen.getByRole('textbox'));
  expect(screen.getByRole('button', { name: 'Янв' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Окт' })).toBeInTheDocument();
});

test('range picker translates dates and presets', () => {
  render(
    <TimeRangePicker
      onSelect={jest.fn()}
      selectDateRange={{
        from: new Date(2026, 9, 1),
        to: new Date(2026, 9, 19),
      }}
    />,
  );
  expect(screen.getByRole('button')).toHaveTextContent(/окт/);
  fireEvent.click(screen.getByRole('button'));
  expect(screen.getByRole('button', { name: 'Сегодня' })).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Последние 7 дней' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'С начала года' }),
  ).toBeInTheDocument();
});
