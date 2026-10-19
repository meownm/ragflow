import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RenameForm } from './rename-form';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it('does not submit a whitespace-only name', async () => {
  const onOk = jest.fn();
  const { container } = render(
    <RenameForm initialName="original" onOk={onOk} hideModal={jest.fn()} />,
  );
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '   ' } });
  fireEvent.submit(container.querySelector('form')!);
  await screen.findByText('common.namePlaceholder');
  expect(onOk).not.toHaveBeenCalled();
});

it('submits the trimmed name', async () => {
  const onOk = jest.fn().mockResolvedValue(0);
  const { container } = render(
    <RenameForm initialName="original" onOk={onOk} hideModal={jest.fn()} />,
  );
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: ' Renamed ' },
  });
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() => expect(onOk).toHaveBeenCalledWith('Renamed'));
});
