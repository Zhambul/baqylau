<script lang="ts">
  import { getAppState } from '../../app/app-context';
  import { formatRoute } from '../../app/route';
  import { directoryName, sessionStatus } from '../../sessions/derived';
  import type { SessionSnapshot } from '../../sessions/model';

  const BASE_TITLE = 'baqylau';
  const APPEARANCE = new Map([
    ['awaiting_attention', { className: 'ask', rank: 0 }],
    ['awaiting_response', { className: 'done', rank: 1 }],
    ['thinking', { className: 'busy', rank: 2 }],
    ['working', { className: 'busy', rank: 2 }],
    ['executing', { className: 'run', rank: 3 }],
    ['awaiting_background', { className: 'run', rank: 3 }],
  ]);
  const ASK_FAVICON = `data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
      <circle cx="100" cy="100" r="82" fill="none" stroke="#E9B949" stroke-width="8"/>
      <g stroke="#9aa7b0" stroke-width="6" stroke-linecap="round">
        <path d="M100 84V18M111.31 88.69l46.67-46.67M116 100h66M111.31 111.31l46.67 46.67M100 116v66M88.69 111.31l-46.67 46.67M84 100H18M88.69 88.69L42.02 42.02"/>
      </g>
      <g fill="#E9B949"><circle cx="100" cy="18" r="9"/><circle cx="157.98" cy="42.02" r="9"/><circle cx="182" cy="100" r="9"/><circle cx="157.98" cy="157.98" r="9"/><circle cx="100" cy="182" r="9"/><circle cx="42.02" cy="157.98" r="9"/><circle cx="18" cy="100" r="9"/><circle cx="42.02" cy="42.02" r="9"/></g>
      <circle cx="100" cy="100" r="16" fill="none" stroke="#9aa7b0" stroke-width="6"/><circle cx="100" cy="100" r="8" fill="#E9B949"/><circle cx="168" cy="32" r="30" fill="#e06c75"/>
    </svg>`,
  )}`;

  const appState = getAppState();
  let normalFavicon = '';

  function label(snapshot: SessionSnapshot): string {
    return (
      snapshot.session.title ??
      (directoryName(snapshot.session.workingDirectory) ||
        snapshot.session.sessionId)
    );
  }

  function appearance(snapshot: SessionSnapshot): {
    readonly className: string;
    readonly rank: number;
  } {
    return (
      APPEARANCE.get(sessionStatus(snapshot) ?? '') ?? {
        className: 'idle',
        rank: 4,
      }
    );
  }

  const live = $derived.by(() =>
    appState.sessions
      .filter((snapshot) => snapshot.live)
      .sort(
        (left, right) =>
          appearance(left).rank - appearance(right).rank ||
          label(left).localeCompare(label(right)) ||
          left.session.sessionId.localeCompare(right.session.sessionId),
      ),
  );
  const asking = $derived(
    live.filter((snapshot) => sessionStatus(snapshot) === 'awaiting_attention')
      .length,
  );

  $effect(() => {
    document.body.classList.toggle('attn-on', live.length > 0);
    document.title =
      asking > 0 ? `(${String(asking)}) ${BASE_TITLE}` : BASE_TITLE;
    const favicon = document.querySelector<HTMLLinkElement>('#favicon');
    if (favicon !== null) {
      if (normalFavicon.length === 0) normalFavicon = favicon.href;
      favicon.href = asking > 0 ? ASK_FAVICON : normalFavicon;
    }
  });
</script>

<div id="attn" hidden={live.length === 0}>
  {#if asking > 0}
    <span class="alead ask">{asking} asking</span>
  {/if}
  {#each live as snapshot (snapshot.session.sessionId)}
    {@const state = sessionStatus(snapshot)}
    <a
      class={[
        'attn-pill',
        appearance(snapshot).className,
        {
          self:
            appState.route.kind === 'session' &&
            appState.route.sessionId === snapshot.session.sessionId,
        },
      ]}
      href={formatRoute({
        kind: 'session',
        sessionId: snapshot.session.sessionId,
        tab: 'mirror',
      })}
      title={`${state ?? 'no tab'} · ${snapshot.session.sessionId}`}
    >
      <span class="adot"></span>
      <span class="alabel">{label(snapshot)}</span>
    </a>
  {/each}
</div>
