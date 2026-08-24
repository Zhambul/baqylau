import type { ActorId, EntryId } from '../app/domain-ids';

export type Content = {
  readonly text: string;
  readonly mediaType: 'text/plain' | 'text/markdown';
};

export type Question = {
  readonly questionId: string;
  readonly title: string | null;
  readonly question: string;
  readonly multiple: boolean;
  readonly choices: readonly {
    readonly label: string;
    readonly description: string | null;
  }[];
};

type EntryEnvelope = {
  readonly entryId: EntryId;
  readonly cursor: number;
  readonly actorId: ActorId;
  readonly parentActorId: ActorId | null;
  readonly turnId: string | null;
  readonly occurredAt: number;
  readonly summary: string | null;
};

export type EntryContent =
  | { readonly type: 'turn_started'; readonly body: Readonly<object> }
  | {
      readonly type: 'turn_finished';
      readonly body: { readonly state: 'finished' | 'aborted' };
    }
  | {
      readonly type: 'message';
      readonly body: {
        readonly messageId: string;
        readonly role: 'user' | 'assistant' | 'system' | 'peer' | 'parent';
        readonly phase:
          'prompt' | 'intermediate' | 'end_turn' | 'synthetic' | 'recap' | null;
        readonly content: Content;
        readonly recipientActorId: ActorId | null;
        readonly replyTo: string | null;
      };
    }
  | {
      readonly type: 'reasoning';
      readonly body: {
        readonly reasoningId: string;
        readonly content: Content;
      };
    }
  | {
      readonly type: 'shell_started';
      readonly body: {
        readonly shellId: string;
        readonly command: Content;
        readonly execution: 'foreground' | 'background' | 'monitor';
      };
    }
  | {
      readonly type: 'shell_output';
      readonly body: {
        readonly shellId: string;
        readonly stream: 'output' | 'error' | 'status';
        readonly mode: 'append' | 'replace';
        readonly content: Content;
      };
    }
  | {
      readonly type: 'shell_backgrounded';
      readonly body: { readonly shellId: string };
    }
  | {
      readonly type: 'shell_finished';
      readonly body: {
        readonly shellId: string;
        readonly state: 'succeeded' | 'failed' | 'cancelled';
        readonly exitCode: number | null;
        readonly result: Content | null;
      };
    }
  | {
      readonly type: 'file';
      readonly body: {
        readonly path: string;
        readonly action: 'read' | 'created' | 'updated' | 'deleted' | 'renamed';
        readonly state: 'succeeded' | 'failed';
        readonly previousPath: string | null;
        readonly linesAdded: number | null;
        readonly linesRemoved: number | null;
        readonly content: Content | null;
      };
    }
  | {
      readonly type: 'search';
      readonly body: {
        readonly tool: string;
        readonly query: Content;
        readonly state: 'succeeded' | 'failed';
        readonly result: Content | null;
      };
    }
  | {
      readonly type: 'web';
      readonly body: {
        readonly url: string | null;
        readonly state: 'succeeded' | 'failed';
        readonly result: Content | null;
      };
    }
  | {
      readonly type: 'worktree';
      readonly body: {
        readonly action: 'entered' | 'exited';
        readonly state: 'succeeded' | 'failed';
        readonly arguments: Content | null;
      };
    }
  | {
      readonly type: 'skill_started';
      readonly body: {
        readonly skillId: string;
        readonly name: string;
        readonly arguments: Content | null;
      };
    }
  | {
      readonly type: 'skill_finished';
      readonly body: {
        readonly skillId: string;
        readonly state: 'succeeded' | 'failed' | 'cancelled';
        readonly result: Content | null;
      };
    }
  | {
      readonly type: 'question_asked';
      readonly body: {
        readonly attentionId: string;
        readonly questions: readonly Question[];
      };
    }
  | {
      readonly type: 'question_answered';
      readonly body: {
        readonly attentionId: string;
        readonly answers: readonly {
          readonly questionId: string;
          readonly labels: readonly string[];
        }[];
        readonly feedback: string | null;
      };
    }
  | {
      readonly type: 'plan_proposed';
      readonly body: { readonly attentionId: string; readonly plan: Content };
    }
  | {
      readonly type: 'plan_resolved';
      readonly body: {
        readonly attentionId: string;
        readonly state: 'approved' | 'changes_requested' | 'rejected';
        readonly feedback: string | null;
        readonly edited: boolean;
      };
    }
  | {
      readonly type: 'compaction_started';
      readonly body: { readonly beforeTokens: number | null };
    }
  | {
      readonly type: 'compaction_finished';
      readonly body: {
        readonly beforeTokens: number | null;
        readonly afterTokens: number | null;
      };
    }
  | {
      readonly type: 'assignment_started';
      readonly body: {
        readonly assignmentId: string;
        readonly assignedActorName: string | null;
        readonly prompt: Content | null;
      };
    }
  | {
      readonly type: 'assignment_finished';
      readonly body: {
        readonly assignmentId: string;
        readonly state: 'succeeded' | 'failed' | 'cancelled';
        readonly result: Content | null;
      };
    }
  | {
      readonly type: 'model_change';
      readonly body: {
        readonly current: string;
        readonly previous: string | null;
        readonly automatic: boolean;
      };
    }
  | {
      readonly type: 'effort_change';
      readonly body: {
        readonly current: string;
        readonly previous: string | null;
      };
    };

export type Entry = EntryEnvelope & EntryContent;

export type EntryPage = {
  readonly items: readonly Entry[];
  readonly oldestCursor: number;
  readonly hasMore: boolean;
};
