<script lang="ts">
  import { onDestroy } from 'svelte';

  import { answerQuestion } from '../../api/controls';
  import { saveDialogDraft } from '../../api/session-preferences';
  import type { QuestionAskedEntry } from '../../entries/attention';
  import { newRequestId } from '../../shared/browser/identity';
  import { reportClientFailure } from '../../shared/browser/optimistic-action';
  import type { SessionViewState } from '../session-view-state.svelte';

  type Answer = {
    selected: string[];
    other: string;
  };

  const SAVE_DELAY_MS = 350;

  let { entry, view }: { entry: QuestionAskedEntry; view: SessionViewState } =
    $props();

  let answers = $state<Answer[]>([]);
  let seededAttention = $state('');
  let pending = $state(false);
  let failure = $state<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;

  const canAnswer = $derived(view.capabilities?.answer === true);
  const complete = $derived(
    answers.length === entry.body.questions.length &&
      answers.every(
        (answer) =>
          answer.selected.length > 0 || answer.other.trim().length > 0,
      ),
  );

  $effect(() => {
    const attentionId = entry.body.attentionId;
    if (seededAttention === attentionId) return;
    const saved = view.application?.dialog.draft;
    answers = entry.body.questions.map((_, index) => {
      if (
        saved?.attentionId === attentionId &&
        saved.answers.length === entry.body.questions.length
      ) {
        const answer = saved.answers[index];
        if (answer !== undefined)
          return { selected: [...answer.selected], other: answer.other };
      }
      return { selected: [], other: '' };
    });
    seededAttention = attentionId;
  });

  onDestroy(() => {
    if (timer !== null) clearTimeout(timer);
  });

  function snapshot(): readonly Answer[] {
    return answers.map((answer) => ({
      selected: [...answer.selected],
      other: answer.other,
    }));
  }

  function scheduleSave(): void {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      void saveDialogDraft(
        view.sessionId,
        entry.body.attentionId,
        view.clientId,
        snapshot(),
      ).catch((error: unknown) => {
        failure = error instanceof Error ? error.message : String(error);
      });
    }, SAVE_DELAY_MS);
  }

  function toggle(index: number, label: string, multiple: boolean): void {
    const answer = answers[index];
    if (answer === undefined) return;
    if (multiple) {
      answer.selected = answer.selected.includes(label)
        ? answer.selected.filter((value) => value !== label)
        : [...answer.selected, label];
    } else {
      answer.selected = [label];
    }
    scheduleSave();
  }

  function otherInput(index: number, multiple: boolean): void {
    const answer = answers[index];
    if (answer === undefined) return;
    if (!multiple && answer.other.trim().length > 0) answer.selected = [];
    scheduleSave();
  }

  async function submit(discuss: boolean): Promise<void> {
    if (pending || !canAnswer || (!discuss && !complete)) return;
    pending = true;
    failure = null;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    const requestId = newRequestId();
    view.showPendingAction('answer', entry.body.attentionId);
    try {
      const result = await answerQuestion(
        view.sessionId,
        requestId,
        entry.body.attentionId,
        discuss ? 'discuss' : 'answer',
        discuss ? null : snapshot(),
        null,
      );
      if (result.status !== 'acknowledged') {
        view.dropPendingAction('answer', entry.body.attentionId, result.status);
        failure = result.reason ?? 'the session did not confirm the answer';
        pending = false;
      }
    } catch (error) {
      view.dropPendingAction('answer', entry.body.attentionId, 'answer-failed');
      reportClientFailure(view.sessionId, 'answer', error);
      failure = error instanceof Error ? error.message : String(error);
      pending = false;
    }
  }
</script>

<div class="askwrap">
  <div class:pending class="askcard">
    <div class="askhead">
      <span class="asktitle">
        {view.harness?.displayName ?? 'the agent'} is asking{entry.body
          .questions.length > 1
          ? ` — ${String(entry.body.questions.length)} questions`
          : ''}
      </span>
      <button
        class="askchat"
        type="button"
        disabled={!canAnswer || pending}
        onclick={() => submit(true)}>chat about this</button
      >
    </div>
    {#each entry.body.questions as question, index (question.questionId)}
      <div class="askq">
        <div class="askqhead">
          {#if question.title !== null}
            <span class="askhdr">{question.title}</span>
          {/if}
          <span class="askqtext">{question.question}</span>
          <span class:multi={question.multiple} class="askpick">
            {question.multiple ? 'pick any' : 'pick one'}
          </span>
        </div>
        <div class:multi={question.multiple} class="askopts">
          {#each question.choices as choice (choice.label)}
            <button
              class:on={answers[index]?.selected.includes(choice.label) ??
                false}
              class="askopt"
              type="button"
              disabled={!canAnswer || pending}
              onclick={() => {
                toggle(index, choice.label, question.multiple);
              }}
            >
              <span class="amark"></span>
              <span class="aotxt">
                <span class="aol">{choice.label}</span>
                {#if choice.description !== null}
                  <span class="aod">{choice.description}</span>
                {/if}
              </span>
            </button>
          {/each}
        </div>
        {#if answers[index] !== undefined}
          <input
            bind:value={answers[index].other}
            class:on={answers[index].other.trim().length > 0 &&
              (question.multiple || answers[index].selected.length === 0)}
            class="askother"
            type="text"
            spellcheck="false"
            disabled={!canAnswer || pending}
            placeholder={question.multiple
              ? 'add your own answer…'
              : 'or type your own answer…'}
            oninput={() => {
              otherInput(index, question.multiple);
            }}
          />
        {/if}
      </div>
    {/each}
    <div class="askfoot">
      <button
        class="asksubmit"
        type="button"
        disabled={!canAnswer || pending || !complete}
        onclick={() => submit(false)}
        >{pending
          ? 'submitting…'
          : entry.body.questions.length > 1
            ? 'submit answers'
            : 'submit answer'}</button
      >
    </div>
    {#if failure !== null}<div class="ask-failure" role="alert">
        {failure}
      </div>{/if}
  </div>
</div>

<style>
  .ask-failure {
    margin-top: 8px;
    color: var(--red);
    font-size: 11px;
  }
</style>
