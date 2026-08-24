<script lang="ts">
  import { decidePlan, readPlanChoices } from '../../api/controls';
  import type { PlanChoice } from '../../controls/plan-choice';
  import type { PlanProposedEntry } from '../../entries/attention';
  import { markdownHtml } from '../../entries/markup';
  import TrustedHtml from '../../entries/TrustedHtml.svelte';
  import { newRequestId } from '../../shared/browser/identity';
  import { reportClientFailure } from '../../shared/browser/optimistic-action';
  import type { SessionViewState } from '../session-view-state.svelte';

  let { entry, view }: { entry: PlanProposedEntry; view: SessionViewState } =
    $props();

  let choices = $state<readonly PlanChoice[]>([]);
  let feedback = $state('');
  let loading = $state(true);
  let pending = $state(false);
  let failure = $state<string | null>(null);
  let requested = $state(false);

  $effect(() => {
    const capable = view.capabilities?.plan;
    if (requested || capable === undefined) return;
    requested = true;
    if (!capable) {
      loading = false;
      return;
    }
    void readPlanChoices(view.sessionId, newRequestId(), entry.body.attentionId)
      .then((result) => {
        if (result.kind === 'plan-choices') choices = result.choices;
        else if (result.status !== 'acknowledged')
          failure = result.reason ?? 'the session did not return plan choices';
      })
      .catch((error: unknown) => {
        failure = error instanceof Error ? error.message : String(error);
      })
      .finally(() => {
        loading = false;
      });
  });

  async function decide(choice: PlanChoice): Promise<void> {
    if (pending) return;
    pending = true;
    failure = null;
    const requestId = newRequestId();
    view.showPendingAction(
      'plan',
      entry.body.attentionId,
      choice.feedback ? feedback.length : null,
    );
    try {
      const result = await decidePlan(
        view.sessionId,
        requestId,
        entry.body.attentionId,
        choice.digit,
        choice.feedback ? feedback : null,
      );
      if (result.status !== 'acknowledged') {
        view.dropPendingAction('plan', entry.body.attentionId, result.status);
        failure = result.reason ?? 'the session did not confirm the decision';
        pending = false;
      }
    } catch (error) {
      view.dropPendingAction('plan', entry.body.attentionId, 'plan-failed');
      reportClientFailure(
        view.sessionId,
        'plan',
        error,
        choice.feedback ? feedback.length : null,
      );
      failure = error instanceof Error ? error.message : String(error);
      pending = false;
    }
  }
</script>

<div class="planwrap">
  <div class:pending class="plancard">
    <div class="plantitle">
      {view.harness?.displayName ?? 'the agent'} proposes a plan
    </div>
    <div class="planbody md">
      <TrustedHtml html={markdownHtml(entry.body.plan.text)} />
    </div>
    {#if loading}
      <div class="plandim">loading decisions…</div>
    {:else}
      <div class="planbtns">
        {#each choices as choice (choice.digit)}
          <button
            class="planopt"
            type="button"
            disabled={pending}
            onclick={() => decide(choice)}>{choice.label}</button
          >
        {/each}
      </div>
      {#if choices.some((choice) => choice.feedback)}
        <div class="planfb">
          <input
            bind:value={feedback}
            class="askother"
            placeholder="feedback for requested changes…"
          />
        </div>
      {/if}
    {/if}
    {#if failure !== null}<div class="plandim" role="alert">{failure}</div>{/if}
  </div>
</div>
