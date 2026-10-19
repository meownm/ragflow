import i18n, { changeLanguageAsync } from '@/locales/config';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createRef } from 'react';
import { I18nextProvider } from 'react-i18next';
import { DynamicForm, FormFieldType, generateSchema } from './dynamic-form';

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));

beforeEach(async () => {
  await changeLanguageAsync('ru');
});

test('translating fields preserves entered values and submission', async () => {
  const ref = createRef<import('./dynamic-form').DynamicFormRef>();
  const onSubmit = jest.fn();
  const field = { name: 'name', label: 'Название', type: FormFieldType.Text };
  const { rerender } = render(
    <I18nextProvider i18n={i18n}>
      <DynamicForm.Root
        ref={ref}
        fields={[field]}
        onSubmit={onSubmit}
        defaultValues={{ name: 'Saved' }}
      />
    </I18nextProvider>,
  );
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'Unsaved input' },
  });
  await act(() => changeLanguageAsync('en'));
  rerender(
    <I18nextProvider i18n={i18n}>
      <DynamicForm.Root
        ref={ref}
        fields={[{ ...field, label: 'Name' }]}
        onSubmit={onSubmit}
        defaultValues={{ name: 'Saved' }}
      />
    </I18nextProvider>,
  );
  expect(screen.getByRole('textbox')).toHaveValue('Unsaved input');
  expect(ref.current?.getValues()).toEqual({ name: 'Unsaved input' });
  await act(async () => ref.current?.submit());
  await waitFor(() =>
    expect(onSubmit).toHaveBeenCalledWith({ name: 'Unsaved input' }),
  );
});

test('a genuinely changed record still resets the form defaults', () => {
  const fields = [
    { name: 'name', label: 'Название', type: FormFieldType.Text },
  ];
  const { rerender } = render(
    <DynamicForm.Root
      fields={fields}
      onSubmit={jest.fn()}
      defaultValues={{ name: 'First' }}
    />,
  );
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'Edited' },
  });
  rerender(
    <DynamicForm.Root
      fields={fields}
      onSubmit={jest.fn()}
      defaultValues={{ name: 'Second' }}
    />,
  );
  expect(screen.getByRole('textbox')).toHaveValue('Second');
});

test('required, numeric and format errors are Russian', () => {
  const cases = [
    {
      field: {
        name: 'name',
        label: 'Название',
        type: FormFieldType.Text,
        required: true,
      },
      value: '',
      error: 'Заполните поле «Название»',
    },
    {
      field: {
        name: 'size',
        label: 'Размер',
        type: FormFieldType.Number,
        validation: { min: 1 },
      },
      value: 0,
      error: 'Значение не может быть меньше 1',
    },
    {
      field: { name: 'email', label: 'Почта', type: FormFieldType.Email },
      value: 'invalid',
      error: 'Введите корректный адрес электронной почты',
    },
  ];
  for (const { field, value, error } of cases) {
    const result = generateSchema([field]).safeParse({ [field.name]: value });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error.issues[0].message).toBe(error);
  }
});
