import {
  saveBusinessDocumentSqlManualQuery,
  validateBusinessDocumentSqlManualQuery,
} from '@/services/business-document-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { GuidedSqlForm } from './guided-sql-form';

jest.mock('@/services/business-document-service', () => ({
  saveBusinessDocumentSqlManualQuery: jest.fn(),
  validateBusinessDocumentSqlManualQuery: jest.fn(),
}));

const validate = validateBusinessDocumentSqlManualQuery as jest.Mock;
const save = saveBusinessDocumentSqlManualQuery as jest.Mock;
const baseSql = 'SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT :row_limit';
const joinedSql = 'SELECT o.order_id AS order_id, c.customer_id AS customer_id FROM dwh.order_fact AS o JOIN dwh.customer_dim AS c ON c.customer_id = o.customer_id LIMIT :row_limit';
const checked = {
  schema_version: '1', status: 'READY', snapshot_fingerprint: 'sha256:schema',
  blocking_issues: [], sql: baseSql, parameters: { row_limit: 100 },
  guard: { status: 'PASS', tables: ['dwh.order_fact'], parameters: ['row_limit'] },
};
const project = {
  id: 'guided-project', state_version: 4,
  artifact_ids: { schema: 'schema-1' },
};

describe('GuidedSqlForm', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.clearAllMocks();
    validate.mockResolvedValue(checked);
    save.mockResolvedValue({});
  });

  it('starts from a physical table and column in the accepted schema', () => {
    render(<GuidedSqlForm project={{
      ...project,
      artifacts: { schema: {
        accepted_schema: [{ entity_id: 'orders' }],
        schema_snapshot: { requirements: [{ selected_table: {
          id: 'orders', schema: 'dwh', technical_name: 'order_fact',
          columns: [{ name: 'order_id', selected: true }],
        } }] },
      } },
    } as never} onSaved={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Собрать SQL по шагам' }));
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(
      'SELECT t."order_id" AS "order_id" FROM "dwh"."order_fact" AS t LIMIT :row_limit',
    );
  });

  it('checks each complete query, invalidates later checks on edits, and saves only the verified final step', async () => {
    const onSaved = jest.fn().mockResolvedValue(undefined);
    render(<GuidedSqlForm project={project as never} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Собрать SQL по шагам' }));
    expect(screen.getByRole('button', { name: 'Добавить уточнение' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('SQL шага 1'), { target: { value: baseSql } });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith('guided-project', 4, baseSql, [
      { name: 'row_limit', type: 'integer', value: 100 },
    ]));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Добавить уточнение' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Добавить уточнение' }));
    expect((screen.getByLabelText('SQL шага 2') as HTMLTextAreaElement).value).toBe(baseSql);
    fireEvent.change(screen.getByLabelText('SQL шага 2'), { target: { value: joinedSql } });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith('guided-project', 4, joinedSql, expect.any(Array)));
    await waitFor(() => expect(screen.getByText(/Шаг проверен/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /1\. Базовая выборка/ }));
    fireEvent.change(screen.getByLabelText('SQL шага 1'), { target: { value: `${baseSql} ` } });
    fireEvent.click(screen.getByRole('button', { name: /2\. Соединения/ }));
    expect(screen.queryByText(/Шаг проверен/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить проверенный SQL' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Проверить шаг' })).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('saves a verified final draft after task alignment', async () => {
    const onSaved = jest.fn().mockResolvedValue(undefined);
    render(<GuidedSqlForm project={project as never} initialSql={baseSql} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Редактировать финальный SQL' }));
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() => expect(screen.getByText(/Шаг проверен/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить проверенный SQL' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('guided-project', 4, expect.stringMatching(/^manual-sql-/), baseSql, [
      { name: 'row_limit', type: 'integer', value: 100 },
    ]));
    expect(onSaved).toHaveBeenCalled();
  });

  it('allows editing a saved result while keeping a new revision blocked until result cleanup', async () => {
    const sql = 'SELECT o.order_id AS order_id FROM dwh.order_fact AS o WHERE o.order_id = :wanted LIMIT :row_limit';
    render(<GuidedSqlForm project={{
      ...project,
      artifact_ids: { schema: 'schema-1', query: 'query-1' },
      artifacts: { query: { mode: 'manual', parameters: [
        { name: 'wanted', type: 'text', value: 'A-1' },
        { name: 'row_limit', type: 'integer', value: 100 },
      ] } },
    } as never} initialSql={sql} saveDisabledReason="Сначала удалите временные данные."
    onSaved={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Редактировать финальный SQL' }));
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(sql);
    expect((screen.getByLabelText('Параметры JSON шага 1') as HTMLTextAreaElement).value).toContain('wanted');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), { target: { value: `${sql} ` } });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith('guided-project', 4, `${sql} `, [
      { name: 'wanted', type: 'text', value: 'A-1' },
      { name: 'row_limit', type: 'integer', value: 100 },
    ]));
    expect(screen.getByRole('button', { name: 'Сохранить проверенный SQL' })).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });
});
