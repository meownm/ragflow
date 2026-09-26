import { fireEvent, render, screen } from '@testing-library/react';

import { SelectWithSearch } from '@/components/originui/select-with-search';
import { MultiSelect } from './multi-select';

beforeAll(() => {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView = jest.fn();
});

describe('selection controls inside forms', () => {
  it('does not submit when opening the chunk method selector', () => {
    const onSubmit = jest.fn((event: React.FormEvent) =>
      event.preventDefault(),
    );
    render(
      <form onSubmit={onSubmit}>
        <SelectWithSearch
          testId="chunk-method-select"
          options={[{ label: 'General', value: 'naive' }]}
        />
      </form>,
    );

    fireEvent.click(screen.getByTestId('chunk-method-select'));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('does not submit when opening the dataset selector', () => {
    const onSubmit = jest.fn((event: React.FormEvent) =>
      event.preventDefault(),
    );
    render(
      <form onSubmit={onSubmit}>
        <MultiSelect
          data-testid="dataset-select"
          options={[{ label: 'Example', value: 'dataset-1' }]}
          onValueChange={jest.fn()}
        />
      </form>,
    );

    fireEvent.click(screen.getByTestId('dataset-select'));

    expect(onSubmit).not.toHaveBeenCalled();
  });
});
