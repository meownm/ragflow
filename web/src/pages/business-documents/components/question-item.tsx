import { Button } from '@/components/ui/button';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { CheckCircle2, CircleHelp } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { BusinessDocumentQuestion } from '../types';
import { appendVoiceTranscript, VoiceInput } from './voice-input';

interface QuestionItemProps {
  question: BusinessDocumentQuestion;
  canAnswer: boolean;
  pending: boolean;
  onAnswer: (payload: Record<string, unknown>) => void;
}

export function QuestionItem({
  question,
  canAnswer,
  pending,
  onAnswer,
}: QuestionItemProps) {
  const [selectedOptionId, setSelectedOptionId] = useState('');
  const [customAnswer, setCustomAnswer] = useState('');
  const isOpen = question.status === 'OPEN';
  const canSubmit =
    canAnswer &&
    isOpen &&
    !pending &&
    Boolean(selectedOptionId || customAnswer.trim());

  useEffect(() => {
    setSelectedOptionId(question.answer?.selected_option_id ?? '');
    setCustomAnswer(question.answer?.custom_answer ?? '');
  }, [question]);

  return (
    <article
      className="border-b border-border-button px-5 py-5 last:border-b-0"
      data-testid="business-document-question"
    >
      <div className="flex items-start gap-3">
        {isOpen ? (
          <CircleHelp className="mt-0.5 size-4 shrink-0 text-accent-primary" />
        ) : (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-state-success" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Вопрос
              {question.sequence_number ? ` ${question.sequence_number}` : ''}
            </span>
            {question.target_section_id && (
              <span className="font-mono text-[11px] text-text-disabled">
                § {question.target_section_id}
              </span>
            )}
          </div>
          <h3 className="mt-2 text-sm font-medium leading-6 text-text-primary">
            {question.text}
          </h3>

          <RadioGroup
            className="mt-4 gap-2"
            value={selectedOptionId}
            disabled={!canAnswer || !isOpen || pending}
            onValueChange={(value) => {
              setSelectedOptionId(value);
              setCustomAnswer('');
            }}
          >
            {question.options.map((option) => (
              <label
                key={option.option_id}
                className="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-2 text-sm transition-colors hover:bg-bg-card has-[:disabled]:cursor-default"
              >
                <RadioGroupItem
                  value={option.option_id}
                  aria-label={option.label}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="block text-text-primary">
                    {option.label}
                  </span>
                  {option.description && (
                    <span className="mt-0.5 block text-xs leading-5 text-text-secondary">
                      {option.description}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </RadioGroup>

          {question.allow_custom_answer && isOpen && (
            <div className="relative mt-3">
              <Textarea
                value={customAnswer}
                aria-label="Свой ответ"
                placeholder="Свой ответ"
                className="min-h-20 pe-11"
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
          ) : (
            <p className="mt-3 text-xs text-state-success">
              Ответ зафиксирован
            </p>
          )}
        </div>
      </div>
    </article>
  );
}
