import { importTemplate } from './template-codec';
import sqlQueryTemplate from './templates/sql-query-step-by-step.v1.json';

export function createSqlQueryTemplate() {
  return importTemplate(sqlQueryTemplate);
}
