import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';

import ServiceWorkspace from './index';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('ServiceWorkspace', () => {
  it.each([
    ['/transcription', '/asr/', 'header.transcription'],
    ['/translator', '/translate/', 'header.translator'],
  ])('embeds %s under the RAGFlow layout', (path, url, title) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={path} element={<ServiceWorkspace />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTitle(title)).toHaveAttribute('src', url);
    expect(
      screen.getByRole('link', { name: 'header.openStandalone' }),
    ).toHaveAttribute('href', url);
  });
});
