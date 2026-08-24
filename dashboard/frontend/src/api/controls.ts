import type { RequestId, SessionId } from '../app/domain-ids';
import type { AttachmentReference, ControlOutcome } from '../controls/model';
import { HttpFailure, apiClient, messageFrom, request } from './client';
import type { ApiResult } from './client';
import { translateControlOutcome } from './translators/controls';

async function outcome<Data>(
  operation: () => Promise<ApiResult<Data>>,
): Promise<ControlOutcome> {
  const result = await request(operation);
  if (result.response.status >= 400 && result.response.status !== 409) {
    throw new HttpFailure(result.response.status, messageFrom(result.error));
  }
  return translateControlOutcome(result.data ?? result.error);
}

function reference(attachment: AttachmentReference) {
  return {
    local_path: attachment.localPath,
    display_name: attachment.displayName,
    ...(attachment.mediaType === undefined
      ? {}
      : { media_type: attachment.mediaType }),
  };
}

export async function sendText(
  sessionId: SessionId,
  id: RequestId,
  text: string,
  attachments: readonly AttachmentReference[],
  replaceTerminalDraft: boolean,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/send-text', {
      params: { path: { session_id: sessionId } },
      body: {
        request_id: id,
        text,
        attachments: attachments.map(reference),
        replace_terminal_draft: replaceTerminalDraft,
      },
    }),
  );
}

export async function interrupt(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/interrupt', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function background(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/background', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function closeSession(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/close-session', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function renameSession(
  sessionId: SessionId,
  id: RequestId,
  name: string,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/rename-session', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id, name },
    }),
  );
}

export async function autoNameSession(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/auto-name-session', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function openRewind(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/open-rewind', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function applyRewind(
  sessionId: SessionId,
  id: RequestId,
  targetMessageId: string,
  targetText: string,
  newerPromptCount: number,
  mode: string,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/apply-rewind', {
      params: { path: { session_id: sessionId } },
      body: {
        request_id: id,
        target_message_id: targetMessageId,
        target_text: targetText,
        newer_prompt_count: newerPromptCount,
        mode,
      },
    }),
  );
}

export async function compact(
  sessionId: SessionId,
  id: RequestId,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/compact', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id },
    }),
  );
}

export async function selectModel(
  sessionId: SessionId,
  id: RequestId,
  modelId: string,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/select-model', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id, model_id: modelId },
    }),
  );
}

export async function selectEffort(
  sessionId: SessionId,
  id: RequestId,
  effort: string,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/select-effort', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id, effort },
    }),
  );
}

export async function answerQuestion(
  sessionId: SessionId,
  id: RequestId,
  attentionId: string,
  decision: 'answer' | 'discuss',
  answers:
    | readonly {
        readonly selected: readonly string[];
        readonly other: string;
      }[]
    | null,
  discussion: string | null,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/answer-question', {
      params: { path: { session_id: sessionId } },
      body: {
        request_id: id,
        attention_id: attentionId,
        decision,
        answers:
          answers === null
            ? null
            : answers.map((answer) => ({
                selected: [...answer.selected],
                other: answer.other,
              })),
        discussion,
      },
    }),
  );
}

export async function readPlanChoices(
  sessionId: SessionId,
  id: RequestId,
  attentionId: string,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/read-plan-choices', {
      params: { path: { session_id: sessionId } },
      body: { request_id: id, attention_id: attentionId },
    }),
  );
}

export async function decidePlan(
  sessionId: SessionId,
  id: RequestId,
  attentionId: string,
  decision: string,
  feedback: string | null,
): Promise<ControlOutcome> {
  return outcome(() =>
    apiClient.POST('/api/sessions/{session_id}/controls/decide-plan', {
      params: { path: { session_id: sessionId } },
      body: {
        request_id: id,
        attention_id: attentionId,
        decision,
        feedback,
      },
    }),
  );
}
