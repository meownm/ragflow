import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { ResizeHandle } from './resize-handle';

function Show({ direction = 'width' }: { direction?: 'width' | 'height' }) {
  const [value, setValue] = useState(300);
  return (
    <ResizeHandle
      direction={direction}
      value={value}
      min={200}
      max={400}
      onChange={setValue}
      onReset={() => setValue(300)}
      label="Размер"
      testId="resize"
    />
  );
}
test('clamps keyboard resizing and resets for either axis', () => {
  render(<Show direction="height" />);
  const handle = screen.getByRole('separator');
  for (let n = 0; n < 8; n++) fireEvent.keyDown(handle, { key: 'ArrowUp' });
  expect(handle).toHaveAttribute('aria-valuenow', '400');
  fireEvent.keyDown(handle, { key: 'Home' });
  expect(handle).toHaveAttribute('aria-valuenow', '300');
});
test('uses pointer deltas and stops on lost capture', () => {
  window.PointerEvent = MouseEvent as typeof PointerEvent;
  render(<Show />);
  const handle = screen.getByRole('separator');
  handle.setPointerCapture = jest.fn();
  handle.releasePointerCapture = jest.fn();
  fireEvent.pointerDown(handle, { clientX: 700, button: 0 });
  fireEvent.pointerMove(handle, { clientX: 650 });
  expect(handle).toHaveAttribute('aria-valuenow', '350');
  fireEvent.lostPointerCapture(handle);
  fireEvent.pointerMove(handle, { clientX: 600 });
  expect(handle).toHaveAttribute('aria-valuenow', '350');
});
