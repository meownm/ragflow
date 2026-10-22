import {
  getNavigationSectionForPath,
  NavigationSectionPaths,
} from './navigation';

it.each(Object.entries(NavigationSectionPaths))(
  'protects the %s entrypoint',
  (section, path) => {
    expect(getNavigationSectionForPath(path)).toBe(section);
    if (path !== '/')
      expect(getNavigationSectionForPath(`${path}/id`)).toBe(section);
  },
);

it.each([
  ['/technical-specifications/document', 'technical_specifications'],
  ['/business-documents/constructor', 'document_constructor'],
  ['/sql-sources', 'sql_projects'],
  ['/source-workspaces/workspace', 'search'],
  ['/dataset//files/id', 'dataset'],
  ['/chunk/result/id', 'dataset'],
  ['/dataflow-result', 'dataset'],
  ['/chat/id', 'chat'],
  ['/search/id', 'search'],
  ['/agent-templates', 'agent'],
  ['/agent/id/explore', 'agent'],
  ['/agent-log-page/id', 'agent'],
  ['/agent-list', 'agent'],
  ['/memory//memory-setting/id', 'memory'],
  ['/files/skills', 'file_manager'],
])('protects nested and alternative route %s', (path, section) => {
  expect(getNavigationSectionForPath(path)).toBe(section);
});

it.each(['/admin', '/login', '/profile', '/agents-other', '/datasets-other'])(
  'does not classify unrelated path %s',
  (path) => {
    expect(getNavigationSectionForPath(path)).toBeUndefined();
  },
);
