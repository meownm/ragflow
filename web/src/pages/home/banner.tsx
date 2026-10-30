import { useTranslation } from 'react-i18next';

export function NextBanner() {
  const { t, i18n } = useTranslation();
  return (
    <h1
      className="text-[1.75rem] leading-tight text-left sm:text-3xl"
      dir={i18n.language?.startsWith('ar') ? 'rtl' : 'ltr'}
    >
      <span className="font-semibold text-text-primary">
        {t('header.welcome')}{' '}
      </span>
      <span className="font-bold text-text-primary">Агент Раггер</span>
    </h1>
  );
}
