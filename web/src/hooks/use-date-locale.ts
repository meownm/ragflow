import { enUS } from 'date-fns/locale/en-US';
import { ru } from 'date-fns/locale/ru';
import { labelDayButton, type Labels } from 'react-day-picker';
import { useTranslation } from 'react-i18next';

export function useDateLocale() {
  const { i18n } = useTranslation();
  return (i18n?.resolvedLanguage || i18n?.language)?.startsWith('ru')
    ? ru
    : enUS;
}

export function useCalendarLabels(): Partial<Labels> {
  const { t } = useTranslation();
  return {
    labelNav: () => t('common.calendarNavigation'),
    labelPrevious: () => t('common.previousMonth'),
    labelNext: () => t('common.nextMonth'),
    labelMonthDropdown: () => t('common.month'),
    labelYearDropdown: () => t('common.year'),
    labelWeekNumber: (week) => t('common.weekNumber', { week }),
    labelWeekNumberHeader: () => t('common.weekNumberHeader'),
    labelDayButton: (date, modifiers, options, dateLib) => {
      let label = labelDayButton(date, {}, options, dateLib);
      if (modifiers.today) label = t('common.todayDate', { date: label });
      if (modifiers.selected) label = t('common.selectedDate', { date: label });
      return label;
    },
  };
}
