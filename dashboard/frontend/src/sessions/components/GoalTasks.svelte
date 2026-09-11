<script lang="ts">
  import { onDestroy } from 'svelte';

  import { dismissGoal } from '../../api/session-preferences';

  import type { Session } from '../model';

  const CONFIRM_MILLISECONDS = 4_000;
  let { session, hidden = false }: { session: Session; hidden?: boolean } =
    $props();
  let saving = $state(false);
  let failure = $state<string | null>(null);
  let armedGoal = $state<string | null>(null);
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;
  const goalKey = $derived(
    JSON.stringify([session.sessionId, session.goal?.objective]),
  );

  onDestroy(resetConfirmation);

  $effect(() => {
    if (!session.goal?.completed) resetConfirmation();
  });

  function resetConfirmation(): void {
    armedGoal = null;
    if (confirmTimer !== null) clearTimeout(confirmTimer);
    confirmTimer = null;
  }

  function requestDismiss(): void {
    if (saving) return;
    if (armedGoal !== goalKey) {
      resetConfirmation();
      armedGoal = goalKey;
      confirmTimer = setTimeout(resetConfirmation, CONFIRM_MILLISECONDS);
      return;
    }
    resetConfirmation();
    void saveDismissal();
  }

  async function saveDismissal(): Promise<void> {
    if (!session.goal?.completed || !session.goal.objective) return;
    saving = true;
    failure = null;
    try {
      await dismissGoal(session.sessionId, session.goal.objective);
    } catch {
      failure = 'Could not hide the goal. Try again.';
    } finally {
      saving = false;
    }
  }

  function goalMark(state: NonNullable<Session['goal']>['state']): string {
    switch (state) {
      case 'completed':
        return '✓';
      case 'blocked':
      case 'usage_limited':
      case 'budget_limited':
        return '!';
      case 'paused':
        return 'Ⅱ';
      case 'cleared':
        return '−';
      case 'active':
        return '◎';
    }
  }

  function goalLabel(state: NonNullable<Session['goal']>['state']): string {
    if (state === 'completed') return 'achieved';
    return state.replace('_', ' ');
  }

  function taskClass(state: Session['tasks'][number]['state']): string {
    switch (state) {
      case 'pending':
        return 'pend';
      case 'in_progress':
        return 'active';
      case 'completed':
        return 'done';
      case 'deleted':
        return 'done';
    }
  }

  function taskMark(state: Session['tasks'][number]['state']): string {
    switch (state) {
      case 'pending':
        return '○';
      case 'in_progress':
        return '◉';
      case 'completed':
        return '✓';
      case 'deleted':
        return '−';
    }
  }
</script>

{#if session.goal?.objective && !(session.goal.completed && hidden)}
  <div class="goalwrap">
    <div class:met={session.goal.completed} class="goalcard">
      <div class="goalhead">
        <span class="goalmark">{goalMark(session.goal.state)}</span>
        <span class="goaltitle">goal</span>
        <span class="goalstate">{goalLabel(session.goal.state)}</span>
        {#if session.goal.completed}
          <button
            type="button"
            class="taskshide"
            disabled={saving}
            aria-busy={saving}
            class:arm={armedGoal === goalKey}
            aria-label={armedGoal === goalKey
              ? 'Confirm dismiss completed goal'
              : 'Dismiss completed goal'}
            title="hide this completed goal"
            onkeydown={(event) => {
              if (event.key === 'Escape') resetConfirmation();
            }}
            onclick={requestDismiss}
            >{saving
              ? 'hiding…'
              : armedGoal === goalKey
                ? 'hide?'
                : '✕'}</button
          >
        {/if}
      </div>
      <div class="goalcond">{session.goal.objective}</div>
      {#if failure}<div role="alert">{failure}</div>{/if}
      {#if session.goal.reason}
        <div class="goalreason">{session.goal.reason}</div>
      {/if}
    </div>
  </div>
{/if}

{#if session.tasks.length > 0}
  <div class="taskswrap">
    <div class="taskscard">
      <div class="taskshead">
        <span class="taskstitle">tasks</span>
        <span class="taskscount">{session.tasks.length}</span>
      </div>
      <div class="tasklist">
        {#each session.tasks as task (task.taskId)}
          <div class={['taskrow', taskClass(task.state)]}>
            <span class="taskmark">{taskMark(task.state)}</span>
            <span class="taskid">{task.taskId}</span>
            <span class="tasksubj">{task.subject}</span>
            {#if task.state === 'in_progress'}
              <span class="taskactive">active</span>
            {/if}
          </div>
        {/each}
      </div>
    </div>
  </div>
{/if}
