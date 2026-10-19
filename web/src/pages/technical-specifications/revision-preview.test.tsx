import { render, screen } from '@testing-library/react';
import { RevisionPreview } from './revision-preview';
type Revision = import('./model').Revision;
type Specification = import('./model').Specification;

jest.mock('@/pages/business-documents/components/document-markdown', () => ({
  DocumentMarkdown: ({ children }: { children: string }) => <p>{children}</p>,
}));

const sections = Object.fromEntries(
  Array.from({ length: 9 }, (_, index) => [String(index + 1), 'Без изменений']),
);
const previous: Revision = {
  number: 1,
  title: 'ТЗ загрузки',
  sections: { ...sections, '4': 'Не более 20 файлов' },
  author_id: 'author',
  reason: 'create',
  created_at: '2026-10-20T00:00:00Z',
};
const current: Specification = {
  id: 'document',
  workspace_id: 'workspace',
  version: 2,
  messages: [],
  comments: [],
  proposals: [],
  people: {},
  eva: null,
  title: 'Обновлённое ТЗ',
  sections: { ...sections, '4': 'Не более 35 файлов' },
  revision: 2,
};

test('history compares title and text and retains the complete selected revision', () => {
  const { container } = render(
    <RevisionPreview current={current} previous={previous} />,
  );
  expect(screen.getByLabelText('Изменение названия')).toHaveTextContent(
    'ТЗ загрузки',
  );
  expect(
    [...container.querySelectorAll('del')].some(
      (node) => node.textContent === '20',
    ),
  ).toBe(true);
  expect(
    [...container.querySelectorAll('ins')].some(
      (node) => node.textContent === '35',
    ),
  ).toBe(true);
  expect(screen.getByText('Полный текст редакции 1')).toBeInTheDocument();
  expect(container.querySelectorAll('h5')).toHaveLength(9);
});

test('identical revisions show an explicit empty comparison', () => {
  const { container } = render(
    <RevisionPreview
      current={{
        ...current,
        title: previous.title,
        sections: previous.sections!,
      }}
      previous={previous}
    />,
  );
  expect(
    screen.getByText('Содержимое редакций совпадает.'),
  ).toBeInTheDocument();
  expect(container.querySelector('del, ins')).toBeNull();
});
