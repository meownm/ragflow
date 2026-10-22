import {
  isNavigationSection,
  type NavigationSection,
} from '@/constants/navigation';
import userService from '@/services/user-service';
import { useQuery } from '@tanstack/react-query';

export type SystemConfig = {
  registerEnabled: number;
  disablePasswordLogin?: boolean;
  visibleSections: NavigationSection[];
  accessibleSections: NavigationSection[];
};

export const SystemConfigKeys = {
  all: ['systemConfig'] as const,
};

/**
 * Hook to fetch system configuration including register enable status
 * @returns System configuration with loading status
 */
export const useSystemConfig = () => {
  const { data, isLoading, isError } = useQuery({
    queryKey: SystemConfigKeys.all,
    queryFn: async () => {
      const { data = {} } = await userService.getSystemConfig();
      if (data.code !== 0 || !Array.isArray(data.data?.visibleSections)) {
        throw new Error('Section access policy is unavailable');
      }
      const config = data.data;
      const visibleSections =
        config.visibleSections.filter(isNavigationSection);

      return {
        registerEnabled: config.registerEnabled ?? 1,
        disablePasswordLogin: config.disablePasswordLogin,
        visibleSections,
        accessibleSections: Array.isArray(config.accessibleSections)
          ? config.accessibleSections.filter(isNavigationSection)
          : visibleSections,
      } satisfies SystemConfig;
    },
  });

  return { config: isError ? undefined : data, loading: isLoading };
};
