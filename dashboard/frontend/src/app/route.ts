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

type SettingsRoute = {
  readonly kind: 'settings';
};

/** One extension's settings; a workspace ID selects that workspace's override. */
export type ExtensionSettingsRoute = {
  readonly kind: 'extension-settings';
  readonly extensionId: string;
  readonly workspaceId?: string;
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

/** Name one extension view; the package manifest owns both identifiers. */
export type ExtensionViewRef = {
  readonly extensionId: string;
  readonly viewId: string;
};

export type SessionRoute = {
  readonly kind: 'session';
  readonly sessionId: SessionId;
  readonly tab: SessionTab;
  readonly actorId?: ActorId;
  readonly detail?: SessionDetail;
  readonly extensionView?: ExtensionViewRef;
};

export type ExtensionPageRoute = {
  readonly kind: 'extension-page';
  readonly workspaceId: string;
  readonly view: ExtensionViewRef;
};

/** A page of one repository, named by a directory in its worktree. */
export type RepositoryPageRoute = {
  readonly kind: 'repository-page';
  readonly directory: string;
  readonly view: ExtensionViewRef;
};

export type NotFoundRoute = {
  readonly kind: 'not-found';
  readonly hash: string;
};

export type Route =
  | ListRoute
  | StatsRoute
  | SettingsRoute
  | ExtensionSettingsRoute
  | LaunchingRoute
  | SessionRoute
  | ExtensionPageRoute
  | RepositoryPageRoute
  | NotFoundRoute;

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
const SETTINGS_ROUTE: SettingsRoute = { kind: 'settings' };
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

  if (segments[position] === 'x') {
    const view = extensionView(segments.slice(position + 1));
    if (view === null) {
      return { kind: 'not-found', hash };
    }
    return {
      kind: 'session',
      sessionId: sessionId(decodedSessionId),
      tab: 'mirror',
      ...(scopedActorId === undefined ? {} : { actorId: scopedActorId }),
      extensionView: view,
    };
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

function extensionView(segments: readonly string[]): ExtensionViewRef | null {
  if (segments.length !== 2) {
    return null;
  }
  const extensionId = decodeSegment(segments[0] ?? '');
  const viewId = decodeSegment(segments[1] ?? '');
  return extensionId === null || viewId === null
    ? null
    : { extensionId, viewId };
}

function extensionSettings(hash: string, segments: readonly string[]): Route {
  const extensionId = decodeSegment(segments[2] ?? '');
  if (extensionId === null) return { kind: 'not-found', hash };
  if (segments.length === 3) return { kind: 'extension-settings', extensionId };
  const workspaceId = decodeSegment(segments[4] ?? '');
  return segments.length === 5 && segments[3] === 'w' && workspaceId !== null
    ? { kind: 'extension-settings', extensionId, workspaceId }
    : { kind: 'not-found', hash };
}

function extensionPage(hash: string, segments: readonly string[]): Route {
  const workspaceId = decodeSegment(segments[1] ?? '');
  const view = segments[2] === 'x' ? extensionView(segments.slice(3)) : null;
  if (workspaceId === null || view === null) {
    return { kind: 'not-found', hash };
  }
  return { kind: 'extension-page', workspaceId, view };
}

function repositoryPage(hash: string, segments: readonly string[]): Route {
  const directory = decodeSegment(segments[1] ?? '');
  const view = segments[2] === 'x' ? extensionView(segments.slice(3)) : null;
  if (directory === null || view === null) {
    return { kind: 'not-found', hash };
  }
  return { kind: 'repository-page', directory, view };
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
  if (
    segments.length === 2 &&
    segments[0] === 'settings' &&
    segments[1] === 'extensions'
  ) {
    return SETTINGS_ROUTE;
  }
  if (segments[0] === 'settings' && segments[1] === 'extensions') {
    return extensionSettings(hash, segments);
  }
  if (segments.length === 1 && segments[0] === 'launching') {
    return LAUNCHING_ROUTE;
  }
  if (segments[0] === 's') {
    return sessionRoute(hash, segments);
  }
  if (segments[0] === 'w') {
    return extensionPage(hash, segments);
  }
  if (segments[0] === 'repo') {
    return repositoryPage(hash, segments);
  }
  return { kind: 'not-found', hash };
}

function encoded(value: string): string {
  return encodeURIComponent(value);
}

function viewPath(view: ExtensionViewRef): string {
  return `/x/${encoded(view.extensionId)}/${encoded(view.viewId)}`;
}

export function formatRoute(route: Exclude<Route, NotFoundRoute>): string {
  switch (route.kind) {
    case 'list':
      return '#/';
    case 'stats':
      return '#/stats';
    case 'settings':
      return '#/settings/extensions';
    case 'extension-settings': {
      const workspace =
        route.workspaceId === undefined
          ? ''
          : `/w/${encoded(route.workspaceId)}`;
      return `#/settings/extensions/${encoded(route.extensionId)}${workspace}`;
    }
    case 'launching':
      return '#/launching';
    case 'extension-page':
      return `#/w/${encoded(route.workspaceId)}${viewPath(route.view)}`;
    case 'repository-page':
      return `#/repo/${encoded(route.directory)}${viewPath(route.view)}`;
    case 'session': {
      const scope =
        route.actorId === undefined ? '' : `/a/${encoded(route.actorId)}`;
      if (route.extensionView !== undefined) {
        return `#/s/${encoded(route.sessionId)}${scope}${viewPath(route.extensionView)}`;
      }
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
