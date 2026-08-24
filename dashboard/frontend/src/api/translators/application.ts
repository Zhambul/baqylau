import { sessionId } from '../../app/domain-ids';
import type { GlobalApplication, UsageRow } from '../../application/model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];

function translateUsageRow(wire: Schemas['UsageRowResponse']): UsageRow {
  return {
    harness: wire.harness,
    accountId: wire.account_id,
    displayName: wire.display_name,
    switchable: wire.switchable,
    plan: wire.plan,
    windows: wire.windows.map((window) => ({
      key: window.key,
      label: window.label,
      usedPercent: window.used_percent,
      resetsAt: window.resets_at,
      durationMinutes: window.duration_minutes,
      scope: window.scope,
      modelId: window.model_id,
    })),
    schedulingScore: wire.scheduling_score,
    schedulingAllowed: wire.scheduling_allowed,
    limit:
      wire.limit === null
        ? null
        : {
            modelId: wire.limit.model_id,
            message: wire.limit.message,
            resetsAt: wire.limit.resets_at,
          },
    authenticationError: wire.authentication_error,
  };
}

export function translateGlobalApplication(
  wire: Schemas['GlobalApplicationResponse'],
): GlobalApplication {
  const latest = wire.notifications.latest;
  return {
    usageRows: wire.usage_rows.map(translateUsageRow),
    notifications: {
      enabled: wire.notifications.enabled,
      latest:
        latest === null
          ? null
          : {
              revision: latest.revision,
              sessionId: sessionId(latest.session_id),
              kind: latest.kind,
              project: latest.project,
              title: latest.title,
            },
    },
    preferences: {
      newSession: {
        workingDirectory: wire.preferences.new_session.working_directory,
        harness: wire.preferences.new_session.harness,
        model: wire.preferences.new_session.model,
        effort: wire.preferences.new_session.effort,
      },
      newSessionDrafts: wire.preferences.new_session_drafts.map((draft) => ({
        workingDirectory: draft.working_directory,
        text: draft.text,
        sequence: draft.sequence,
      })),
      hiddenDirectories: new Map(
        Object.entries(wire.preferences.hidden_directories),
      ),
      limits: {
        uploadBytes: wire.preferences.limits.upload_bytes,
        renameCharacters: wire.preferences.limits.rename_characters,
        presenceSeconds: wire.preferences.limits.presence_seconds,
      },
    },
  };
}
