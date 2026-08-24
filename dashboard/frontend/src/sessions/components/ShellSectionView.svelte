<script lang="ts">
  import { taskId } from '../../app/domain-ids';
  import { formatRoute } from '../../app/route';
  import type { SessionDetail } from '../../app/route';
  import type { SessionViewState } from '../session-view-state.svelte';
  import type { ShellFold } from '../shell-fold';

  let {
    kind,
    detail,
    view,
  }: {
    kind: 'monitor' | 'job';
    detail: SessionDetail | undefined;
    view: SessionViewState;
  } = $props();

  const items = $derived(kind === 'monitor' ? view.monitors : view.jobs);
  const selected = $derived(
    detail?.kind === kind
      ? (items.find((item) => item.shellId === detail.taskId) ?? null)
      : null,
  );
  const label = $derived(kind === 'monitor' ? 'monitors' : 'jobs');
  const glyph = $derived(kind === 'monitor' ? '◉' : '◷');

  function detailHref(item: ShellFold): string {
    return formatRoute({
      kind: 'session',
      sessionId: view.sessionId,
      tab: kind === 'monitor' ? 'monitors' : 'jobs',
      ...(view.actorId === undefined ? {} : { actorId: view.actorId }),
      detail: { kind, taskId: taskId(item.shellId) },
    });
  }

  function listHref(): string {
    return formatRoute({
      kind: 'session',
      sessionId: view.sessionId,
      tab: kind === 'monitor' ? 'monitors' : 'jobs',
      ...(view.actorId === undefined ? {} : { actorId: view.actorId }),
    });
  }

  function status(item: ShellFold): string {
    if (item.live) return 'running';
    if (item.state === null) return 'unknown';
    return item.state === 'succeeded' ? 'finished' : item.state;
  }

  function statusClass(item: ShellFold): string {
    if (item.live) return 'st-run';
    if (item.state === 'failed' || item.state === 'cancelled') return 'st-bad';
    if (item.state === null) return 'st-warn';
    return 'st-ok';
  }

  function lines(item: ShellFold): number {
    return item.output.length === 0 ? 0 : item.output.split('\n').length;
  }
</script>

{#if detail !== undefined}
  <div class="crumbs">
    <a class="crumb" href={listHref()}>
      <span class="cg">{glyph}</span>
      {label}
    </a>
    <span class="csep">›</span>
    <span class="crumb cur"
      ><span class="cg">{glyph}</span>
      {selected?.summary ?? selected?.command ?? detail.taskId}</span
    >
  </div>
  {#if selected === null}
    <div class="empty">{kind} not found</div>
  {:else}
    <div class="mdetail">
      <div class="mdhead">
        <span class={['k', kind === 'monitor' ? 'k-monitor' : 'k-job']}>
          {glyph}
          {kind === 'monitor' ? 'monitor' : 'background'}
        </span>
        <span class={statusClass(selected)}>{status(selected)}</span>
      </div>
      {#if selected.summary !== null}
        <div class="mdesc">{selected.summary}</div>
      {/if}
      <div class="lbl">command</div>
      <div class="jcmd"><pre class="opl">{selected.command}</pre></div>
      <div class="mmeta">
        <span class="mk">task</span><span class="mv">{selected.shellId}</span>
        {#if kind === 'monitor'}
          <span class="mk">events</span><span class="mv"
            >{selected.statusOutput.split('\n').filter(Boolean).length}</span
          >
        {:else}
          <span class="mk">lines</span><span class="mv">{lines(selected)}</span>
        {/if}
        <span class="mk">started</span><span class="mv"
          >{new Date(selected.startedAt * 1000).toLocaleString()}</span
        >
        {#if selected.finishedAt !== null}
          <span class="mk">ended</span><span class="mv"
            >{new Date(selected.finishedAt * 1000).toLocaleString()}</span
          >
        {/if}
      </div>
    </div>
    <div class="mevents">
      <div class="mhead">{kind === 'monitor' ? 'events' : 'output'}</div>
      {#if kind === 'monitor'}
        {#each selected.statusOutput
          .split('\n')
          .filter(Boolean)
          .reverse() as event, index (`${event}:${String(index)}`)}
          <div class="mev"><span class="mtxt">{event}</span></div>
        {:else}
          <div class="empty">
            {selected.live ? 'no events yet — waiting' : 'no events fired'}
          </div>
        {/each}
      {:else}
        <div class="joutput">
          {#if selected.output.trim().length > 0}
            <pre>{selected.output}</pre>
          {:else}
            <div class="empty">
              {selected.live ? 'no output yet' : '(no output)'}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
{:else}
  <div class="sgrid">
    {#if items.length === 0}
      <div class="empty">
        no {kind === 'monitor' ? 'monitors' : 'background jobs'} in this session
      </div>
    {:else}
      {#each items as item (item.shellId)}
        <a class="acard" data-st={statusClass(item)} href={detailHref(item)}>
          <div class="actorId">{glyph} {item.summary ?? item.command}</div>
          <div class="desc">{item.shellId}</div>
          <div class="meta">
            <span class={statusClass(item)}>{status(item)}</span>
            {#if kind === 'monitor'}
              <span
                >{item.statusOutput.split('\n').filter(Boolean).length} events</span
              >
            {:else}
              <span>{lines(item)} lines</span>
            {/if}
          </div>
        </a>
      {/each}
    {/if}
  </div>
{/if}
