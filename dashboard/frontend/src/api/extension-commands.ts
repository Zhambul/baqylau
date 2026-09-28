import type { CommandJob, Diagnostic } from '@baqylau/extension-api';

import { apiClient, execute } from './client';
import type { components } from './generated/schema';

type Schemas = components['schemas'];
type JobReply = Schemas['ExtensionJobResponse'];
type DiagnosticReply = Schemas['Diagnostic'];

/** Name one extension and the scope of its commands and jobs. */
export type CommandTarget = {
  readonly owner: string;
  readonly scope: unknown;
};

/** One command submission; the request key makes a retry the same job. */
export type CommandSubmission = {
  readonly commandId: string;
  readonly requestKey: string;
  readonly argumentsJson: string;
  readonly expectedStateRevision: string | null;
};

function diagnostic(reply: DiagnosticReply): Diagnostic {
  return { ...reply, input_id: reply.input_id ?? null };
}

/** Keep what a view reads from a job: its state, result document, or reason. */
function commandJob(reply: JobReply): CommandJob {
  const result = reply.result ?? null;
  const document =
    result !== null && 'document' in result ? result.document : null;
  const reason =
    result !== null && 'diagnostic' in result && result.diagnostic
      ? result.diagnostic
      : (reply.diagnostic ?? null);
  return {
    jobId: reply.job_id,
    state: reply.state,
    document,
    diagnostic: reason === null ? null : diagnostic(reason),
  };
}

/** Submit one declared command; the host accepts the job and runs it later. */
export async function submitExtensionCommand(
  target: CommandTarget,
  submission: CommandSubmission,
  signal: AbortSignal,
): Promise<CommandJob> {
  const reply = await execute(() =>
    apiClient.POST('/api/extensions/{extension_id}/commands/{command_id}', {
      params: {
        path: { extension_id: target.owner, command_id: submission.commandId },
      },
      body: {
        scope: JSON.stringify(target.scope),
        request_key: submission.requestKey,
        arguments: submission.argumentsJson,
        expected_state_revision: submission.expectedStateRevision,
      },
      signal,
    }),
  );
  return commandJob(reply);
}

/** Read one job of an extension in one scope. */
export async function readExtensionJob(
  target: CommandTarget,
  jobId: string,
  signal: AbortSignal,
): Promise<CommandJob> {
  const reply = await execute(() =>
    apiClient.GET('/api/extensions/{extension_id}/jobs/{job_id}', {
      params: {
        path: { extension_id: target.owner, job_id: jobId },
        query: { scope: JSON.stringify(target.scope) },
      },
      signal,
    }),
  );
  return commandJob(reply);
}
