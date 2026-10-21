import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { ChevronDown } from 'lucide-react';
import { type Ref, useEffect, useState } from 'react';
import type { BusinessDocumentQuestion } from '../types';
import { DocumentMarkdown } from './document-markdown';
import { appendVoiceTranscript, VoiceInput } from './voice-input';

const questionStatusPresentation = {
  OPEN: {
    label: 'Требует ответа',

    badgeClass:
      'border-accent-primary/20 bg-accent-primary/10 text-accent-primary',
  },
  ANSWERED: {
    label: 'Ответ зафиксирован',

    badgeClass:
      'border-state-success/20 bg-state-success/10 text-state-success',
  },
  CANCELLED: {
    label: 'Закрыт',

    badgeClass: 'border-border-button bg-bg-card text-text-disabled',
  },
} satisfies Record<
  BusinessDocumentQuestion['status'],
  {
    label: string;

    badgeClass: string;
  }
>;

interface QuestionItemProps {
  question: BusinessDocumentQuestion;
  canAnswer: boolean;
  pending: boolean;
  expanded: boolean;
  triggerRef: Ref<HTMLButtonElement>;
  onExpandedChange: (expanded: boolean) => void;
  onAnswer: (payload: Record<string, unknown>) => void;
}

export function QuestionItem({
  question,
  canAnswer,
  pending,
  expanded,
  triggerRef,
  onExpandedChange,
  onAnswer,
}: QuestionItemProps) {
  const [selectedOptionId, setSelectedOptionId] = useState('');
  const [customAnswer, setCustomAnswer] = useState('');
  const status = questionStatusPresentation[question.status];
  const isOpen = question.status === 'OPEN';
  const canSubmit =
    canAnswer &&
    isOpen &&
    !pending &&
    Boolean(selectedOptionId || customAnswer.trim());

  const selectOption = (value: string) => {
    setSelectedOptionId(value);
    setCustomAnswer('');
  };

  useEffect(() => {
    setSelectedOptionId(question.answer?.selected_option_id ?? '');
    setCustomAnswer(question.answer?.custom_answer ?? '');
  }, [question]);

  return (
    <Collapsible open={expanded} onOpenChange={onExpandedChange} asChild>
      <article
        data-protocol-kind="question"
        data-protocol-id={question.question_id}
        className={cn(
          'relative mx-4 my-3 overflow-hidden rounded-lg border border-accent-primary bg-bg-base p-3.5 transition-colors duration-200',
        )}
        data-testid="business-document-question"
        data-status={question.status}
      >
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <h3 className="text-[10px] font-medium uppercase tracking-wide text-text-secondary">
                    Вопрос
                    {question.sequence_number
                      ? ` ${question.sequence_number}`
                      : ''}
                  </h3>
                  {question.target_section_id && (
                    <span className="font-mono text-[11px] text-text-secondary">
                      § {question.target_section_id}
                    </span>
                  )}
                  <Badge
                    variant="outline"
                    className={cn(
                      'ms-auto h-5 px-2 py-0 text-[10px] font-medium leading-none',
                      status.badgeClass,
                    )}
                    data-testid={`question-status-${question.question_id}`}
                  >
                    {status.label}
                  </Badge>
                </div>
                <DocumentMarkdown
                  className={cn(
                    'mt-2 text-[13px] font-medium leading-5 text-text-primary',
                    !expanded && 'line-clamp-2',
                  )}
                >
                  {question.text}
                </DocumentMarkdown>
              </div>
              <CollapsibleTrigger asChild>
                <button
                  ref={triggerRef}
                  type="button"
                  className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-bg-card hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary"
                  aria-label={`${expanded ? 'Свернуть' : 'Развернуть'} вопрос${
                    question.sequence_number
                      ? ` ${question.sequence_number}`
                      : ''
                  }: ${question.text}`}
                  data-testid={`toggle-question-${question.question_id}`}
                >
                  <ChevronDown
                    className={`size-4 transition-transform duration-200 ${
                      expanded ? 'rotate-180' : ''
                    }`}
                  />
                </button>
              </CollapsibleTrigger>
            </div>

            <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
              <RadioGroup
                className="mt-3 gap-2"
                value={selectedOptionId}
                disabled={!canAnswer || !isOpen || pending}
                onValueChange={selectOption}
              >
                {question.options.map((option) => (
                  <div
                    key={option.option_id}
                    className="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors hover:bg-bg-card has-[:disabled]:cursor-default"
                    role="presentation"
                    onClick={(event) => {
                      if (
                        !canAnswer ||
                        !isOpen ||
                        pending ||
                        (event.target as HTMLElement).closest(
                          'a, button, input',
                        )
                      )
                        return;
                      selectOption(option.option_id);
                    }}
                  >
                    <RadioGroupItem
                      value={option.option_id}
                      aria-labelledby={`question-option-${question.question_id}-${option.option_id}`}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <DocumentMarkdown
                        id={`question-option-${question.question_id}-${option.option_id}`}
                        className="text-text-primary"
                      >
                        {option.label}
                      </DocumentMarkdown>
                      {option.description && (
                        <DocumentMarkdown className="mt-0.5 text-xs leading-5 text-text-secondary">
                          {option.description}
                        </DocumentMarkdown>
                      )}
                    </div>
                  </div>
                ))}
              </RadioGroup>

              {question.allow_custom_answer && isOpen && (
                <div className="relative mt-3">
                  <Textarea
                    value={customAnswer}
                    aria-label="Свой ответ"
                    placeholder="Свой ответ"
                    rows={1}
                    className="h-11 min-h-11 pe-11"
                    disabled={!canAnswer || pending}
                    onChange={(event) => {
                      setCustomAnswer(event.target.value);
                      if (event.target.value) setSelectedOptionId('');
                    }}
                  />
                  <div className="absolute end-2 top-2">
                    <VoiceInput
                      label="Ответ на вопрос"
                      disabled={!canAnswer || pending}
                      onTranscript={(transcript) => {
                        setCustomAnswer((value) =>
                          appendVoiceTranscript(value, transcript),
                        );
                        setSelectedOptionId('');
                      }}
                      testId={`voice-input-question-${question.question_id}`}
                    />
                  </div>
                </div>
              )}

              {isOpen ? (
                <div className="mt-3 flex items-center justify-between gap-3">
                  {!canAnswer && (
                    <span className="text-xs text-text-disabled">
                      Ответ сейчас недоступен
                    </span>
                  )}
                  <Button
                    size="sm"
                    variant="accent"
                    className="ms-auto"
                    data-testid={`answer-question-${question.question_id}`}
                    disabled={!canSubmit}
                    loading={pending}
                    onClick={() =>
                      onAnswer({
                        question_id: question.question_id,
                        selected_option_id: selectedOptionId || null,
                        custom_answer: customAnswer.trim() || null,
                      })
                    }
                  >
                    Ответить
                  </Button>
                </div>
              ) : null}
            </CollapsibleContent>
          </div>
        </div>
      </article>
    </Collapsible>
  );
}
