import {
  getFirstVisibleNavigationPath,
  type NavigationSection,
} from '@/constants/navigation';
import { useSystemConfig } from '@/hooks/use-system-request';
import { useTranslation } from 'react-i18next';
import { Link, Navigate } from 'react-router';

export default function SectionAccess({
  section,
  children,
}: React.PropsWithChildren<{ section: NavigationSection }>) {
  const { config, loading } = useSystemConfig();
  const { t } = useTranslation();
  if (loading) return null;
  const allowed = config?.accessibleSections ?? config?.visibleSections ?? [];
  if (allowed.includes(section)) return children;
  const destination = getFirstVisibleNavigationPath(allowed);
  if (section === 'home' && destination) {
    return <Navigate to={destination} replace />;
  }
  return (
    <div role="alert" className="p-8" data-testid="section-access-denied">
      <p>
        {t('common.sectionAccessDenied', {
          defaultValue: 'Доступ к разделу запрещён.',
        })}
      </p>
      {destination && (
        <Link to={destination}>
          {t('common.back', { defaultValue: 'Назад' })}
        </Link>
      )}
    </div>
  );
}
