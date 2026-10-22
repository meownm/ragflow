import {
  checkAlignment,
  getAlignment,
  listAlignmentSources,
} from '@/services/technical-specification-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AlignmentPanel } from './alignment-panel';
type Specification = import('./model').Specification;
type AlignmentView =
  import('@/services/technical-specification-service').AlignmentView;

jest.mock('@/services/technical-specification-service', () => ({
  checkAlignment: jest.fn(),
  getAlignment: jest.fn(),
  listAlignmentSources: jest.fn(),
}));

const document: Specification = {
  id: 'tz',
  workspace_id: 'qa',
  version: 1,
  revision: 1,
  title: 'ТЗ',
  sections: {},
  messages: [],
  comments: [],
  people: {},
  proposals: [],
  eva: null,
};
const empty: AlignmentView = { document, checks: [] };
const props = {
  document,
  disabled: false,
  canEdit: true,
  onDocument: jest.fn(),
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(getAlignment).mockResolvedValue(empty);
  jest.mocked(listAlignmentSources).mockResolvedValue({
    items: [
      { document_id: 'bt', title: 'Согласованные БТ', revision_number: 4 },
    ],
  });
});

test('readers see history without fetching the editable source catalogue', async () => {
  render(<AlignmentPanel {...props} canEdit={false} />);
  await screen.findByText('Проверок ещё нет.');
  expect(listAlignmentSources).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Проверить соответствие' }),
  ).toBeDisabled();
});

test('sources show authorized saved references without fetching or running the checker', async () => {
  jest.mocked(getAlignment).mockResolvedValue({
    document,
    checks: [
      {
        id: 'check',
        current: true,
        qualifies: true,
        references: [
          {
            document_id: 'source / one',
            title: 'Согласованные требования',
            revision: 4,
          },
        ],
      },
    ],
  });
  const onConfigure = jest.fn();
  render(
    <AlignmentPanel {...props} mode="sources" onConfigure={onConfigure} />,
  );
  expect(
    await screen.findByRole('link', { name: 'Согласованные требования' }),
  ).toHaveAttribute('href', '/business-documents/source%20%2F%20one');
  expect(screen.getByText('Согласованная редакция 4')).toBeInTheDocument();
  expect(listAlignmentSources).not.toHaveBeenCalled();
  expect(checkAlignment).not.toHaveBeenCalled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Открыть соответствие БТ' }),
  );
  expect(onConfigure).toHaveBeenCalledTimes(1);
});

test('source summary does not claim confirmation for a different revision', async () => {
  jest.mocked(getAlignment).mockResolvedValue({
    document: { ...document, revision: 2 },
    checks: [{ id: 'check', current: true, qualifies: true }],
  });
  render(<AlignmentPanel {...props} mode="sources" />);
  await screen.findByText('Соответствие не подтверждено');
  expect(
    screen.queryByText('Соответствие подтверждено'),
  ).not.toBeInTheDocument();
  expect(props.onDocument).not.toHaveBeenCalled();
});

test('dirty document cannot start inference', async () => {
  render(<AlignmentPanel {...props} disabled />);
  await screen.findByLabelText('Согласованные БТ · редакция 4');
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Проверить соответствие' }),
  ).toBeDisabled();
  expect(checkAlignment).not.toHaveBeenCalled();
});

test('semantic PASS with incomplete evidence is visibly unconfirmed', async () => {
  jest.mocked(getAlignment).mockResolvedValue({
    document,
    checks: [
      {
        id: 'check',
        current: true,
        qualifies: false,
        result: {
          quality_status: 'PASS',
          evidence_status: 'INCOMPLETE',
          reason_code: 'MODEL_EVIDENCE_INCOMPLETE',
        },
      },
    ],
  });
  render(<AlignmentPanel {...props} />);
  await screen.findByText('Соответствие не подтверждено');
  expect(
    screen.getByText(/Смысловая оценка: PASS · доказательства: INCOMPLETE/),
  ).toBeInTheDocument();
  expect(
    screen.queryByText('Соответствие подтверждено'),
  ).not.toBeInTheDocument();
});

test.each(['dirty', 'history', 'foreign-revision'])(
  'a qualified report cannot confirm the visible document when %s',
  async (state) => {
    jest.mocked(getAlignment).mockResolvedValue({
      document:
        state === 'foreign-revision' ? { ...document, revision: 2 } : document,
      checks: [{ id: 'check', current: state !== 'history', qualifies: true }],
    });
    render(<AlignmentPanel {...props} disabled={state === 'dirty'} />);
    await screen.findByText(/Соответствие не подтверждено/);
    expect(
      screen.queryByText('Соответствие подтверждено'),
    ).not.toBeInTheDocument();
    expect(props.onDocument).not.toHaveBeenCalled();
  },
);

test('recovery advances operational version only for the same content revision', async () => {
  jest
    .mocked(getAlignment)
    .mockResolvedValue({ document: { ...document, version: 3 }, checks: [] });
  render(<AlignmentPanel {...props} />);
  await waitFor(() =>
    expect(props.onDocument).toHaveBeenCalledWith(
      expect.objectContaining({ version: 3, revision: 1 }),
    ),
  );
});

test('transport retry reuses request identity and publishes returned version', async () => {
  jest
    .mocked(checkAlignment)
    .mockRejectedValueOnce(new Error('connection lost'))
    .mockResolvedValueOnce({
      document: { ...document, version: 3 },
      checks: [],
    });
  render(<AlignmentPanel {...props} />);
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить соответствие' }),
  );
  await screen.findByRole('alert');
  const original = jest.mocked(checkAlignment).mock.calls[0][2];
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить соответствие' }),
  );
  await waitFor(() =>
    expect(props.onDocument).toHaveBeenCalledWith(
      expect.objectContaining({ version: 3 }),
    ),
  );
  expect(jest.mocked(checkAlignment).mock.calls[1][2]).toEqual(original);
  expect(original).toMatchObject({ version: 1, business_document_ids: ['bt'] });
});

test('a late history response from an old revision is discarded', async () => {
  let resolve!: (value: AlignmentView) => void;
  jest.mocked(getAlignment).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const mounted = render(<AlignmentPanel {...props} />);
  mounted.rerender(
    <AlignmentPanel {...props} document={{ ...document, revision: 2 }} />,
  );
  await screen.findByText('Проверок ещё нет.');
  resolve({
    document,
    checks: [{ id: 'old', current: true, qualifies: true }],
  });
  await waitFor(() => expect(getAlignment).toHaveBeenCalledTimes(2));
  expect(
    screen.queryByText('Соответствие подтверждено'),
  ).not.toBeInTheDocument();
});
