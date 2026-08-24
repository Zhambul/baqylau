import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { requestDictationGrant } from '../api/dictation';
import { sessionId } from '../app/domain-ids';
import { DictationController } from './dictation-controller.svelte';

vi.mock('../api/dictation', () => ({
  requestDictationGrant: vi.fn(),
}));

const requestGrantMock = vi.mocked(requestDictationGrant);
const stopTrack = vi.fn();

class FakeAudioWorkletNode {
  static latest: FakeAudioWorkletNode | null = null;
  readonly port: {
    onmessage: ((event: { readonly data: unknown }) => void) | null;
  } = { onmessage: null };
  readonly connect = vi.fn();

  constructor() {
    FakeAudioWorkletNode.latest = this;
  }

  audio(buffer: ArrayBuffer): void {
    this.port.onmessage?.({ data: buffer });
  }
}

class FakeAudioContext {
  readonly sampleRate = 48_000;
  state = 'running';
  readonly destination = {};
  readonly audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  readonly sourceConnect = vi.fn();
  readonly resume = vi.fn().mockResolvedValue(undefined);
  readonly close = vi.fn(() => {
    this.state = 'closed';
    return Promise.resolve();
  });

  createMediaStreamSource() {
    return { connect: this.sourceConnect };
  }
}

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static latest: FakeWebSocket | null = null;

  readyState = FakeWebSocket.CONNECTING;
  bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { readonly data: unknown }) => void) | null = null;
  readonly sent: unknown[] = [];
  readonly url: string;
  readonly protocols: readonly string[];

  constructor(url: string, protocols: readonly string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.latest = this;
  }

  send(value: unknown): void {
    this.sent.push(value);
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  message(value: unknown): void {
    this.onmessage?.({ data: value });
  }

  close(): void {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
  }

  finish(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}

describe('DictationController', () => {
  beforeEach(() => {
    FakeAudioWorkletNode.latest = null;
    FakeWebSocket.latest = null;
    stopTrack.mockClear();
    requestGrantMock.mockResolvedValue({
      token: 'short-lived-token',
      expiresIn: 30,
      websocketUrl: 'wss://dictation.test/listen',
    });
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode);
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:dictation-worklet'),
    });
    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: stopTrack }],
        }),
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('captures before the socket opens and flushes preroll on open', async () => {
    const failures = vi.fn();
    const controller = new DictationController(failures);
    const textarea = document.createElement('textarea');

    await controller.start({
      textarea,
      harness: 'claude-code',
      workingDirectory: '/project',
      sessionId: sessionId('session-1'),
    });
    expect(controller.state).toBe('capturing');

    const audio = FakeAudioWorkletNode.latest;
    const socket = FakeWebSocket.latest;
    expect(audio).not.toBeNull();
    expect(socket).not.toBeNull();
    audio?.audio(new ArrayBuffer(320));
    socket?.open();

    expect(controller.state).toBe('streaming');
    expect(socket?.sent[0]).toBeInstanceOf(ArrayBuffer);
    expect(failures).not.toHaveBeenCalled();

    controller.stop();
    expect(socket?.sent).toContain('{"type":"CloseStream"}');
    socket?.finish();
    expect(controller.state).toBe('idle');
    expect(stopTrack).toHaveBeenCalledOnce();
  });

  it('splices interim and final transcripts at the original caret', async () => {
    const controller = new DictationController(vi.fn());
    const textarea = document.createElement('textarea');
    textarea.value = 'start end';
    textarea.setSelectionRange(6, 6);
    const input = vi.fn();
    textarea.addEventListener('input', input);

    await controller.start({
      textarea,
      harness: 'claude-code',
      workingDirectory: '/project',
      sessionId: null,
    });
    const socket = FakeWebSocket.latest;
    socket?.open();
    socket?.message(
      JSON.stringify({
        type: 'Results',
        is_final: false,
        start: 0,
        duration: 0.5,
        channel: { alternatives: [{ transcript: 'hello' }] },
      }),
    );
    expect(textarea.value).toBe('start helloend');

    socket?.message(
      JSON.stringify({
        type: 'Results',
        is_final: true,
        start: 0,
        duration: 1,
        channel: { alternatives: [{ transcript: 'hello world' }] },
      }),
    );
    expect(textarea.value).toBe('start hello world end');
    expect(input).toHaveBeenCalledTimes(2);

    controller.stop();
    socket?.finish();
  });

  it('keeps captured speech when stop happens before socket open', async () => {
    const controller = new DictationController(vi.fn());
    const textarea = document.createElement('textarea');

    await controller.start({
      textarea,
      harness: 'claude-code',
      workingDirectory: '/project',
      sessionId: null,
    });
    FakeAudioWorkletNode.latest?.audio(new ArrayBuffer(320));
    controller.stop();
    expect(controller.state).toBe('stopping');

    const socket = FakeWebSocket.latest;
    socket?.open();
    expect(socket?.sent[0]).toBeInstanceOf(ArrayBuffer);
    expect(socket?.sent[1]).toBe('{"type":"CloseStream"}');
    socket?.finish();
    expect(controller.state).toBe('idle');
  });
});
