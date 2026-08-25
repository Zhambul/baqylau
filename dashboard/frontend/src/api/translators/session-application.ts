import { clientId } from '../../app/domain-ids';
import type { SessionApplication } from '../../application/session-model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];

export function translateSessionApplication(
  wire: Schemas['SessionApplicationResponse'],
): SessionApplication {
  return {
    preferences: {
      viewMode: wire.preferences.view_mode,
      notificationsMuted: wire.preferences.notifications_muted,
      tasksHidden: wire.preferences.tasks_hidden,
    },
    composer: {
      draft:
        wire.composer.draft === null
          ? null
          : {
              text: wire.composer.draft.text,
              origin: clientId(wire.composer.draft.origin),
              sequence: wire.composer.draft.sequence,
            },
      queue:
        wire.composer.queue === null
          ? null
          : {
              items: wire.composer.queue.items.map((item) => ({
                requestId: item.request_id,
                text: item.text,
              })),
              origin: clientId(wire.composer.queue.origin),
            },
    },
    dialog: {
      draft:
        wire.dialog.draft === null
          ? null
          : {
              attentionId: wire.dialog.draft.attention_id,
              answers: wire.dialog.draft.answers.map((answer) => ({
                selected: answer.selected,
                other: answer.other,
              })),
              origin: clientId(wire.dialog.draft.origin),
            },
    },
    terminal: {
      windowId: wire.terminal.window_id,
      inputState:
        wire.terminal.input_state === null
          ? null
          : {
              typedText: wire.terminal.input_state.typed_text,
              suggestion: wire.terminal.input_state.suggestion,
            },
    },
    errors: wire.errors.map((error) => ({
      errorId: error.error_id,
      timestamp: error.timestamp,
      component: error.component,
      action: error.action,
      traceback: error.traceback,
      context: error.context,
    })),
  };
}
