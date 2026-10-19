import { Button } from '@/components/ui/button';
import { Copy } from 'lucide-react';
import { useTranslation as useUiTranslation } from 'react-i18next';

interface ChunkToolbarProps {
  text: string;
}

export function ChunkToolbar({ text }: ChunkToolbarProps) {
  const { t: tUi } = useUiTranslation();
  return (
    <div className="flex justify-between px-9">
      <span className="text-colors-text-neutral-strong text-3xl font-bold">
        {text}
      </span>
      <div className="flex items-center gap-3">
        <Button variant={'ghost'} size={'icon'}>
          <Copy />
        </Button>
        <Button variant={'outline'} size={'sm'}>
          {tUi('common.export')}
        </Button>
      </div>
    </div>
  );
}
