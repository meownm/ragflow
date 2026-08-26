import { Button } from '@/components/ui/button';
import { SearchInput } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Radio } from '@/components/ui/radio';
import { Segmented } from '@/components/ui/segmented';
import { useTranslate } from '@/hooks/common-hooks';
import { cn } from '@/lib/utils';
import { LucideFilter, Plus } from 'lucide-react';
import { useState } from 'react';
import { ChunkTextMode } from '../../constant';

interface ChunkResultBarProps {
  className?: string;
  changeChunkTextMode: (mode: ChunkTextMode) => void;
  createChunk: (id: string) => unknown;
  isReadonly?: boolean;
  available?: number;
  selectAllChunk?: (value: boolean) => void;
  handleSetAvailable?: (value: number | undefined) => void;
  handleInputChange?: React.ChangeEventHandler<HTMLInputElement>;
  searchString?: string;
}

export default function ChunkResultBar({
  className,
  changeChunkTextMode,
  createChunk,
  isReadonly = false,
  available,
  selectAllChunk,
  handleSetAvailable,
  handleInputChange,
  searchString,
}: ChunkResultBarProps) {
  const { t } = useTranslate('chunk');
  const [textSelectValue, setTextSelectValue] = useState<string | number>(
    ChunkTextMode.Full,
  );

  const handleFilterChange = (value: string | number) => {
    selectAllChunk?.(false);
    handleSetAvailable?.(value === -1 ? undefined : Number(value));
  };

  const textSelectOptions = [
    { label: t(ChunkTextMode.Full), value: ChunkTextMode.Full },
    { label: t(ChunkTextMode.Ellipse), value: ChunkTextMode.Ellipse },
  ];

  const changeTextSelectValue = (value: string | number) => {
    setTextSelectValue(value);
    changeChunkTextMode(value as ChunkTextMode);
  };

  const supportsFiltering = Boolean(selectAllChunk && handleSetAvailable);

  return (
    <div className={cn('flex justify-end gap-4', className)}>
      <Segmented
        className="gap-0 me-auto"
        buttonSize="xs"
        itemClassName="px-2"
        options={textSelectOptions}
        value={textSelectValue}
        onChange={changeTextSelectValue}
      />

      {supportsFiltering && (
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="icon">
              <LucideFilter />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="p-0 w-[200px]">
            <Radio.Group onChange={handleFilterChange} value={available ?? -1}>
              <div className="flex flex-col gap-2 p-4">
                <Radio value={-1}>{t('all')}</Radio>
                <Radio value={1}>{t('enabled')}</Radio>
                <Radio value={0}>{t('disabled')}</Radio>
              </div>
            </Radio.Group>
          </PopoverContent>
        </Popover>
      )}

      {(handleInputChange || searchString !== undefined) && (
        <SearchInput
          className="w-28"
          placeholder={t('search')}
          onChange={handleInputChange}
          value={searchString}
        />
      )}

      {!isReadonly && (
        <Button variant="outline" size="icon" onClick={() => createChunk('')}>
          <Plus size={44} />
        </Button>
      )}
    </div>
  );
}
