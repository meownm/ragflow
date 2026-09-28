export const NavigationSections = [
  'home',
  'dataset',
  'chat',
  'search',
  'agent',
  'memory',
  'catalog',
  'business_documents',
  'document_constructor',
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
  document_constructor: '/document-constructor',
  file_manager: '/files',
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
