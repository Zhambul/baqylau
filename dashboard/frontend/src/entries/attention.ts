import type { Entry } from './model';

export type QuestionAskedEntry = Extract<
  Entry,
  { readonly type: 'question_asked' }
>;
export type PlanProposedEntry = Extract<
  Entry,
  { readonly type: 'plan_proposed' }
>;

export type PendingAttention = {
  readonly question: QuestionAskedEntry | null;
  readonly plan: PlanProposedEntry | null;
};

export function pendingAttention(entries: readonly Entry[]): PendingAttention {
  const openQuestions = new Map<string, QuestionAskedEntry>();
  const openPlans = new Map<string, PlanProposedEntry>();
  for (const entry of [...entries].reverse()) {
    switch (entry.type) {
      case 'question_asked':
        openQuestions.set(entry.body.attentionId, entry);
        break;
      case 'question_answered':
        openQuestions.delete(entry.body.attentionId);
        break;
      case 'plan_proposed':
        openPlans.set(entry.body.attentionId, entry);
        break;
      case 'plan_resolved':
        openPlans.delete(entry.body.attentionId);
        break;
      default:
        break;
    }
  }
  return {
    question: [...openQuestions.values()].at(-1) ?? null,
    plan: [...openPlans.values()].at(-1) ?? null,
  };
}
