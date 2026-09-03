import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { MessageSquareText, Quote, Send } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  BusinessDocumentCommandType,
  BusinessDocumentReviewCycle,
  BusinessDocumentRevision,
  BusinessDocumentSelection,
} from '../types';
import { ProposalItem } from './proposal-item';
import { QuestionItem } from './question-item';
import { appendVoiceTranscript, VoiceInput } from './voice-input';

const dispositionLabels = {
  NEEDS_QUESTION: 'Требует уточнения',
  CONFIRMED_CHANGE: 'Подтверждено к правке',
  NO_CHANGE: 'Без изменения',
} as const;

interface ProtocolPaneProps {
  reviewCycle: BusinessDocumentReviewCycle | null;
  reviewCycleNumber: number;
  proposalDecisionsOpen: boolean;
  revision: BusinessDocumentRevision | null;
  selection: BusinessDocumentSelection | null;
  allowedCommands: BusinessDocumentCommandType[];
  pending: boolean;
  onCommand: (
    type: BusinessDocumentCommandType,
    payload: Record<string, unknown>,
    onSuccess?: () => void,
  ) => void;
  onClearSelection: () => void;
}

export function ProtocolPane({
  reviewCycle,
  reviewCycleNumber,
  proposalDecisionsOpen,
  revision,
  selection,
  allowedCommands,
  pending,
  onCommand,
  onClearSelection,
}: ProtocolPaneProps) {
  const [comment, setComment] = useState('');
  const [expandedQuestionId, setExpandedQuestionId] = useState<string | null>(
    null,
  );
  const [questionToFocus, setQuestionToFocus] = useState<string | null>(null);
  const questionTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const allowed = useMemo(() => new Set(allowedCommands), [allowedCommands]);
  const canComment = allowed.has('ADD_COMMENT');

  useEffect(() => {
    if (!revision) onClearSelection();
  }, [onClearSelection, revision]);

  useEffect(() => {
    const questions = reviewCycle?.questions ?? [];
    setExpandedQuestionId((currentQuestionId) => {
      const currentQuestion = questions.find(
        (question) => question.question_id === currentQuestionId,
      );
      if (currentQuestion?.status === 'OPEN') return currentQuestionId;
      return (
        questions.find((question) => question.status === 'OPEN')?.question_id ??
        null
      );
    });
  }, [reviewCycle?.questions]);

  useEffect(() => {
    if (!questionToFocus || expandedQuestionId !== questionToFocus) return;

    const trigger = questionTriggerRefs.current.get(questionToFocus);
    if (!trigger) return;

    trigger.focus({ preventScroll: true });
    const scrollContainer = scrollContainerRef.current;
    if (scrollContainer) {
      const triggerRect = trigger.getBoundingClientRect();
      const containerRect = scrollContainer.getBoundingClientRect();
      const isOutsideViewport =
        triggerRect.top < containerRect.top ||
        triggerRect.bottom > containerRect.bottom;
      if (isOutsideViewport) {
        trigger.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
      }
    }
    setQuestionToFocus(null);
  }, [expandedQuestionId, questionToFocus]);

  const advanceToNextQuestion = useCallback(
    (answeredQuestionId: string) => {
      const questions = reviewCycle?.questions ?? [];
      const answeredIndex = questions.findIndex(
        (question) => question.question_id === answeredQuestionId,
      );
      const orderedCandidates =
        answeredIndex >= 0
          ? [
              ...questions.slice(answeredIndex + 1),
              ...questions.slice(0, answeredIndex),
            ]
          : questions;
      const nextQuestion = orderedCandidates.find(
        (question) =>
          question.question_id !== answeredQuestionId &&
          question.status === 'OPEN',
      );
      const nextQuestionId = nextQuestion?.question_id ?? null;

      setExpandedQuestionId(nextQuestionId);
      setQuestionToFocus(nextQuestionId);
    },
    [reviewCycle?.questions],
  );

  const submitComment = () => {
    const text = comment.trim();
    if (!text || !canComment || pending) return;
    onCommand(
      'ADD_COMMENT',
      {
        text,
        revision_id: selection?.revision_id ?? revision?.revision_id,
        section_id: selection?.section_id ?? null,
        anchor: selection,
      },
      () => {
        setComment('');
        onClearSelection();
      },
    );
  };

  const hasProtocolEntries = Boolean(
    reviewCycle &&
    (reviewCycle.questions.length ||
      reviewCycle.proposals.length ||
      reviewCycle.comments.length),
  );

  return (
    <aside
      className="flex min-h-0 flex-col border-s border-border-button bg-bg-component"
      aria-label="Протокол"
      data-testid="business-document-protocol"
    >
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border-button px-5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <MessageSquareText className="size-4 text-text-secondary" />
          Протокол
        </div>
        {reviewCycleNumber > 0 && (
          <span className="font-mono text-xs text-text-secondary">
            Цикл {reviewCycleNumber}
          </span>
        )}
      </div>

      <div
        ref={scrollContainerRef}
        className="min-h-0 flex-1 overflow-y-auto scrollbar-auto"
        data-testid="business-document-protocol-scroll"
      >
        {!hasProtocolEntries && (
          <div
            className="px-5 py-10 text-center"
            data-testid="business-document-protocol-empty"
          >
            <MessageSquareText className="mx-auto size-7 stroke-[1.25] text-text-disabled" />
            <p className="mt-3 text-sm font-medium text-text-primary">
              Протокол пока пуст
            </p>
            <p className="mt-1 text-xs leading-5 text-text-secondary">
              Вопросы, предложения и комментарии появятся здесь.
            </p>
          </div>
        )}

        {reviewCycle?.questions.map((question) => (
          <QuestionItem
            key={question.question_id}
            question={question}
            canAnswer={allowed.has('ANSWER_QUESTION')}
            pending={pending}
            expanded={expandedQuestionId === question.question_id}
            triggerRef={(trigger) => {
              if (trigger) {
                questionTriggerRefs.current.set(question.question_id, trigger);
              } else {
                questionTriggerRefs.current.delete(question.question_id);
              }
            }}
            onExpandedChange={(expanded) =>
              setExpandedQuestionId(expanded ? question.question_id : null)
            }
            onAnswer={(payload) =>
              onCommand('ANSWER_QUESTION', payload, () =>
                advanceToNextQuestion(question.question_id),
              )
            }
          />
        ))}

        {reviewCycle?.proposals.map((proposal) => (
          <ProposalItem
            key={proposal.proposal_id}
            proposal={proposal}
            proposalDecisionsOpen={proposalDecisionsOpen}
            canDecide={allowed.has('DECIDE_PROPOSAL')}
            pending={pending}
            onDecide={(payload) => onCommand('DECIDE_PROPOSAL', payload)}
          />
        ))}

        {reviewCycle?.comments.map((item) => (
          <article
            key={item.comment_id}
            className="border-b border-border-button px-5 py-4 last:border-b-0"
            data-testid="business-document-comment"
          >
            <div className="flex items-center justify-between gap-2 text-xs text-text-secondary">
              <span>Комментарий автора</span>
              {item.section_id && (
                <span className="font-mono">§ {item.section_id}</span>
              )}
            </div>
            {item.anchor?.selected_text && (
              <div className="mt-2">
                {item.anchor_status === 'ORPHANED' && (
                  <p
                    className="mb-1 text-[11px] font-medium text-state-error"
                    data-testid="business-document-orphaned-anchor"
                  >
                    Фрагмент относится к предыдущей ревизии
                  </p>
                )}
                <blockquote
                  className={`line-clamp-3 border-s-2 ps-3 text-xs italic leading-5 text-text-secondary ${
                    item.anchor_status === 'ORPHANED'
                      ? 'border-state-error/60'
                      : 'border-accent-primary/60'
                  }`}
                >
                  {item.anchor.selected_text}
                </blockquote>
              </div>
            )}
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-text-primary">
              {item.text}
            </p>
            {item.disposition && (
              <p
                className="mt-2 text-[11px] font-medium text-text-secondary"
                data-testid="business-document-comment-disposition"
              >
                {dispositionLabels[item.disposition.disposition]}
              </p>
            )}
          </article>
        ))}
      </div>

      <div
        className="shrink-0 border-t border-border-button bg-bg-component p-4"
        data-testid="business-document-comment-composer"
      >
        {selection && (
          <div className="mb-3 flex items-start gap-2 rounded-md bg-accent-primary/5 px-3 py-2 text-xs text-text-secondary">
            <Quote className="mt-0.5 size-3.5 shrink-0 text-accent-primary" />
            <span className="line-clamp-2 flex-1 italic">
              {selection.selected_text}
            </span>
            <button
              type="button"
              className="shrink-0 text-text-secondary hover:text-text-primary"
              onClick={onClearSelection}
              aria-label="Убрать привязку"
            >
              ×
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <div className="relative flex-1">
            <Textarea
              value={comment}
              autoSize={{ minRows: 2, maxRows: 6 }}
              aria-label="Комментарий"
              placeholder="Оставить комментарий"
              className="pe-11"
              disabled={!canComment || pending}
              onChange={(event) => setComment(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  submitComment();
                }
              }}
            />
            <div className="absolute end-2 top-2">
              <VoiceInput
                label="Замечание к документу"
                disabled={!canComment || pending}
                onTranscript={(transcript) =>
                  setComment((value) =>
                    appendVoiceTranscript(value, transcript),
                  )
                }
                testId="voice-input-comment"
              />
            </div>
          </div>
          <Button
            size="icon-lg"
            variant="accent"
            aria-label="Добавить комментарий"
            disabled={!comment.trim() || !canComment || pending}
            loading={pending}
            onClick={submitComment}
          >
            <Send className="size-4" />
          </Button>
        </div>
        <p className="mt-2 text-[11px] text-text-disabled">
          {canComment
            ? 'Ctrl + Enter — отправить'
            : 'Комментарии сейчас недоступны'}
        </p>
      </div>
    </aside>
  );
}
