import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import {
  BookmarkPlus,
  Loader2,
  MessageSquareText,
  Quote,
  Send,
  Sparkles,
  Trash2,
} from 'lucide-react';
import {
  type SetStateAction,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type {
  BusinessDocumentCommandType,
  BusinessDocumentReviewCycle,
  BusinessDocumentRevision,
  BusinessDocumentSelection,
} from '../types';
import { DocumentMarkdown } from './document-markdown';
import { ProposalItem } from './proposal-item';
import { QuestionItem } from './question-item';
import { ResizeHandle } from './resize-handle';
import { ReviewHistory } from './review-history';
import { appendVoiceTranscript, VoiceInput } from './voice-input';

const dispositionLabels = {
  NEEDS_QUESTION: 'Требует уточнения',
  CONFIRMED_CHANGE: 'Подтверждено к правке',
  NO_CHANGE: 'Без изменения',
} as const;

interface ProtocolPaneProps {
  documentId: string;
  storageIdentity: string | null;
  reviewCycle: BusinessDocumentReviewCycle | null;
  previousCycles?: Array<
    BusinessDocumentReviewCycle & { review_cycle: number }
  >;
  onAnalyze?: () => void;
  proposalDecisionsOpen: boolean;
  revision: BusinessDocumentRevision | null;
  selection: BusinessDocumentSelection | null;
  allowedCommands: BusinessDocumentCommandType[];
  pending: boolean;
  busy: boolean;
  editable: boolean;
  onCommand: (
    type: BusinessDocumentCommandType,
    payload: Record<string, unknown>,
    onSuccess?: () => void,
  ) => void;
  onClearSelection: () => void;
  focusRequest?: {
    kind: 'question' | 'proposal' | 'comment';
    id: string;
    sequence: number;
  } | null;
}

export function ProtocolPane({
  documentId,
  storageIdentity,
  reviewCycle,
  previousCycles = [],
  onAnalyze,
  proposalDecisionsOpen,
  revision,
  selection,
  allowedCommands,
  pending,
  busy,
  editable,
  onCommand,
  onClearSelection,
  focusRequest,
}: ProtocolPaneProps) {
  const [comment, setComment] = useState('');
  const draftVersion = useRef(0);
  const updateComment = useCallback((value: SetStateAction<string>) => {
    draftVersion.current += 1;
    setComment(value);
  }, []);
  const [tab, setTab] = useState<'work' | 'history'>('work');
  const minimumComposerHeight = selection ? 260 : 188;
  const [composerHeight, setComposerHeight] = useState(188);
  const paneRef = useRef<HTMLElement>(null);
  const [composerMaxHeight, setComposerMaxHeight] = useState(520);
  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    const observer = new ResizeObserver(() => {
      const maximum = Math.max(
        minimumComposerHeight,
        Math.min(520, pane.clientHeight - 180),
      );
      setComposerMaxHeight(maximum);
      setComposerHeight((height) => Math.min(height, maximum));
    });
    observer.observe(pane);
    return () => observer.disconnect();
  }, [minimumComposerHeight]);
  useEffect(() => {
    setComposerHeight((height) => Math.max(height, minimumComposerHeight));
  }, [minimumComposerHeight]);
  useEffect(() => {
    setTab('work');
  }, [documentId]);
  const [savedPrompts, setSavedPrompts] = useState<string[]>([]);
  const [savedPromptsOpen, setSavedPromptsOpen] = useState(false);
  const [loadedDraftStorageKey, setLoadedDraftStorageKey] = useState<
    string | null
  >(null);
  const [expandedQuestionId, setExpandedQuestionId] = useState<string | null>(
    null,
  );
  const [questionToFocus, setQuestionToFocus] = useState<string | null>(null);
  const questionTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const focusedSequence = useRef<number | null>(null);
  const allowed = useMemo(() => new Set(allowedCommands), [allowedCommands]);
  const canComment = allowed.has('ADD_COMMENT');
  const hasComment = Boolean(comment.trim());
  const showProcessing = busy && !hasComment;
  const draftStorageKey = storageIdentity
    ? `ragflow.business-documents.prompt-draft.${storageIdentity}.${documentId}`
    : null;
  useLayoutEffect(() => {
    draftVersion.current += 1;
    return () => {
      draftVersion.current += 1;
    };
  }, [draftStorageKey, selection]);
  const savedStorageKey = storageIdentity
    ? `ragflow.business-documents.saved-prompts.${storageIdentity}.${documentId}`
    : null;
  const questions = reviewCycle?.questions ?? [];
  const proposals = reviewCycle?.proposals ?? [];
  const answeredCount = questions.filter((q) => q.status === 'ANSWERED').length;
  const cancelledCount = questions.filter(
    (q) => q.status === 'CANCELLED',
  ).length;
  const questionCount = questions.length - cancelledCount;
  const decidedCount = proposals.filter((p) => p.decision !== 'PENDING').length;

  const activeQuestions = useMemo(
    () =>
      (reviewCycle?.questions ?? []).filter(
        (question) => question.status === 'OPEN',
      ),
    [reviewCycle],
  );
  const activeProposals = useMemo(
    () =>
      (reviewCycle?.proposals ?? []).filter(
        (proposal) =>
          (proposal.decision === 'PENDING' && proposalDecisionsOpen) ||
          (proposal.decision === 'ACCEPTED' && proposal.applied === false),
      ),
    [reviewCycle, proposalDecisionsOpen],
  );
  const activeProposalIds = new Set(
    activeProposals.map((proposal) => proposal.proposal_id),
  );
  const currentHistory = {
    questions: questions.filter((question) => question.status !== 'OPEN'),
    proposals: proposals.filter(
      (proposal) => !activeProposalIds.has(proposal.proposal_id),
    ),
    comments: [],
  };
  const historyCycles = [currentHistory, ...previousCycles];
  const historyCount = historyCycles.reduce(
    (count, cycle) =>
      count +
      cycle.questions.length +
      cycle.proposals.length +
      cycle.comments.length,
    0,
  );
  const workCount =
    activeQuestions.length +
    activeProposals.length +
    (reviewCycle?.comments.length ?? 0);
  useEffect(() => {
    if (!focusRequest || focusedSequence.current === focusRequest.sequence)
      return;
    const inWork =
      (focusRequest.kind === 'question' &&
        activeQuestions.some((item) => item.question_id === focusRequest.id)) ||
      (focusRequest.kind === 'proposal' &&
        activeProposals.some((item) => item.proposal_id === focusRequest.id)) ||
      (focusRequest.kind === 'comment' &&
        reviewCycle?.comments.some(
          (item) => item.comment_id === focusRequest.id,
        ));
    const nextTab = inWork ? 'work' : 'history';
    if (tab !== nextTab) {
      setTab(nextTab);
      return;
    }
    if (focusRequest.kind === 'question' && inWork)
      setExpandedQuestionId(focusRequest.id);

    const entry = Array.from(
      scrollContainerRef.current?.querySelectorAll<HTMLElement>(
        '[data-protocol-kind]',
      ) ?? [],
    ).find(
      (candidate) =>
        candidate.dataset.protocolKind === focusRequest.kind &&
        candidate.dataset.protocolId === focusRequest.id,
    );
    if (!entry) return;
    focusedSequence.current = focusRequest.sequence;
    entry.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    entry.classList.add('ring-2', 'ring-inset', 'ring-accent-primary');
    const timeout = window.setTimeout(
      () =>
        entry.classList.remove('ring-2', 'ring-inset', 'ring-accent-primary'),
      3000,
    );
    return () => window.clearTimeout(timeout);
  }, [focusRequest, tab, reviewCycle, activeQuestions, activeProposals]);

  useEffect(() => {
    if (!revision) onClearSelection();
  }, [onClearSelection, revision]);

  useEffect(() => {
    if (!draftStorageKey || !savedStorageKey || !editable) {
      updateComment('');
      setSavedPrompts([]);
      setLoadedDraftStorageKey(null);
      return;
    }
    try {
      window.localStorage.removeItem(
        `ragflow.business-documents.prompt-draft.${documentId}`,
      );
      window.localStorage.removeItem(
        `ragflow.business-documents.saved-prompts.${documentId}`,
      );
      updateComment(window.localStorage.getItem(draftStorageKey) ?? '');
      const stored = JSON.parse(
        window.localStorage.getItem(savedStorageKey) ?? '[]',
      );
      setSavedPrompts(
        Array.isArray(stored)
          ? stored.filter((item): item is string => typeof item === 'string')
          : [],
      );
      setLoadedDraftStorageKey(draftStorageKey);
    } catch {
      setSavedPrompts([]);
      setLoadedDraftStorageKey(draftStorageKey);
    }
  }, [documentId, draftStorageKey, editable, savedStorageKey, updateComment]);

  useEffect(() => {
    if (
      !draftStorageKey ||
      !editable ||
      loadedDraftStorageKey !== draftStorageKey
    )
      return;
    try {
      if (comment) window.localStorage.setItem(draftStorageKey, comment);
      else window.localStorage.removeItem(draftStorageKey);
    } catch {
      // Draft persistence is best-effort; the editor must remain usable when
      // storage is unavailable or its quota is exhausted.
    }
  }, [comment, draftStorageKey, editable, loadedDraftStorageKey]);

  const persistSavedPrompts = (items: string[]) => {
    if (!savedStorageKey || !editable) return;
    setSavedPrompts(items);
    try {
      window.localStorage.setItem(savedStorageKey, JSON.stringify(items));
    } catch {
      // Keep the in-memory notes usable for the current page session.
    }
  };

  const savePromptForLater = () => {
    const text = comment.trim();
    if (!text || !editable) return;
    persistSavedPrompts(
      [text, ...savedPrompts.filter((item) => item !== text)].slice(0, 20),
    );
    setSavedPromptsOpen(true);
  };

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
    if (
      !text ||
      !editable ||
      !canComment ||
      pending ||
      loadedDraftStorageKey !== draftStorageKey
    )
      return;
    const submittedDraftVersion = draftVersion.current;
    onCommand(
      'ADD_COMMENT',
      {
        text,
        revision_id: selection?.revision_id ?? revision?.revision_id,
        section_id: selection?.section_id ?? null,
        anchor: selection,
      },
      () => {
        if (draftVersion.current !== submittedDraftVersion) return;
        updateComment('');
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
      ref={paneRef}
      className="flex min-h-0 min-w-0 flex-col bg-bg-card"
      aria-label="Обсуждение"
      data-testid="business-document-protocol"
    >
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border-button px-4">
        <h2 className="text-[13px] font-medium">Обсуждение</h2>
        {onAnalyze && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 bg-accent-primary/10 px-2 text-accent-primary"
            aria-label="Проанализировать замечания"
            disabled={pending}
            onClick={onAnalyze}
          >
            <Sparkles className="size-3.5" />
            Анализировать
          </Button>
        )}
      </div>
      <div
        className="flex h-9 shrink-0 items-center gap-1 px-4"
        role="tablist"
        aria-label="Текущие и завершённые обсуждения"
      >
        <Button
          role="tab"
          id="review-work-tab"
          aria-controls="review-work-panel"
          aria-selected={tab === 'work'}
          size="sm"
          variant={tab === 'work' ? 'secondary' : 'ghost'}
          onClick={() => setTab('work')}
        >
          В работе · {workCount}
        </Button>
        <Button
          role="tab"
          id="review-history-tab"
          aria-controls="review-history-panel"
          aria-selected={tab === 'history'}
          aria-label={`Завершённые обсуждения · ${historyCount}`}
          size="sm"
          variant={tab === 'history' ? 'secondary' : 'ghost'}
          onClick={() => setTab('history')}
        >
          Завершённые · {historyCount}
        </Button>
      </div>
      {tab === 'work' && (questions.length > 0 || proposals.length > 0) && (
        <div
          className="grid shrink-0 grid-cols-2 gap-4 border-b border-border-button px-4 py-1.5"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          data-testid="business-document-review-progress"
        >
          {[
            {
              label: 'Ответы',
              value: answeredCount,
              total: questionCount,
              detail: cancelledCount ? `Закрыто: ${cancelledCount}` : '',
              description: 'Ответы на вопросы',
            },
            {
              label: 'Предложения',
              value: decidedCount,
              total: proposals.length,
              detail:
                !proposalDecisionsOpen && decidedCount < proposals.length
                  ? `Без решения: ${proposals.length - decidedCount}`
                  : '',
              description: 'Решения по предложениям',
            },
          ].map(({ label, value, total, detail, description }) => (
            <div key={label} className="min-w-0" title={description}>
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="text-text-secondary">{label}</span>
                <span className="font-medium tabular-nums text-text-primary">
                  {total ? `${value}/${total}` : '—'}
                </span>
              </div>
              {total > 0 && (
                <div
                  role="progressbar"
                  aria-label={description}
                  aria-valuemin={0}
                  aria-valuemax={total}
                  aria-valuenow={value}
                  className="mt-1.5 h-1 overflow-hidden rounded-full bg-bg-card"
                >
                  <div
                    className={`h-full rounded-full transition-[width] motion-reduce:transition-none ${value === total ? 'bg-state-success' : 'bg-accent-primary'}`}
                    style={{ width: `${(value / total) * 100}%` }}
                  />
                </div>
              )}
              {detail && (
                <p className="mt-1 text-[10px] text-text-secondary">{detail}</p>
              )}
            </div>
          ))}
        </div>
      )}

      <div
        ref={scrollContainerRef}
        className="min-h-0 flex-1 overflow-y-auto scrollbar-auto"
        data-testid="business-document-protocol-scroll"
        role="tabpanel"
        id={tab === 'work' ? 'review-work-panel' : 'review-history-panel'}
        aria-labelledby={
          tab === 'work' ? 'review-work-tab' : 'review-history-tab'
        }
      >
        {tab === 'history' ? (
          <ReviewHistory
            documentId={documentId}
            cycles={historyCycles}
            focusId={focusRequest?.id}
          />
        ) : (
          <>
            {!hasProtocolEntries && (
              <div
                className="px-5 py-10 text-center"
                data-testid="business-document-protocol-empty"
              >
                <MessageSquareText className="mx-auto size-7 stroke-[1.25] text-text-disabled" />
                <p className="mt-3 text-sm font-medium text-text-primary">
                  {historyCount > 0
                    ? 'В работе пока ничего нет'
                    : 'Обсуждение пока пусто'}
                </p>
                <p className="mt-1 text-xs leading-5 text-text-secondary">
                  {historyCount > 0
                    ? 'Завершённые обсуждения доступны на вкладке «Завершённые».'
                    : 'Вопросы, предложения и комментарии появятся здесь.'}
                </p>
              </div>
            )}

            {activeQuestions.map((question) => (
              <QuestionItem
                key={question.question_id}
                question={question}
                canAnswer={allowed.has('ANSWER_QUESTION')}
                pending={pending}
                expanded={expandedQuestionId === question.question_id}
                triggerRef={(trigger) => {
                  if (trigger) {
                    questionTriggerRefs.current.set(
                      question.question_id,
                      trigger,
                    );
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

            {activeProposals.map((proposal) => (
              <ProposalItem
                key={proposal.proposal_id}
                proposal={proposal}
                proposalDecisionsOpen={proposalDecisionsOpen}
                canDecide={allowed.has('DECIDE_PROPOSAL')}
                canCancelAcceptance={allowed.has('CANCEL_PROPOSAL_ACCEPTANCE')}
                pending={pending}
                onDecide={(payload) =>
                  onCommand(
                    payload.decision === 'ACCEPTED'
                      ? 'ACCEPT_PROPOSAL'
                      : 'DECIDE_PROPOSAL',
                    payload.decision === 'ACCEPTED'
                      ? { proposal_id: payload.proposal_id }
                      : payload,
                  )
                }
                onCancelAcceptance={() =>
                  onCommand('CANCEL_PROPOSAL_ACCEPTANCE', {
                    proposal_id: proposal.proposal_id,
                  })
                }
              />
            ))}

            {reviewCycle?.comments.map((item) => (
              <article
                key={item.comment_id}
                data-protocol-kind="comment"
                data-protocol-id={item.comment_id}
                className="border-b border-border-button px-5 py-4 last:border-b-0"
                data-testid="business-document-comment"
              >
                <div className="flex items-center justify-between gap-2 text-xs text-text-secondary">
                  <span>Комментарий автора</span>
                  <span
                    className={item.section_id ? 'font-mono' : undefined}
                    data-testid="business-document-comment-scope"
                  >
                    {item.section_id ? `§ ${item.section_id}` : 'Весь документ'}
                  </span>
                </div>
                {item.anchor?.selected_text && (
                  <div className="mt-2">
                    {item.anchor_status === 'ORPHANED' && (
                      <p
                        className="mb-1 text-[11px] font-medium text-state-error"
                        data-testid="business-document-orphaned-anchor"
                      >
                        Фрагмент относится к предыдущей редакции
                      </p>
                    )}
                    <blockquote
                      className={`line-clamp-3 border-s-2 ps-3 text-xs italic leading-5 text-text-secondary ${
                        item.anchor_status === 'ORPHANED'
                          ? 'border-state-error/60'
                          : 'border-accent-primary/60'
                      }`}
                    >
                      <DocumentMarkdown>
                        {item.anchor.selected_text}
                      </DocumentMarkdown>
                    </blockquote>
                  </div>
                )}
                <DocumentMarkdown className="mt-2 text-sm leading-6 text-text-primary">
                  {item.text}
                </DocumentMarkdown>
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
            {!workCount && hasProtocolEntries && (
              <p className="px-4 py-8 text-center text-xs text-text-secondary">
                Всё разобрано. Ответы и решения сохранены в истории.
              </p>
            )}
          </>
        )}
      </div>

      <div
        className="flex min-h-[188px] shrink-0 flex-col border-t border-border-button bg-bg-card px-4 pb-3"
        style={{ height: Math.max(minimumComposerHeight, composerHeight) }}
        data-testid="business-document-comment-composer"
      >
        <ResizeHandle
          direction="height"
          label="Изменить высоту ввода"
          testId="business-document-composer-resizer"
          value={composerHeight}
          min={minimumComposerHeight}
          max={composerMaxHeight}
          onChange={setComposerHeight}
          onReset={() => setComposerHeight(minimumComposerHeight)}
        />
        <div
          className="mb-2 flex shrink-0 items-center gap-2 text-xs text-text-secondary"
          data-testid="business-document-comment-scope-selector"
        >
          <span>К документу</span>
          <span className="rounded-full bg-bg-card px-2 py-0.5 font-medium text-text-primary">
            {selection
              ? `выделенный фрагмент · § ${selection.section_id}`
              : 'весь документ'}
          </span>
          {savedPrompts.length > 0 && (
            <Popover open={savedPromptsOpen} onOpenChange={setSavedPromptsOpen}>
              <PopoverTrigger asChild>
                <Button size="sm" variant="ghost" className="h-6 px-1 text-xs">
                  Заметки · {savedPrompts.length}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-80 p-0">
                <div
                  className="max-h-64 overflow-y-auto rounded-md border border-border-button bg-bg-base scrollbar-auto"
                  data-testid="business-document-saved-prompts"
                >
                  <div className="border-b border-border-button px-3 py-2 text-[11px] text-text-secondary">
                    <p className="font-medium">Заметки к документу</p>
                    <p className="mt-1">
                      Только в этом браузере. Сохранение в заметки не отправляет
                      комментарий в обсуждение.
                    </p>
                  </div>
                  {savedPrompts.map((prompt, index) => (
                    <div
                      key={`${prompt}-${index}`}
                      className="flex items-start gap-2 border-b border-border-button px-3 py-2 last:border-b-0"
                    >
                      <button
                        type="button"
                        className="line-clamp-2 min-w-0 flex-1 text-start text-xs leading-5 text-text-primary hover:text-accent-primary"
                        onClick={() => updateComment(prompt)}
                      >
                        {prompt}
                      </button>
                      <button
                        type="button"
                        className="mt-0.5 shrink-0 text-text-disabled hover:text-state-error"
                        aria-label={`Удалить заметку ${index + 1}`}
                        onClick={() =>
                          persistSavedPrompts(
                            savedPrompts.filter(
                              (_, itemIndex) => itemIndex !== index,
                            ),
                          )
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="ms-auto h-6 px-1 text-xs"
            onClick={() =>
              setComposerHeight((height) =>
                height > minimumComposerHeight
                  ? minimumComposerHeight
                  : Math.min(320, composerMaxHeight),
              )
            }
          >
            {composerHeight > minimumComposerHeight ? 'Свернуть' : 'Развернуть'}
          </Button>
        </div>
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
        <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border-button bg-bg-base">
          <div className="relative min-h-0 flex-1">
            <Textarea
              value={comment}
              rows={2}
              aria-label="Комментарий"
              placeholder={
                selection
                  ? 'Комментарий к выделенному фрагменту'
                  : 'Комментарий ко всему документу'
              }
              className="h-full min-h-0 rounded-b-none border-0 bg-transparent pe-3 text-[13px] focus-visible:ring-inset"
              disabled={!editable}
              onChange={(event) => updateComment(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  submitComment();
                }
              }}
            />
          </div>
          <div className="flex shrink-0 items-center gap-1 px-2 pb-2">
            <VoiceInput
              label="Замечание к документу"
              disabled={!editable || pending}
              onTranscript={(transcript) =>
                updateComment((value) =>
                  appendVoiceTranscript(value, transcript),
                )
              }
              testId="voice-input-comment"
            />
            <Button
              size="icon"
              variant="ghost"
              aria-label="Сохранить в заметки"
              title="Сохранить в заметки"
              disabled={!comment.trim() || !editable}
              onClick={savePromptForLater}
              data-testid="business-document-save-prompt"
            >
              <BookmarkPlus className="size-4" />
            </Button>
            <Button
              size="icon"
              className="ms-auto"
              variant="accent"
              aria-label={
                showProcessing
                  ? 'Идёт обработка документа'
                  : 'Добавить комментарий'
              }
              disabled={
                !hasComment ||
                !editable ||
                !canComment ||
                pending ||
                loadedDraftStorageKey !== draftStorageKey
              }
              aria-busy={showProcessing}
              onClick={submitComment}
            >
              {showProcessing ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="size-4" aria-hidden="true" />
              )}
            </Button>
          </div>
        </div>
        <p className="mt-2 line-clamp-2 shrink-0 text-[11px] leading-4 text-text-secondary">
          {canComment
            ? pending
              ? 'Пока идёт обработка, можно подготовить следующий комментарий и добавить его в заметки'
              : selection
                ? 'Уберите привязку, чтобы оставить комментарий ко всему документу · Ctrl + Enter — отправить'
                : 'Чтобы привязать комментарий к месту, выделите текст в документе · Ctrl + Enter — отправить'
            : editable
              ? 'Можно подготовить комментарий и добавить его в заметки; отправка сейчас недоступна'
              : 'Комментарии сейчас недоступны'}
        </p>
      </div>
    </aside>
  );
}
