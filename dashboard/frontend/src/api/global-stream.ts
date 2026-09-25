import type { GlobalApplication } from '../application/model';
import {
  decodeGlobalApplicationFrame,
  decodeGlobalStreamFrame,
  decodeReadyFrame,
  decodeViewReset,
} from './stream-decoder';
import type { GlobalStreamDelta } from './stream-decoder';

export type GlobalStreamCallbacks = {
  readonly opened: () => void;
  readonly disconnected: () => void;
  readonly delta: (frame: GlobalStreamDelta) => void;
  readonly application: (application: GlobalApplication) => void;
  readonly ready: (bootId: string) => void;
  /** The server closes the stream after this; the client reloads its list. */
  readonly reset: (viewRevision: number) => void;
  readonly invalid: (error: Error) => void;
};

type EventSourceFactory = (url: string) => EventSource;

function eventData(event: Event): unknown {
  return Reflect.get(event, 'data');
}

function messageData(event: Event): string {
  const data = eventData(event);
  if (event instanceof MessageEvent && typeof data === 'string') {
    return data;
  }
  throw new Error('event stream message has no text data');
}

export class GlobalStream {
  readonly source: EventSource;

  constructor(
    position: { readonly cursor: number; readonly viewRevision: number | null },
    callbacks: GlobalStreamCallbacks,
    factory: EventSourceFactory = (url) => new EventSource(url),
  ) {
    const view =
      position.viewRevision === null
        ? ''
        : `&view_revision=${encodeURIComponent(String(position.viewRevision))}`;
    this.source = factory(
      `/sessionData/stream?after_cursor=${encodeURIComponent(String(position.cursor))}${view}`,
    );
    this.source.onopen = callbacks.opened;
    this.source.onerror = callbacks.disconnected;
    this.source.addEventListener('ready', (event) => {
      try {
        callbacks.ready(decodeReadyFrame(messageData(event)));
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
    this.source.addEventListener('reset', (event) => {
      try {
        const viewRevision = decodeViewReset(messageData(event));
        this.source.close();
        callbacks.reset(viewRevision);
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
    this.source.addEventListener('sessionData', (event) => {
      try {
        callbacks.delta(decodeGlobalStreamFrame(messageData(event)));
      } catch (error) {
        callbacks.invalid(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    });
    this.source.addEventListener('application', (event) => {
      try {
        callbacks.application(decodeGlobalApplicationFrame(messageData(event)));
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
