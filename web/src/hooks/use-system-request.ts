import {
  isNavigationSection,
  NavigationSections,
  type NavigationSection,
} from '@/constants/navigation';
import userService from '@/services/user-service';
import { useQuery } from '@tanstack/react-query';

export type SystemConfig = {
  registerEnabled: number;
  disablePasswordLogin?: boolean;
  visibleSections: NavigationSection[];
};

export const SystemConfigKeys = {
  all: ['systemConfig'] as const,
};

/**
 * Hook to fetch system configuration including register enable status
 * @returns System configuration with loading status
 */
export const useSystemConfig = () => {
  const { data, isLoading } = useQuery({
    queryKey: SystemConfigKeys.all,
    queryFn: async () => {
      const { data = {} } = await userService.getSystemConfig();
      const config = data.data ?? {};
      const visibleSections = Array.isArray(config.visibleSections)
        ? config.visibleSections.filter(isNavigationSection)
        : [...NavigationSections];

      return {
        registerEnabled: config.registerEnabled ?? 1,
        disablePasswordLogin: config.disablePasswordLogin,
        visibleSections,
      } satisfies SystemConfig;
    },
  });

  return { config: data, loading: isLoading };
};
