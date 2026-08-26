import { FilterButton } from '@/components/list-filter-bar';
import {
  CheckboxFormMultipleProps,
  FilterPopover,
} from '@/components/list-filter-bar/filter-popover';
import { SearchInput } from '@/components/ui/input';
import { Segmented } from '@/components/ui/segmented';
import { ChangeEventHandler, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { LogTabs } from './dataset-common';

const countFilterValues = (value: Record<string, unknown>): number =>
  Object.values(value).reduce<number>(
    (total, item) =>
      total +
      (Array.isArray(item)
        ? item.length
        : item && typeof item === 'object'
          ? countFilterValues(item as Record<string, unknown>)
          : 0),
    0,
  );

interface IProps {
  searchString?: string;
  onSearchChange?: ChangeEventHandler<HTMLInputElement>;
  active?: (typeof LogTabs)[keyof typeof LogTabs];
  setActive?: (active: (typeof LogTabs)[keyof typeof LogTabs]) => void;
}
const DatasetFilter = (
  props: IProps & Omit<CheckboxFormMultipleProps, 'setOpen'>,
) => {
  const {
    searchString,
    onSearchChange,
    value,
    onChange,
    filters,
    onOpenChange,
    active = LogTabs.FILE_LOGS,
    setActive,
  } = props;
  const { t } = useTranslation();
  const filterCount = useMemo(() => {
    return typeof value === 'object' && value !== null
      ? countFilterValues(value)
      : 0;
  }, [value]);
  return (
    <div className="flex items-center justify-between mb-4">
      <div>
        <Segmented
          value={active}
          options={[
            { value: LogTabs.FILE_LOGS, label: t('knowledgeDetails.fileLogs') },
            {
              value: LogTabs.DATASET_LOGS,
              label: t('knowledgeDetails.datasetLogs'),
            },
          ]}
          onChange={(value) =>
            setActive?.(value as (typeof LogTabs)[keyof typeof LogTabs])
          }
        />
      </div>

      <div className="flex items-center gap-4">
        <FilterPopover
          value={value}
          onChange={onChange}
          filters={filters}
          onOpenChange={onOpenChange}
        >
          <FilterButton count={filterCount}></FilterButton>
        </FilterPopover>

        <SearchInput
          value={searchString}
          onChange={onSearchChange}
          className="w-32"
        ></SearchInput>
      </div>
    </div>
  );
};

export { DatasetFilter };
