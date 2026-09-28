import type { SessionId } from '../app/domain-ids';
import type { SessionApplication } from '../application/session-model';
import {
  decodeSessionApplicationFrame,
  decodeSessionStreamFrame,
  decodeViewReset,
} from './stream-decoder';
import type { SessionStreamDelta } from './stream-decoder';

export type SessionStreamCallbacks = {
  readonly opened: () => void;
  readonly disconnected: () => void;
  readonly delta: (frame: SessionStreamDelta, cursor: number) => void;
  readonly application: (application: SessionApplication) => void;
  /** The server closes the stream after this; the client reloads its view. */
  readonly reset: (viewRevision: number) => void;
  readonly invalid: (error: Error) => void;
};

type EventSourceFactory = (url: string) => EventSource;

function eventValue(event: Event, name: string): unknown {
  return Reflect.get(event, name);
}

function messageText(event: Event): string {
  const data = eventValue(event, 'data');
  if (event instanceof MessageEvent && typeof data === 'string') {
    return data;
  }
  throw new Error('event stream message has no text data');
}

function eventCursor(event: Event): number {
  const value = eventValue(event, 'lastEventId');
  if (typeof value !== 'string' || !/^\d+$/.test(value)) {
    throw new Error('event stream message has no valid cursor');
  }
  const cursor = Number(value);
  if (!Number.isSafeInteger(cursor)) {
    throw new Error('event stream cursor is outside the safe integer range');
  }
  return cursor;
}

export class SessionStream {
  readonly source: EventSource;

  constructor(
    sessionId: SessionId,
    position: {
      readonly cursor: number;
      readonly viewRevision: number | null;
      /** The highest entry row that the view has; a projection commits some entries late. */
      readonly entryCursor?: number | null;
    },
    callbacks: SessionStreamCallbacks,
    factory: EventSourceFactory = (url) => new EventSource(url),
  ) {
    const view =
      position.viewRevision === null
        ? ''
        : `&view_revision=${encodeURIComponent(String(position.viewRevision))}`;
    const entries =
      position.entryCursor === undefined || position.entryCursor === null
        ? ''
        : `&after_entry=${encodeURIComponent(String(position.entryCursor))}`;
    this.source = factory(
      `/sessionData/${encodeURIComponent(sessionId)}/stream?after_cursor=${encodeURIComponent(String(position.cursor))}${view}${entries}`,
    );
    this.source.onopen = callbacks.opened;
    this.source.onerror = callbacks.disconnected;
    this.source.addEventListener('sessionData', (event) => {
      try {
        callbacks.delta(
          decodeSessionStreamFrame(messageText(event)),
          eventCursor(event),
        );
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
    this.source.addEventListener('reset', (event) => {
      try {
        const viewRevision = decodeViewReset(messageText(event));
        this.source.close();
        callbacks.reset(viewRevision);
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
    this.source.addEventListener('application', (event) => {
      try {
        callbacks.application(
          decodeSessionApplicationFrame(messageText(event)),
        );
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
  }

  close(): void {
    this.source.close();
  }
}
