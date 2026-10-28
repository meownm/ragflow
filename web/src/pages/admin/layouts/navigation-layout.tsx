import { useContext, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, Outlet, useNavigate } from 'react-router';

import { useMutation, useQuery } from '@tanstack/react-query';

import { LucideMenu } from 'lucide-react';

import { Button } from '@/components/ui/button';
import '@/components/workbench/workbench.css';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { getSystemVersion, logout } from '@/services/admin-service';

import authorizationUtil from '@/utils/authorization-util';

import ThemeSwitch from '../../../components/theme-switch';
import { IS_ENTERPRISE } from '../utils';
import { CurrentUserInfoContext } from './root-layout';

const AdminNavigationLayout = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [, setCurrentUserInfo] = useContext(CurrentUserInfoContext);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  const { data: version } = useQuery({
    queryKey: ['admin/version'],
    queryFn: async () => (await getSystemVersion())?.data?.data?.version,
  });

  const navItems = useMemo(
    () => [
      {
        path: Routes.AdminServices,
        name: t('admin.serviceStatus'),
      },
      {
        path: Routes.AdminUserManagement,
        name: t('admin.usersNavigation', { defaultValue: 'Пользователи' }),
      },
      {
        path: Routes.AdminSandboxSettings,
        name: t('admin.sandboxNavigation', { defaultValue: 'Песочница' }),
      },
      {
        path: Routes.AdminNavigationVisibility,
        name: t('admin.navigationVisibility'),
      },
      {
        path: Routes.AdminAccessGroups,
        name: t('admin.accessGroups'),
      },
      {
        path: Routes.AdminBusinessDocumentsSettings,
        name: t('admin.businessDocumentsSettings'),
      },
      {
        path: Routes.AdminBusinessDocumentsQuality,
        name: t('admin.businessDocumentsQuality'),
      },
      {
        path: Routes.AdminAudit,
        name: t('admin.audit'),
      },
      ...(IS_ENTERPRISE
        ? [
            {
              path: Routes.AdminWhitelist,
              name: t('admin.registrationWhitelist'),
            },
            {
              path: Routes.AdminRoles,
              name: t('admin.roles'),
            },
            {
              path: Routes.AdminMonitoring,
              name: t('admin.monitoring'),
            },
          ]
        : []),
    ],
    [t],
  );

  const logoutMutation = useMutation({
    mutationKey: ['adminLogout'],
    mutationFn: async () => {
      await logout();
      authorizationUtil.removeAll();
      navigate(Routes.Admin);
      setCurrentUserInfo({
        userInfo: null,
        source: null,
      });
    },
    retry: false,
  });

  return (
    <main className="rf-workbench rf-admin min-w-0">
      <header className="rf-admin-header">
        <img src="/app-icon.png" alt="Агент Раггер" />
        <span className="font-medium">Агент Раггер</span>
        <span className="text-text-secondary">Панель администратора</span>
        <Button
          variant="ghost"
          className="shrink-0 self-start md:hidden"
          aria-controls="admin-navigation"
          aria-expanded={mobileNavigationOpen}
          onClick={() => setMobileNavigationOpen((open) => !open)}
        >
          <LucideMenu aria-hidden="true" />
          {t('admin.title')}
        </Button>
      </header>
      <aside
        id="admin-navigation"
        className="rf-admin-nav"
        data-open={mobileNavigationOpen}
      >
        <nav>
          <ul className="grid gap-2">
            {navItems.map((it) => (
              <li key={it.path}>
                <NavLink
                  to={it.path}
                  onClick={() => setMobileNavigationOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      'px-4 py-3 rounded-lg',
                      'text-base w-full flex items-center justify-start text-text-secondary',
                      'hover:bg-bg-card focus:bg-bg-card focus-visible:bg-bg-card',
                      'hover:text-text-primary focus:text-text-primary focus-visible:text-text-primary',
                      'active:text-text-primary',
                      'transition-colors',
                      {
                        'bg-bg-card text-text-primary': isActive,
                      },
                    )
                  }
                >
                  <span>{it.name}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-auto space-y-4">
          <div className="flex justify-between items-center">
            <span className="leading-none text-xs text-accent-primary">
              {version}
            </span>

            <ThemeSwitch aria-label="Тёмная тема" />
          </div>

          <Button
            size="lg"
            variant="transparent"
            block
            onClick={() => logoutMutation.mutate()}
          >
            {t('header.logout')}
          </Button>
        </div>
      </aside>

      <section className="rf-admin-content">
        <Outlet />
      </section>
    </main>
  );
};

export default AdminNavigationLayout;
