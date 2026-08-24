import { sessionId } from '../app/domain-ids';
import type { HarnessCatalog } from '../harnesses/model';
import type {
  LaunchInput,
  LaunchResult,
  ResumableSession,
} from '../new-session/model';
import { apiClient, execute } from './client';

export async function readResumableSessions(
  workingDirectory: string,
  search: string,
  signal?: AbortSignal,
): Promise<readonly ResumableSession[]> {
  const rows = await execute(() =>
    apiClient.GET('/api/resumable-sessions', {
      params: {
        query: {
          working_directory: workingDirectory,
          search: search.length > 0 ? search : null,
        },
      },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return rows.map((row) => ({
    sessionId: sessionId(row.session_id),
    title: row.title,
    lastActivityAt: row.last_activity_at,
    active: row.active,
    harness: row.harness,
    model:
      row.model === null
        ? null
        : {
            id: row.model.name,
            displayName: row.model.display_name ?? row.model.name,
          },
    effort: row.effort,
    account:
      row.account === null
        ? null
        : {
            id: row.account.account_id,
            displayName: row.account.display_name,
          },
  }));
}

export async function launchSession(input: LaunchInput): Promise<LaunchResult> {
  const result = await execute(() =>
    apiClient.POST('/api/sessions', {
      body: {
        harness: input.harness,
        working_directory: input.workingDirectory,
        initial_text: input.initialText,
        model_id: input.modelId,
        effort: input.effort,
        account_id: input.accountId,
        resume_session_id: input.resumeSessionId,
        attachments: input.attachments.map((attachment) => ({
          local_path: attachment.localPath,
          display_name: attachment.displayName,
          media_type: attachment.mediaType ?? null,
        })),
      },
    }),
  );
  return {
    status: result.status,
    windowId: result.window_id,
    reason: result.reason,
  };
}

export async function saveNewSessionPreferences(
  workingDirectory: string,
  harness: string,
  model: string | null,
  effort: string | null,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/application/new-session-preferences', {
      body: {
        working_directory: workingDirectory,
        harness,
        model,
        effort,
      },
    }),
  );
}

export async function saveNewSessionDraft(
  workingDirectory: string,
  text: string,
  sequence: number,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/application/new-session-drafts', {
      body: {
        working_directory: workingDirectory,
        text,
        sequence,
      },
    }),
  );
}

export async function readLaunchCatalog(
  harness: string,
  workingDirectory: string,
  signal?: AbortSignal,
): Promise<HarnessCatalog> {
  const wire = await execute(() =>
    apiClient.GET('/api/harnesses/{harness}/catalog', {
      params: {
        path: { harness },
        query: { working_directory: workingDirectory },
      },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return {
    commands: wire.commands.map((command) => ({
      command: command.command,
      description: command.description,
      minimumPromptCount: command.minimum_prompt_count,
    })),
    models: wire.models.map((model) => ({
      modelId: model.model_id,
      displayName: model.display_name,
      default: model.default,
      efforts: model.efforts.map((effort) => ({
        value: effort.value,
        displayName: effort.display_name,
        default: effort.default,
      })),
    })),
    rewindModes: wire.rewind_modes.map((mode) => ({
      value: mode.value,
      displayName: mode.display_name,
    })),
  };
}
