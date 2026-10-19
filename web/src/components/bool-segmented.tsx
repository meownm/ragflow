import { omit } from 'lodash';
import { useTranslation } from 'react-i18next';
import { Segmented, SegmentedProps } from './ui/segmented';

export function BoolSegmented({ ...props }: Omit<SegmentedProps, 'options'>) {
  const { t } = useTranslation();
  return (
    <Segmented
      options={
        [
          { value: true, label: t('common.booleanTrue') },
          { value: false, label: t('common.booleanFalse') },
        ] as any
      }
      sizeType="sm"
      itemClassName="justify-center flex-1"
      {...omit(props, 'options')}
    ></Segmented>
  );
}
