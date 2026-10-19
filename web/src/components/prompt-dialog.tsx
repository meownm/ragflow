import { IModalProps } from '@/interfaces/common';
import { IFeedbackRequestBody } from '@/interfaces/request/chat';
import { useTranslation as useUiTranslation } from 'react-i18next';
import HighLightMarkdown from './highlight-markdown';
import SvgIcon from './svg-icon';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';

type PromptDialogProps = IModalProps<IFeedbackRequestBody> & {
  prompt?: string;
};

export function PromptDialog({
  visible,
  hideModal,
  prompt,
}: PromptDialogProps) {
  const { t: tUi } = useUiTranslation();
  return (
    <Dialog open={visible} onOpenChange={hideModal}>
      <DialogContent className="max-w-[80vw]">
        <DialogHeader>
          <DialogTitle>
            <div className="space-x-2">
              <SvgIcon name={`prompt`} width={18}></SvgIcon>
              <span> {tUi('flow.prompt')}</span>
            </div>
          </DialogTitle>
        </DialogHeader>
        <section className="max-h-[80vh] overflow-auto">
          <HighLightMarkdown>{prompt}</HighLightMarkdown>
        </section>
      </DialogContent>
    </Dialog>
  );
}
