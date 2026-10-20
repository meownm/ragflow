import { useContext, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, Outlet, useNavigate } from 'react-router';

import { useMutation, useQuery } from '@tanstack/react-query';

import {
  LucideActivity,
  LucideFiles,
  LucideMenu,
  LucideMonitor,
  LucideScrollText,
  LucideServerCrash,
  LucideSquareUserRound,
  LucideUserCog,
  LucideUserStar,
  LucideUsersRound,
  LucideZap,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
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
        icon: <LucideServerCrash className="size-[1em]" />,
      },
      {
        path: Routes.AdminUserManagement,
        name: t('admin.userManagement'),
        icon: <LucideUserCog className="size-[1em]" />,
      },
      {
        path: Routes.AdminSandboxSettings,
        name: t('admin.sandboxSettings'),
        icon: <LucideZap className="size-[1em]" />,
      },
      {
        path: Routes.AdminNavigationVisibility,
        name: t('admin.navigationVisibility'),
        icon: <LucideMenu className="size-[1em]" />,
      },
      {
        path: Routes.AdminAccessGroups,
        name: t('admin.accessGroups'),
        icon: <LucideUsersRound className="size-[1em]" />,
      },
      {
        path: Routes.AdminBusinessDocumentsSettings,
        name: t('admin.businessDocumentsSettings'),
        icon: <LucideFiles className="size-[1em]" />,
      },
      {
        path: Routes.AdminBusinessDocumentsQuality,
        name: t('admin.businessDocumentsQuality'),
        icon: <LucideActivity className="size-[1em]" />,
      },
      {
        path: Routes.AdminAudit,
        name: t('admin.audit'),
        icon: <LucideScrollText className="size-[1em]" />,
      },
      ...(IS_ENTERPRISE
        ? [
            {
              path: Routes.AdminWhitelist,
              name: t('admin.registrationWhitelist'),
              icon: <LucideUserStar className="size-[1em]" />,
            },
            {
              path: Routes.AdminRoles,
              name: t('admin.roles'),
              icon: <LucideSquareUserRound className="size-[1em]" />,
            },
            {
              path: Routes.AdminMonitoring,
              name: t('admin.monitoring'),
              icon: <LucideMonitor className="size-[1em]" />,
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
    <main className="flex h-screen w-screen min-w-0 flex-col gap-3 p-3 md:flex-row md:gap-0 md:px-6 md:pb-6 md:pt-12 dark:*:focus-visible:ring-white">
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
      <aside
        id="admin-navigation"
        className={cn(
          'hidden max-h-[50vh] w-full shrink-0 flex-col gap-6 overflow-y-auto md:mr-6 md:flex md:max-h-none md:w-72 md:overflow-visible',
          mobileNavigationOpen && 'flex',
        )}
      >
        <div className="mb-6 hidden items-center md:flex">
          <img
            className="size-8 mr-5"
            src="/app-icon.png"
            alt={t('common.logo')}
          />
          <span className="text-xl font-bold">{t('admin.title')}</span>
        </div>

        <nav>
          <ul className="space-y-4">
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
                  {it.icon}
                  <span className="ml-3">{it.name}</span>
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

            <ThemeSwitch />
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

      <section className="min-h-0 min-w-0 flex-1">
        <Outlet />
      </section>
    </main>
  );
};

export default AdminNavigationLayout;
