import { validateTemplate } from './model';
import { createSqlQueryTemplate } from './sql-query-template';
import { exportTemplate } from './template-codec';
import sqlQueryTemplate from './templates/sql-query-step-by-step.v1.json';

describe('step-by-step SQL query template', () => {
  it('is a canonical constructor project that round-trips without drift', () => {
    const draft = createSqlQueryTemplate();

    expect(validateTemplate(draft)).toEqual([]);
    expect(exportTemplate(draft)).toEqual(sqlQueryTemplate);
    expect(draft.version).toBe('1.2.0');
    expect(draft.sections).toHaveLength(44);
  });

  it('keeps ambiguity gates for entities, fields and joins before SQL', () => {
    const exported = exportTemplate(createSqlQueryTemplate());
    const section = (id: string) =>
      exported.sections.find((candidate) => candidate.id === id);

    expect(section('1.4.1')?.generator_instructions).toContain(
      'не выбирайте автоматически',
    );
    expect(section('2.1.1')?.requirements).toContain(
      'При нескольких полях-кандидатах показать варианты и запросить подтверждение',
    );
    expect(section('2.4.2')?.generator_instructions).toContain(
      'итоговый SQL формировать нельзя',
    );
    expect(section('5.1')?.requirements).toContain(
      'READY допустим только при отсутствии блокирующих пунктов',
    );
    expect(
      exported.sections.findIndex((candidate) => candidate.id === '5.1'),
    ).toBeLessThan(
      exported.sections.findIndex((candidate) => candidate.id === '5.5'),
    );
  });

  it('defines one auditable card for every WHERE condition', () => {
    const whereCard = exportTemplate(createSqlQueryTemplate()).sections.find(
      (section) => section.id === '3.2.1',
    );

    expect(whereCard?.requirements).toEqual(
      expect.arrayContaining([
        'Привести описание на естественном языке',
        'Привести SQL-выражение с квалифицированными именами полей',
        'Указать параметры и их типы вместо подстановки пользовательских значений в SQL',
        'Указать источник поля и правила из базы знаний',
      ]),
    );
  });

  it('separates ID dictionary enrichment from a later additional-data cycle', () => {
    const section = exportTemplate(createSqlQueryTemplate()).sections.find(
      (candidate) => candidate.id === '2.4.3',
    );

    expect(section?.title).toBe(
      'Расшифровка идентификаторов по справочникам',
    );
    expect(section?.requirements).toEqual(
      expect.arrayContaining([
        'Указать исходное ID-поле, таблицу-справочник, ключ справочника и возвращаемое поле с названием',
        'Предпочитать один подтверждённый JOIN в основном запросе, когда справочник известен до выполнения',
        'Если потребность обнаружена только после результата, оформить отдельный additional_data_request с уникальными ID, параметрами, лимитом и новым циклом подтверждения',
      ]),
    );
    expect(section?.generator_instructions).toContain('SQLGuard');
  });

  it('keeps optional Python execution in a dedicated document section', () => {
    const exported = exportTemplate(createSqlQueryTemplate());
    const section = (id: string) =>
      exported.sections.find((candidate) => candidate.id === id);

    expect(section('6')?.title).toBe('Постобработка результатов на Python');
    expect(section('6')?.required).toBe(true);
    expect(section('6')?.requirements).toEqual(
      expect.arrayContaining([
        'Всегда включать раздел в итоговый документ; при отсутствии постобработки фиксировать статус skipped и причину',
        'Считать этап выключенным по умолчанию и запускать только после успешного read-only SQL и ResultGate',
      ]),
    );
    expect(section('6.2')?.generator_instructions).toContain(
      'не извлекайте данные из Python',
    );
    expect(section('6.4')?.allowed_blocks).toContain('code');
    expect(section('6.5')?.requirements).toEqual(
      expect.arrayContaining([
        'Выполнять код в отдельном одноразовом процессе или контейнере, а не внутри API или worker-процесса',
        'Запретить сеть, БД, запуск процессов, секреты и запись в постоянную файловую систему',
      ]),
    );
    expect(section('6.6')?.requirements).toEqual(
      expect.arrayContaining([
        'Использовать закрытый статус python_execution: skipped, ok, disabled или error',
        'При skipped или disabled вернуть неизменённый исходный набор без деградации; при error вернуть исходный набор и отметить pipeline как degraded, а не failed',
        'Если план или проверка выявляет нехватку данных, создать additional_data_request и начать отдельный согласуемый SQL-цикл вместо выполнения SQL из Python',
      ]),
    );
    expect(
      exported.sections.findIndex((candidate) => candidate.id === '5.6'),
    ).toBeLessThan(
      exported.sections.findIndex((candidate) => candidate.id === '6'),
    );
  });
});
