import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { IFlowTemplate } from '@/interfaces/database/agent';
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
interface IProps {
  data: IFlowTemplate;
  isCreate?: boolean;
  showModal(record: IFlowTemplate): void;
}

function getLocalizedText(values: Record<string, string>, language: string) {
  const selectedLanguage = [language, language.split('-')[0], 'en'].find(
    (key) => values[key]?.trim(),
  );
  return {
    text: selectedLanguage ? values[selectedLanguage] : '',
    language: selectedLanguage,
  };
}

export function TemplateCard({ data, showModal }: IProps) {
  const { t, i18n } = useTranslation();

  const handleClick = useCallback(() => {
    showModal(data);
  }, [data, showModal]);

  const language = i18n.language || 'en';
  const title = getLocalizedText(data.title, language);
  const description = getLocalizedText(data.description, language);

  return (
    <Card className="border-colors-outline-neutral-standard group relative min-h-40">
      <CardContent className="p-4 ">
        <div className="flex justify-start items-center gap-4 mb-4">
          <RAGFlowAvatar
            className="w-7 h-7"
            avatar={data.avatar ? data.avatar : 'https://github.com/shadcn.png'}
            name={title.text || 'CN'}
          ></RAGFlowAvatar>
          <div
            className="text-[18px] font-bold break-words hyphens-auto overflow-hidden"
            lang={title.language}
          >
            {title.text}
          </div>
        </div>
        <p className="break-words hypens-auto" lang={description.language}>
          {description.text}
        </p>
        <div className="group-hover:bg-gradient-to-t from-black/70 from-10% via-black/0 via-50% to-black/0 w-full h-full group-hover:block absolute top-0 left-0 hidden rounded-xl">
          <Button
            variant="default"
            className="w-1/3 absolute bottom-4 right-4 left-4 justify-center text-center m-auto"
            onClick={handleClick}
          >
            {t('flow.useTemplate')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
