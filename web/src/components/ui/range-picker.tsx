'use client';

import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { useDateLocale } from '@/hooks/use-date-locale';
import { format } from 'date-fns';
import { CalendarIcon } from 'lucide-react';
import { PropsRangeRequired } from 'react-day-picker';
import { useTranslation as useUiTranslation } from 'react-i18next';

export function DatePickerWithRange({
  selected,
  ...props
}: Omit<PropsRangeRequired, 'mode'>) {
  const { t: tUi } = useUiTranslation();
  const locale = useDateLocale();
  //   const [date, setDate] = React.useState<DateRange | undefined>({
  //     from: new Date(new Date().getFullYear(), 0, 20),
  //     to: addDays(new Date(new Date().getFullYear(), 0, 20), 20),
  //   });

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          id="date-picker-range"
          className="justify-start px-2.5 font-normal"
        >
          <CalendarIcon />
          {selected?.from ? (
            selected.to ? (
              <>
                {format(selected.from, 'PP', { locale })} -{' '}
                {format(selected.to, 'PP', { locale })}
              </>
            ) : (
              format(selected.from, 'PP', { locale })
            )
          ) : (
            <span>{tUi('common.pickDate')}</span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="range"
          selected={selected}
          //   defaultMonth={date?.from}
          numberOfMonths={2}
          {...props}
        />
      </PopoverContent>
    </Popover>
  );
}
