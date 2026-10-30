import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { formatDate } from '@/utils/date';
import { t } from 'i18next';
import { ChevronRight } from 'lucide-react';

type ApplicationCardProps = {
  app: {
    avatar?: string;
    title: string;
    update_time: number;
  };
  onClick?(): void;
  moreDropdown: React.ReactNode;
};

export function ApplicationCard({
  app,
  onClick,
  moreDropdown,
}: ApplicationCardProps) {
  return (
    <Card className="w-[264px]" onClick={onClick} as="article">
      <CardContent className="p-2.5 group flex justify-between w-full">
        <div className="flex items-center gap-2.5 w-full">
          <RAGFlowAvatar
            className="size-14 rounded-lg"
            avatar={app.avatar}
            name={app.title || 'CN'}
            aria-hidden="true"
          />

          <div className="flex-1">
            <h3 className="text-sm font-normal line-clamp-1 mb-1 text-ellipsis w-[160px] overflow-hidden">
              {app.title}
            </h3>
            <p className="text-xs font-normal text-text-secondary">
              {formatDate(app.update_time)}
            </p>
          </div>
        </div>

        {moreDropdown}
      </CardContent>
    </Card>
  );
}

export type SeeAllAppCardProps = {
  click(): void;
};

export function SeeAllAppCard({ click }: SeeAllAppCardProps) {
  return (
    <Button
      type="button"
      variant="outline"
      className="min-h-[76px] h-full w-full whitespace-normal text-text-secondary"
      onClick={click}
    >
      {t('common.seeAll')} <ChevronRight className="size-4" />
    </Button>
  );
}
