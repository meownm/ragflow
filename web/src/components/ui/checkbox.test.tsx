import { fireEvent, render, screen } from '@testing-library/react';
import { Checkbox } from './checkbox';

test('uncontrolled checkbox keeps a visible indicator when checked', () => {
  render(<Checkbox aria-label="Выбрать строку" defaultChecked />);
  const checkbox = screen.getByRole('checkbox', { name: 'Выбрать строку' });
  expect(checkbox).toBeChecked();
  expect(checkbox.querySelector('svg')).not.toBeNull();
  fireEvent.click(checkbox);
  expect(checkbox).not.toBeChecked();
  expect(checkbox.querySelector('svg')).toBeNull();
  fireEvent.click(checkbox);
  expect(checkbox).toBeChecked();
  expect(checkbox.querySelector('svg')).not.toBeNull();
});
