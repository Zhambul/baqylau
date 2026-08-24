import { actorId, sessionId, taskId } from './domain-ids';
import type { ActorId, SessionId, TaskId } from './domain-ids';

export const SESSION_TABS = [
  'mirror',
  'agents',
  'monitors',
  'jobs',
  'errors',
] as const;

export type SessionTab = (typeof SESSION_TABS)[number];

type ListRoute = {
  readonly kind: 'list';
};

type StatsRoute = {
  readonly kind: 'stats';
};

type LaunchingRoute = {
  readonly kind: 'launching';
};

type MonitorDetail = {
  readonly kind: 'monitor';
  readonly taskId: TaskId;
};

type JobDetail = {
  readonly kind: 'job';
  readonly taskId: TaskId;
};

export type SessionDetail = MonitorDetail | JobDetail;

export type SessionRoute = {
  readonly kind: 'session';
  readonly sessionId: SessionId;
  readonly tab: SessionTab;
  readonly actorId?: ActorId;
  readonly detail?: SessionDetail;
};

export type NotFoundRoute = {
  readonly kind: 'not-found';
  readonly hash: string;
};

export type Route =
  ListRoute | StatsRoute | LaunchingRoute | SessionRoute | NotFoundRoute;

export type StartupNavigation = {
  readonly hash: string;
  readonly openNewSession: boolean;
  readonly consumeQuery: boolean;
};

export function startupNavigation(
  hash: string,
  search: string,
): StartupNavigation {
  if (hash.length > 0)
    return { hash, openNewSession: false, consumeQuery: false };
  const query = new URLSearchParams(search);
  const session = query.get('s');
  if (session !== null && session.length > 0)
    return {
      hash: `#/s/${encodeURIComponent(session)}`,
      openNewSession: false,
      consumeQuery: true,
    };
  const openNewSession = query.get('new') === '1';
  const attention = query.get('attn') === '1';
  return {
    hash: '#/',
    openNewSession,
    consumeQuery: openNewSession || attention,
  };
}

const LIST_ROUTE: ListRoute = { kind: 'list' };
const STATS_ROUTE: StatsRoute = { kind: 'stats' };
const LAUNCHING_ROUTE: LaunchingRoute = { kind: 'launching' };

function decodeSegment(segment: string): string | null {
  try {
    const decoded = decodeURIComponent(segment);
    return decoded.length > 0 ? decoded : null;
  } catch {
    return null;
  }
}

function sessionTab(segment: string | undefined): SessionTab | null {
  if (segment === undefined || segment === '') {
    return 'mirror';
  }
  return SESSION_TABS.find((tab) => tab === segment) ?? null;
}

function detail(
  kind: string | undefined,
  value: string | undefined,
): SessionDetail | null {
  if (kind === undefined && value === undefined) {
    return null;
  }
  if ((kind !== 'm' && kind !== 'j') || value === undefined) {
    return null;
  }
  const decodedTaskId = decodeSegment(value);
  if (decodedTaskId === null) {
    return null;
  }
  return kind === 'm'
    ? { kind: 'monitor', taskId: taskId(decodedTaskId) }
    : { kind: 'job', taskId: taskId(decodedTaskId) };
}

function sessionRoute(hash: string, segments: readonly string[]): Route {
  const decodedSessionId = decodeSegment(segments[1] ?? '');
  if (decodedSessionId === null) {
    return { kind: 'not-found', hash };
  }

  let position = 2;
  let scopedActorId: ActorId | undefined;
  if (segments[position] === 'a') {
    const decodedActorId = decodeSegment(segments[position + 1] ?? '');
    if (decodedActorId === null) {
      return { kind: 'not-found', hash };
    }
    scopedActorId = actorId(decodedActorId);
    position += 2;
  }

  const possibleDetail = detail(segments[position], segments[position + 1]);
  if (possibleDetail !== null) {
    if (segments.length !== position + 2) {
      return { kind: 'not-found', hash };
    }
    return {
      kind: 'session',
      sessionId: sessionId(decodedSessionId),
      tab: possibleDetail.kind === 'monitor' ? 'monitors' : 'jobs',
      ...(scopedActorId === undefined ? {} : { actorId: scopedActorId }),
      detail: possibleDetail,
    };
  }

  const tab = sessionTab(segments[position]);
  if (
    tab === null ||
    segments.length !== position + (segments[position] === undefined ? 0 : 1)
  ) {
    return { kind: 'not-found', hash };
  }
  return {
    kind: 'session',
    sessionId: sessionId(decodedSessionId),
    tab,
    ...(scopedActorId === undefined ? {} : { actorId: scopedActorId }),
  };
}

export function parseHash(hash: string): Route {
  const normalized = hash.startsWith('#') ? hash.slice(1) : hash;
  const path = normalized.startsWith('/') ? normalized.slice(1) : normalized;
  if (path === '') {
    return LIST_ROUTE;
  }
  const segments = path.split('/');
  if (segments.length === 1 && segments[0] === 'stats') {
    return STATS_ROUTE;
  }
  if (segments.length === 1 && segments[0] === 'launching') {
    return LAUNCHING_ROUTE;
  }
  if (segments[0] === 's') {
    return sessionRoute(hash, segments);
  }
  return { kind: 'not-found', hash };
}

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export function formatRoute(route: Exclude<Route, NotFoundRoute>): string {
  switch (route.kind) {
    case 'list':
      return '#/';
    case 'stats':
      return '#/stats';
    case 'launching':
      return '#/launching';
    case 'session': {
      const scope =
        route.actorId === undefined ? '' : `/a/${encoded(route.actorId)}`;
      if (route.detail !== undefined) {
        const detailKind = route.detail.kind === 'monitor' ? 'm' : 'j';
        return `#/s/${encoded(route.sessionId)}${scope}/${detailKind}/${encoded(route.detail.taskId)}`;
      }
      const tab = route.tab === 'mirror' ? '' : `/${route.tab}`;
      return `#/s/${encoded(route.sessionId)}${scope}${tab}`;
    }
  }
}

export function isSessionRoute(route: Route): route is SessionRoute {
  return route.kind === 'session';
}
