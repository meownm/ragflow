import { fireEvent, render, screen } from '@testing-library/react';
import { DocumentMarkdown } from './document-markdown';
import { QuestionItem } from './question-item';
type BusinessDocumentQuestion = import('../types').BusinessDocumentQuestion;

jest.mock('./voice-input', () => ({ VoiceInput: () => null }));

test('renders Markdown blocks, GFM tables, links and inline formatting', () => {
  const { container } = render(
    <DocumentMarkdown>
      {
        '### Заголовок\n\n**Жирный** и *курсив*, ***оба***, ~~удалено~~, `код`.\nНовая строка\n\n- Пункт\n\n3. Шаг\n\n> Цитата\n\n| Поле | Значение |\n| --- | --- |\n| Имя | Тест |\n\n[Источник](https://example.test)\n\n```json\n{"ok":true}\n```'
      }
    </DocumentMarkdown>,
  );
  expect(screen.getByRole('heading', { name: 'Заголовок' })).toBeVisible();
  expect(container.querySelector('strong')).toHaveTextContent('Жирный');
  expect(container.querySelector('em')).toHaveTextContent('курсив');
  expect(container.querySelector('em strong, strong em')).toHaveTextContent(
    'оба',
  );
  expect(container.querySelector('del')).toHaveTextContent('удалено');
  expect(container.querySelector('br')).toBeInTheDocument();
  expect(container.querySelector('ul')).toHaveTextContent('Пункт');
  expect(container.querySelector('ol')).toHaveAttribute('start', '3');
  expect(container.querySelector('blockquote')).toHaveTextContent('Цитата');
  expect(screen.getByRole('table')).toHaveTextContent('Имя');
  expect(screen.getByRole('link', { name: 'Источник' })).toHaveAttribute(
    'href',
    'https://example.test',
  );
  expect(container.querySelector('pre code')).toHaveTextContent('{"ok":true}');
});

test('removes unsafe raw HTML and executable Markdown URLs', () => {
  const { container } = render(
    <DocumentMarkdown>
      {
        '<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>\n\n[Опасная ссылка](javascript:alert%281%29)'
      }
    </DocumentMarkdown>,
  );
  expect(container.querySelector('script, img, [onerror]')).toBeNull();
  expect(container.querySelector('a')).not.toHaveAttribute(
    'href',
    expect.stringMatching(/^javascript:/),
  );
});

const question: BusinessDocumentQuestion = {
  question_id: 'q-md',
  text: '**Выберите** вариант',
  status: 'OPEN',
  options: [
    {
      option_id: 'first',
      label: '**Первый** [источник](https://example.test)',
      description: '*Описание*',
    },
  ],
  allow_custom_answer: true,
};

test.each([false, true])(
  'keeps formatted answer selection and permission checks (disabled=%s)',
  (disabled) => {
    const onAnswer = jest.fn();
    const { container } = render(
      <QuestionItem
        question={question}
        canAnswer={!disabled}
        pending={false}
        expanded
        triggerRef={null}
        onExpandedChange={jest.fn()}
        onAnswer={onAnswer}
      />,
    );
    expect(container.querySelector('strong')).toHaveTextContent('Выберите');
    expect(container.querySelector('em')).toHaveTextContent('Описание');
    const radio = screen.getByRole('radio', { name: 'Первый источник' });
    fireEvent.click(screen.getByText('Первый'));
    expect(radio).toHaveAttribute('aria-checked', String(!disabled));
    const submit = screen.getByRole('button', { name: 'Ответить' });
    if (disabled) {
      expect(submit).toBeDisabled();
      expect(onAnswer).not.toHaveBeenCalled();
    } else {
      fireEvent.click(submit);
      expect(onAnswer).toHaveBeenCalledWith({
        question_id: 'q-md',
        selected_option_id: 'first',
        custom_answer: null,
      });
    }
  },
);

test('opening an option link does not select an answer', () => {
  render(
    <QuestionItem
      question={question}
      canAnswer
      pending={false}
      expanded
      triggerRef={null}
      onExpandedChange={jest.fn()}
      onAnswer={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('link', { name: 'источник' }));
  expect(screen.getByRole('radio')).toHaveAttribute('aria-checked', 'false');
});
