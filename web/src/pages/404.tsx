import { Button } from '@/components/ui/button';
import { Routes } from '@/routes';
import { useTranslation as useUiTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router';

const NoFoundPage = () => {
  const { t: tUi } = useUiTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <div className="text-6xl font-bold text-text-secondary mb-4">404</div>
      <div className="text-lg text-text-secondary mb-8">
        {tUi('common.pageNotFound')}
      </div>
      <Button
        onClick={() => {
          navigate(
            location.pathname.startsWith(Routes.Admin) ? Routes.Admin : '/',
          );
        }}
      >
        {tUi('common.business')}
      </Button>
    </div>
  );
};

export default NoFoundPage;
