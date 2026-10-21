import { useCallback } from 'react';
import { useSearchParams } from 'react-router';
import DocumentConstructorPage from '.';

export default function SqlProjectsPage() {
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project');
  const selectProject = useCallback(
    (id: string) => {
      setParams((current) => {
        if (current.get('project') === id) return current;
        const next = new URLSearchParams(current);
        next.set('project', id);
        return next;
      });
    },
    [setParams],
  );
  return (
    <DocumentConstructorPage
      surface="sql"
      sqlProjectId={projectId}
      onSqlProjectSelected={selectProject}
    />
  );
}
