export const NavigationSections = [
  'home',
  'dataset',
  'chat',
  'search',
  'agent',
  'memory',
  'catalog',
  'business_documents',
  'technical_specifications',
  'document_constructor',
  'sql_projects',
  'sql_assistant',
  'asr',
  'translator',
  'file_manager',
] as const;

export type NavigationSection = (typeof NavigationSections)[number];

export const NavigationSectionPaths: Record<NavigationSection, string> = {
  home: '/',
  dataset: '/datasets',
  chat: '/chats',
  search: '/searches',
  agent: '/agents',
  memory: '/memories',
  catalog: '/openmetadata',
  business_documents: '/business-documents',
  technical_specifications: '/technical-specifications',
  document_constructor: '/document-constructor',
  sql_projects: '/sql-projects',
  sql_assistant: '/sql-assistant',
  asr: '/transcription',
  translator: '/translator',
  file_manager: '/files',
};

// Match path segments so that e.g. /agents-other is not treated as /agents.
export const getNavigationSectionForPath = (
  pathname: string,
): NavigationSection | undefined => {
  const path = pathname.replace(/\/+$/, '') || '/';
  if (path === '/') return 'home';
  const aliases: [string, NavigationSection][] = [
    ['/business-documents/constructor', 'document_constructor'],
    ['/sql-sources', 'sql_projects'],
    ['/source-workspaces', 'search'],
    ['/dataset', 'dataset'],
    ['/chunk', 'dataset'],
    ['/dataflow-result', 'dataset'],
    ['/chat', 'chat'],
    ['/search', 'search'],
    ['/agent', 'agent'],
    ['/agent-list', 'agent'],
    ['/agent-templates', 'agent'],
    ['/agent-log-page', 'agent'],
    ['/memory', 'memory'],
  ];
  for (const section of NavigationSections) {
    if (section !== 'home') {
      aliases.push([NavigationSectionPaths[section], section]);
    }
  }
  return aliases.find(
    ([prefix]) => path === prefix || path.startsWith(`${prefix}/`),
  )?.[1];
};

export const isNavigationSection = (
  value: unknown,
): value is NavigationSection =>
  typeof value === 'string' &&
  NavigationSections.includes(value as NavigationSection);

export const getFirstVisibleNavigationPath = (
  visibleSections: readonly NavigationSection[],
) => {
  const visible = new Set(visibleSections);
  const firstVisibleSection = NavigationSections.find((section) =>
    visible.has(section),
  );

  return firstVisibleSection
    ? NavigationSectionPaths[firstVisibleSection]
    : undefined;
};
