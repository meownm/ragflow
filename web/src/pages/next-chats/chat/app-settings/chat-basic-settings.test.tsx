import { Form } from '@/components/ui/form';
import { TooltipProvider } from '@/components/ui/tooltip';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useForm } from 'react-hook-form';
import ChatBasicSetting from './chat-basic-settings';

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('@/components/avatar-name-description', () => ({
  AvatarNameDescription: () => null,
}));
jest.mock('@/components/knowledge-base-item', () => ({
  KnowledgeBaseFormField: () => null,
}));
jest.mock('@/components/llm-setting-items/next', () => ({
  LlmSettingFieldItems: () => null,
}));
jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));

function Settings({
  permission = 'me',
  submit,
}: {
  permission?: 'me' | 'team';
  submit: (value: unknown) => void;
}) {
  const form = useForm({
    defaultValues: { permission, prompt_config: { prologue: '' } },
  });
  return (
    <TooltipProvider>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(submit)}>
          <ChatBasicSetting />
          <button type="submit">Save</button>
        </form>
      </Form>
    </TooltipProvider>
  );
}

test('keeps a new chat private and submits an explicit team choice', async () => {
  const submit = jest.fn();
  render(<Settings submit={submit} />);
  expect(screen.getByRole('radio', { name: 'me' })).toBeChecked();
  fireEvent.click(screen.getByRole('radio', { name: 'team' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ permission: 'team' }),
      expect.anything(),
    ),
  );
});

test('loads a shared chat and can make it private again', async () => {
  const submit = jest.fn();
  render(<Settings permission="team" submit={submit} />);
  expect(screen.getByRole('radio', { name: 'team' })).toBeChecked();
  fireEvent.click(screen.getByRole('radio', { name: 'me' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ permission: 'me' }),
      expect.anything(),
    ),
  );
});
