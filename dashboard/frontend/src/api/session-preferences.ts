import type { ClientId, SessionId } from '../app/domain-ids';
import type { ViewMode } from '../application/session-model';
import { apiClient, execute } from './client';

export async function saveComposerDraft(
  sessionId: SessionId,
  text: string,
  origin: ClientId,
  sequence: number,
  signal?: AbortSignal,
): Promise<boolean> {
  const response = await execute(() =>
    apiClient.POST('/api/sessions/{session_id}/application/composer-draft', {
      params: { path: { session_id: sessionId } },
      body: { text, origin, sequence },
      // Composer drafts are small and must finish when a person reloads or
      // leaves the page before the debounce timer settles.
      keepalive: true,
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return response.saved;
}

export async function saveViewMode(
  sessionId: SessionId,
  viewMode: ViewMode,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/sessions/{session_id}/application/view-mode', {
      params: { path: { session_id: sessionId } },
      body: { view_mode: viewMode },
    }),
  );
}

export async function saveNotificationsMuted(
  sessionId: SessionId,
  muted: boolean,
): Promise<void> {
  await execute(() =>
    apiClient.POST(
      '/api/sessions/{session_id}/application/notifications-muted',
      {
        params: { path: { session_id: sessionId } },
        body: { muted },
      },
    ),
  );
}

export async function saveTasksHidden(
  sessionId: SessionId,
  hidden: boolean,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/sessions/{session_id}/application/tasks-hidden', {
      params: { path: { session_id: sessionId } },
      body: { hidden },
    }),
  );
}

export async function saveDialogDraft(
  sessionId: SessionId,
  attentionId: string,
  origin: ClientId,
  answers: readonly {
    readonly selected: readonly string[];
    readonly other: string;
  }[],
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/sessions/{session_id}/application/dialog-draft', {
      params: { path: { session_id: sessionId } },
      body: {
        attention_id: attentionId,
        origin,
        answers: answers.map((answer) => ({
          selected: [...answer.selected],
          other: answer.other,
        })),
      },
    }),
  );
}
