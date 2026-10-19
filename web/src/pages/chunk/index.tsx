import { PageHeader } from '@/components/page-header';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb';
import { Button } from '@/components/ui/button';
import { Segmented, SegmentedValue } from '@/components/ui/segmented';
import {
  QueryStringMap,
  useNavigatePage,
} from '@/hooks/logic-hooks/navigate-hooks';
import { Routes } from '@/routes';
import { EllipsisVertical, Save } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation as useUiTranslation } from 'react-i18next';
import { Outlet, useLocation } from 'react-router';

export default function ChunkPage() {
  const { t: tUi } = useUiTranslation();
  const { navigateToDataset, getQueryString, navigateToChunk } =
    useNavigatePage();
  const location = useLocation();

  const options = useMemo(() => {
    return [
      {
        label: 'Parsed results',
        value: Routes.ParsedResult,
      },
      {
        label: 'Chunk result',
        value: Routes.ChunkResult,
      },
      {
        label: 'Result view',
        value: Routes.ResultView,
      },
    ];
  }, []);

  const path = useMemo(() => {
    return location.pathname.split('/').slice(0, 3).join('/');
  }, [location.pathname]);

  return (
    <section>
      <PageHeader>
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink
                onClick={navigateToDataset(
                  getQueryString(QueryStringMap.KnowledgeId) as string,
                )}
              >
                {tUi('header.flow')}
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>xxx</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <div>
          <Segmented
            options={options}
            value={path}
            onChange={navigateToChunk as (val: SegmentedValue) => void}
          ></Segmented>
        </div>
        <div className="flex items-center gap-2">
          <Button variant={'ghost'} size={'icon'}>
            <EllipsisVertical />
          </Button>
          <Button size={'sm'}>
            <Save />
            {tUi('common.save')}
          </Button>
        </div>
      </PageHeader>
      <Outlet />
    </section>
  );
}
