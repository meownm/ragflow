import { CardSineLineContainer } from '@/components/card-singleline-container';
import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import { HomeIcon } from '@/components/svg-icon';
import { Segmented, SegmentedValue } from '@/components/ui/segmented';
import { getNavigationSectionForPath } from '@/constants/navigation';
import { useSystemConfig } from '@/hooks/use-system-request';
import { Routes } from '@/routes';
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { Agents } from './agent-list';
import { SeeAllAppCard } from './application-card';
import { ChatList } from './chat-list';
import { MemoryList } from './memory-list';
import { SearchList } from './search-list';

const IconMap = {
  [Routes.Chats]: 'chats',
  [Routes.Searches]: 'searches',
  [Routes.Agents]: 'agents',
  [Routes.Memories]: 'memory',
};

const EmptyTypeMap = {
  [Routes.Chats]: EmptyCardType.Chat,
  [Routes.Searches]: EmptyCardType.Search,
  [Routes.Agents]: EmptyCardType.Agent,
  [Routes.Memories]: EmptyCardType.Memory,
};

export function Applications() {
  const [selected, setVal] = useState(Routes.Chats);
  const { config } = useSystemConfig();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [listLength, setListLength] = useState(0);
  const [loading, setLoading] = useState(false);

  const options = useMemo(() => {
    const allowed = config?.accessibleSections ?? config?.visibleSections ?? [];
    return [
      { value: Routes.Chats, label: t('header.chat') },
      { value: Routes.Searches, label: t('header.search') },
      { value: Routes.Agents, label: t('header.flow') },
      { value: Routes.Memories, label: t('header.memories') },
    ].filter(({ value }) =>
      allowed.includes(getNavigationSectionForPath(value)!),
    );
  }, [t, config]);
  const val = options.some(({ value }) => value === selected)
    ? selected
    : options[0]?.value;
  const handleNavigate = useCallback(
    ({ isCreate }: { isCreate?: boolean }) => {
      if (val) navigate(val + (isCreate ? '?isCreate=true' : ''));
    },
    [navigate, val],
  );

  const handleChange = (path: SegmentedValue) => {
    setVal(path as Routes);
    setListLength(0);
    setLoading(true);
  };

  if (!val) return null;
  return (
    <section className="mt-8">
      <header className="mb-4 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="flex items-center text-xl font-semibold sm:text-2xl">
          <HomeIcon
            imgClass="me-2.5"
            name={`${IconMap[val as keyof typeof IconMap]}`}
            width={24}
          />
          {options.find((x) => x.value === val)?.label}
        </h2>

        <div
          role="group"
          aria-label={t('header.applicationType', {
            defaultValue: 'Application type',
          })}
          className="max-w-full"
        >
          <Segmented
            buttonSize="lg"
            className="max-w-full flex-wrap gap-1"
            itemClassName="min-h-11 px-2"
            options={options}
            value={val}
            onChange={handleChange}
          />
        </div>
      </header>

      <CardSineLineContainer>
        {val === Routes.Agents && (
          <Agents
            setListLength={(length: number) => setListLength(length)}
            setLoading={(loading: boolean) => setLoading(loading)}
          />
        )}
        {val === Routes.Chats && (
          <ChatList
            setListLength={(length: number) => setListLength(length)}
            setLoading={(loading: boolean) => setLoading(loading)}
          />
        )}
        {val === Routes.Searches && (
          <SearchList
            setListLength={(length: number) => setListLength(length)}
            setLoading={(loading: boolean) => setLoading(loading)}
          />
        )}
        {val === Routes.Memories && (
          <MemoryList
            setListLength={(length: number) => setListLength(length)}
            setLoading={(loading: boolean) => setLoading(loading)}
          />
        )}
        {listLength > 0 && (
          <SeeAllAppCard click={() => handleNavigate({ isCreate: false })} />
        )}
      </CardSineLineContainer>

      {listLength <= 0 && !loading && (
        <EmptyAppCard
          type={EmptyTypeMap[val as keyof typeof EmptyTypeMap]}
          onClick={() => handleNavigate({ isCreate: true })}
        />
      )}
    </section>
  );
}
