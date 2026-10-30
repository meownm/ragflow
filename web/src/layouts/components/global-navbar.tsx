import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';

import {
  Bot,
  FilePenLine,
  LayoutTemplate,
  LucideAudioLines,
  LucideBrain,
  LucideCpu,
  LucideDatabase,
  LucideFolderOpen,
  LucideHouse,
  LucideLanguages,
  LucideMenu,
  LucideMessageSquareText,
  LucideNetwork,
  LucideSearch,
  X,
  type LucideIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import type { NavigationSection } from '@/constants/navigation';
import { useSystemConfig } from '@/hooks/use-system-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { getBusinessDocumentCapabilities } from '@/services/business-document-service';
import { supportsCssAnchor } from '@/utils/css-support';
import { useQuery } from '@tanstack/react-query';

const PathMap = {
  [Routes.Datasets]: [Routes.Datasets, Routes.DatasetBase],
  [Routes.Chats]: [Routes.Chats, Routes.Chat],
  [Routes.Searches]: [Routes.Searches, Routes.Search],
  [Routes.SourceWorkspaces]: [Routes.SourceWorkspaces],
  [Routes.Agents]: [Routes.Agents, Routes.AgentTemplates],
  [Routes.Memories]: [Routes.Memories, Routes.Memory, Routes.MemoryMessage],
  [Routes.OpenMetadata]: [Routes.OpenMetadata],
  [Routes.BusinessDocuments]: [Routes.BusinessDocuments],
  [Routes.TechnicalSpecifications]: [Routes.TechnicalSpecifications],
  [Routes.DocumentConstructor]: [Routes.DocumentConstructor],
  [Routes.SqlProjects]: [Routes.SqlProjects],
  [Routes.SqlAssistant]: [Routes.SqlAssistant],
  [Routes.Transcription]: [Routes.Transcription],
  [Routes.Translator]: [Routes.Translator],
  [Routes.Files]: [Routes.Files],
} as const;

// Match on path-segment boundaries, not a loose substring, so e.g.
// "/user-setting/chat-channel" does not match the "/chat" tab.
const matchesPath = (pathname: string, candidate: string) =>
  pathname === candidate || pathname.startsWith(`${candidate}/`);

const menuItems: Array<{
  path: Routes;
  name: string;
  icon: LucideIcon;
  group: 'home' | 'documents' | 'data' | 'tools';
  fallbackName?: string;
  section?: NavigationSection;
  requiresBusinessDocumentCreate?: boolean;
  'data-testid'?: string;
}> = [
  {
    path: Routes.Root,
    name: 'header.home',
    group: 'home',
    icon: LucideHouse,
    section: 'home',
    'data-testid': 'nav-home',
  },
  {
    path: Routes.Datasets,
    name: 'header.dataset',
    group: 'data',
    icon: LucideDatabase,
    section: 'dataset',
  },
  {
    path: Routes.Chats,
    name: 'header.chat',
    group: 'tools',
    icon: LucideMessageSquareText,
    section: 'chat',
    'data-testid': 'nav-chat',
  },
  {
    path: Routes.Searches,
    name: 'header.search',
    group: 'tools',
    icon: LucideSearch,
    section: 'search',
    'data-testid': 'nav-search',
  },
  {
    path: Routes.SourceWorkspaces,
    name: 'header.sourceWorkspaces',
    group: 'documents',
    fallbackName: 'Связанные документы',
    icon: LucideFolderOpen,
    section: 'search',
    'data-testid': 'nav-source-workspaces',
  },
  {
    path: Routes.Agents,
    name: 'header.flow',
    group: 'tools',
    icon: LucideCpu,
    section: 'agent',
    'data-testid': 'nav-agent',
  },
  {
    path: Routes.Memories,
    name: 'header.memories',
    group: 'tools',
    icon: LucideBrain,
    section: 'memory',
  },
  {
    path: Routes.OpenMetadata,
    name: 'header.openMetadata',
    group: 'data',
    icon: LucideNetwork,
    section: 'catalog',
    'data-testid': 'nav-openmetadata',
  },
  {
    path: Routes.BusinessDocuments,
    name: 'header.businessDocuments',
    group: 'documents',
    fallbackName: 'Business docs',
    icon: FilePenLine,
    section: 'business_documents',
    'data-testid': 'nav-business-documents',
  },
  {
    path: Routes.TechnicalSpecifications,
    name: 'header.technicalSpecifications',
    group: 'documents',
    fallbackName: 'Технические задания',
    icon: FilePenLine,
    section: 'technical_specifications',
    'data-testid': 'nav-technical-specifications',
  },
  {
    path: Routes.DocumentConstructor,
    name: 'header.documentConstructor',
    group: 'documents',
    fallbackName: 'Constructor',
    icon: LayoutTemplate,
    section: 'document_constructor',
    requiresBusinessDocumentCreate: true,
    'data-testid': 'nav-document-constructor',
  },
  {
    path: Routes.SqlProjects,
    name: 'header.sqlProjects',
    group: 'data',
    fallbackName: 'SQL-проекты',
    icon: Bot,
    section: 'sql_projects',
    requiresBusinessDocumentCreate: true,
    'data-testid': 'nav-sql-projects',
  },
  {
    path: Routes.SqlAssistant,
    name: 'header.sqlAssistant',
    group: 'data',
    fallbackName: 'SQL-помощник',
    icon: Bot,
    section: 'sql_assistant',
    'data-testid': 'nav-sql-assistant',
  },
  {
    path: Routes.Files,
    name: 'header.fileManager',
    group: 'data',
    icon: LucideFolderOpen,
    section: 'file_manager',
  },
  {
    path: Routes.Transcription,
    name: 'header.transcription',
    group: 'tools',
    fallbackName: 'Расшифровка',
    icon: LucideAudioLines,
    section: 'asr',
    'data-testid': 'nav-transcription',
  },
  {
    path: Routes.Translator,
    name: 'header.translator',
    group: 'tools',
    fallbackName: 'Переводчик',
    icon: LucideLanguages,
    section: 'translator',
    'data-testid': 'nav-translator',
  },
];

function useVisibleMenuItems() {
  const { config } = useSystemConfig();
  const { data: userInfo, loading: userInfoLoading } = useFetchUserInfo();
  const actorId = userInfo.id?.trim();
  const businessDocumentsVisible =
    config?.visibleSections.some(
      (section: NavigationSection) =>
        section === 'business_documents' ||
        section === 'document_constructor' ||
        section === 'sql_projects',
    ) === true;
  const { data: businessDocumentAccess } = useQuery({
    queryKey: ['business-document-capabilities', actorId],
    queryFn: getBusinessDocumentCapabilities,
    enabled: businessDocumentsVisible && Boolean(actorId) && !userInfoLoading,
    retry: false,
  });
  const canCreateBusinessDocuments =
    businessDocumentAccess?.capabilities?.create === true;

  return useMemo(() => {
    const visibleSections = new Set(config?.visibleSections);
    return menuItems.filter(
      ({ section, requiresBusinessDocumentCreate }) =>
        (!section ||
          (config
            ? visibleSections.has(section)
            : section !== 'sql_assistant')) &&
        (!requiresBusinessDocumentCreate || canCreateBusinessDocuments),
    );
  }, [canCreateBusinessDocuments, config]);
}

function useActivePath() {
  const { pathname } = useLocation();

  return useMemo(() => {
    return (
      Object.keys(PathMap).find((x: string) =>
        PathMap[x as keyof typeof PathMap].some((y: string) =>
          matchesPath(pathname, y),
        ),
      ) || pathname
    );
  }, [pathname]);
}

const DesktopNavbarWithAnchor = () => {
  const { t } = useTranslation();
  const activePath = useActivePath();
  const visibleMenuItems = useVisibleMenuItems();
  const navbarAnchorNamePrefix = useId().replace(/:/g, '');

  const activePathAnchorName = `--${navbarAnchorNamePrefix}${activePath === Routes.Root ? '-root' : activePath.replace('/', '-')}`;

  const hasAnyActive = useMemo(
    () => visibleMenuItems.some(({ path }) => path === activePath),
    [activePath, visibleMenuItems],
  );

  return (
    <nav>
      <ul className="relative flex items-center p-1 bg-bg-card rounded-full border border-border-button">
        {visibleMenuItems.map((item) => {
          const { path, name, fallbackName, icon: Icon } = item;
          const isActive = path === activePath;
          const anchorName = `--${navbarAnchorNamePrefix}${path === Routes.Root ? '-root' : path.replace('/', '-')}`;

          return (
            <li key={path} className="relative" style={{ anchorName }}>
              <Link
                data-testid={item['data-testid']}
                to={path}
                className={cn(
                  'h-10 px-4 xl:px-6 text-sm xl:text-base inline-flex items-center justify-center whitespace-nowrap',
                  'hover:text-current focus-visible:text-current rounded-full transition-all',
                  isActive && '!text-bg-base',
                )}
                aria-current={isActive ? 'page' : undefined}
              >
                {path === Routes.Root ? (
                  <>
                    <Icon className="size-6 stroke-[1.5]" />
                    <span className="sr-only">
                      {t(name, { defaultValue: fallbackName ?? name })}
                    </span>
                  </>
                ) : (
                  <span>{t(name, { defaultValue: fallbackName ?? name })}</span>
                )}
              </Link>
            </li>
          );
        })}

        <li
          className={cn(
            'absolute -z-[1] bg-text-primary border-b-2 border-b-accent-primary rounded-full opacity-0',
            'transition-all',
            hasAnyActive && 'opacity-100',
          )}
          role="presentation"
          style={{
            top: 'anchor(top)',
            left: 'anchor(left)',
            width: 'anchor-size(width)',
            height: 'anchor-size(height)',
            positionAnchor: activePathAnchorName,
          }}
        />
      </ul>
    </nav>
  );
};

const DesktopNavbarFallback = () => {
  const { t } = useTranslation();
  const activePath = useActivePath();
  const visibleMenuItems = useVisibleMenuItems();

  return (
    <nav>
      <ul className="flex items-center p-1 bg-bg-card rounded-full border border-border-button">
        {visibleMenuItems.map((item) => {
          const { path, name, fallbackName, icon: Icon } = item;
          const isActive = path === activePath;

          return (
            <li key={path}>
              <Link
                data-testid={item['data-testid']}
                to={path}
                className={cn(
                  'h-10 px-4 xl:px-6 text-sm xl:text-base inline-flex items-center justify-center whitespace-nowrap',
                  'hover:text-current focus-visible:text-current rounded-full transition-all',
                  isActive &&
                    '!text-bg-base bg-text-primary border-b-2 border-b-accent-primary',
                )}
                aria-label={t(name, { defaultValue: fallbackName ?? name })}
                aria-current={isActive ? 'page' : undefined}
              >
                {path === Routes.Root ? (
                  <Icon className="size-6 stroke-[1.5]" />
                ) : (
                  <span>{t(name, { defaultValue: fallbackName ?? name })}</span>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};

export function DesktopNavbar() {
  return supportsCssAnchor ? (
    <DesktopNavbarWithAnchor />
  ) : (
    <DesktopNavbarFallback />
  );
}

function MobileNavItem({
  label,
  icon: Icon,
  isActive,
  onClick,
  ...linkProps
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  isActive?: boolean;
  onClick?: () => void;
  to: string;
  'data-testid'?: string;
}) {
  return (
    <Link
      {...linkProps}
      onClick={onClick}
      className={cn(
        'flex min-h-11 w-full items-center gap-3 rounded-lg px-3 py-3 text-sm',
        'text-text-secondary transition-colors hover:bg-bg-card hover:text-text-primary',
        'focus-visible:bg-bg-card focus-visible:text-text-primary',
        isActive &&
          'border-l-2 border-text-primary bg-bg-card font-medium text-text-primary',
      )}
      aria-current={isActive ? 'page' : undefined}
    >
      <Icon className="size-5 shrink-0 stroke-[1.5]" />
      <span className="min-w-0 break-words">{label}</span>
    </Link>
  );
}

type MobileNavbarProps = {
  renderFooter?: (close: () => void) => React.ReactNode;
};

export function MobileNavbar({ renderFooter }: MobileNavbarProps) {
  const { t } = useTranslation();
  const activePath = useActivePath();
  const visibleMenuItems = useVisibleMenuItems();
  const [open, setOpen] = useState(false);

  const close = () => setOpen(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-10 shrink-0"
          aria-label={t('common.menu')}
        >
          <LucideMenu className="size-6 stroke-[1.75]" />
        </Button>
      </SheetTrigger>

      <SheetContent
        side="left"
        closeIcon={false}
        aria-describedby={undefined}
        className="flex w-[min(92vw,20rem)] flex-col gap-0 p-0 sm:w-80"
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-border-button p-4">
          <img
            src="/app-icon.png"
            alt="Логотип Агент Раггер"
            className="size-9"
          />
          <SheetTitle className="flex-1 text-base text-text-primary">
            {t('common.menu')}
          </SheetTitle>
          <SheetClose asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-11"
              aria-label={t('common.close')}
            >
              <X className="size-5" />
            </Button>
          </SheetClose>
        </div>

        <nav
          aria-label={t('common.menu')}
          className="min-h-0 flex-1 overflow-y-auto px-2 py-3"
        >
          {(['home', 'documents', 'data', 'tools'] as const).map((group) => {
            const items = visibleMenuItems.filter(
              (item) => item.group === group,
            );
            if (!items.length) return null;
            return (
              <div key={group} className="mb-4 last:mb-0">
                {group !== 'home' && (
                  <h2 className="px-3 pb-2 pt-1 text-xs font-medium text-text-secondary">
                    {t(`header.navigationGroups.${group}`, {
                      defaultValue: group,
                    })}
                  </h2>
                )}
                <ul className="space-y-1">
                  {items.map((item) => (
                    <li key={item.path}>
                      <MobileNavItem
                        data-testid={item['data-testid']}
                        to={item.path}
                        label={t(item.name, {
                          defaultValue: item.fallbackName ?? item.name,
                        })}
                        icon={item.icon}
                        isActive={item.path === activePath}
                        onClick={close}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>

        {renderFooter?.(close)}
      </SheetContent>
    </Sheet>
  );
}

const GlobalNavbar = DesktopNavbar;

export default GlobalNavbar;
