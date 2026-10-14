import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router';

import { Routes } from '@/routes';

const Workspaces = {
  [Routes.Transcription]: {
    title: 'header.transcription',
    url: '/asr/',
  },
  [Routes.Translator]: {
    title: 'header.translator',
    url: '/translate/',
  },
} as const;

export default function ServiceWorkspace() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const workspace =
    Workspaces[pathname as keyof typeof Workspaces] ??
    Workspaces[Routes.Transcription];
  const title = t(workspace.title);

  return (
    <section
      className="flex size-full min-h-0 min-w-0 flex-col"
      aria-label={title}
    >
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-border-button px-5 py-2">
        <h1 className="text-base font-semibold">{title}</h1>
        <a
          className="text-sm text-text-secondary underline hover:text-text-primary"
          href={workspace.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t('header.openStandalone')}
        </a>
      </div>
      <iframe
        key={workspace.url}
        title={title}
        src={workspace.url}
        className="min-h-0 w-full flex-1 border-0 bg-white"
      />
    </section>
  );
}
