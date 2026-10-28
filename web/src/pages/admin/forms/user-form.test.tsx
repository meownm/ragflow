import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import useCreateUserForm from './user-form';

const originalScroll = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'scrollIntoView',
);
beforeAll(() =>
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: jest.fn(),
  }),
);
afterAll(() =>
  originalScroll
    ? Object.defineProperty(
        HTMLElement.prototype,
        'scrollIntoView',
        originalScroll,
      )
    : Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'),
);

jest.mock('../utils', () => ({ IS_ENTERPRISE: true }));
jest.mock('@/services/admin-service', () => ({
  listRoles: async () => ({
    data: { data: { roles: [{ id: 'reader', role_name: 'Reader' }] } },
  }),
}));
jest.mock('react-i18next', () => ({
  ...jest.requireActual('react-i18next'),
  useTranslation: () => ({ t: (key: string) => key }),
}));

test('the selected enterprise role is included in the validated user form', async () => {
  const submit = jest.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  function Form() {
    const { id, FormComponent } = useCreateUserForm({
      defaultValues: {
        email: 'qa@example.test',
        password: 'example123',
        confirmPassword: 'example123',
      },
    });
    return (
      <>
        <FormComponent onSubmit={submit} />
        <button type="submit" form={id}>
          Create QA user
        </button>
      </>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Form />
    </QueryClientProvider>,
  );
  fireEvent.keyDown(await screen.findByRole('combobox'), { key: 'ArrowDown' });
  fireEvent.click(await screen.findByRole('option', { name: 'Reader' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create QA user' }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ role: 'Reader' }),
      expect.anything(),
    ),
  );
  client.clear();
});

test.each([5, 6])(
  'creation preserves its existing password boundary at %i characters',
  (length) => {
    let check: ReturnType<typeof useCreateUserForm>['schema'] | undefined;
    function Form() {
      check = useCreateUserForm().schema;
      return null;
    }
    render(<Form />);
    const result = check!.safeParse({
      email: 'qa@example.test',
      password: 'a'.repeat(length),
      confirmPassword: 'a'.repeat(length),
    });
    expect(result.success).toBe(length >= 6);
  },
);
