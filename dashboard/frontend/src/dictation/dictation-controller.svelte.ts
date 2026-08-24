import { requestDictationGrant } from '../api/dictation';
import type { SessionId } from '../app/domain-ids';
import { DICTATION_RATE, DICTATION_WORKLET } from './audio-worklet';

export type DictationState =
  'idle' | 'starting' | 'capturing' | 'streaming' | 'stopping' | 'failed';

export type DictationTelemetry = (
  name: string,
  details: Readonly<Record<string, string | number | boolean | null>>,
) => void;

export type DictationStart = {
  readonly textarea: HTMLTextAreaElement;
  readonly harness: string;
  readonly workingDirectory: string;
  readonly sessionId: SessionId | null;
};

type DeepgramResult = {
  readonly final: boolean;
  readonly transcript: string;
  readonly processedSeconds: number | null;
};

type DictationRun = {
  readonly startedAt: number;
  readonly outputRate: number;
  readonly bytesPerSecond: number;
  readonly textarea: HTMLTextAreaElement;
  readonly sessionId: SessionId | null;
  readonly audioContext: AudioContext;
  readonly grantAbort: AbortController;
  readonly preroll: ArrayBuffer[];
  readonly onEdit: () => void;
  stream: MediaStream | null;
  socket: WebSocket | null;
  heldBytes: number;
  sentSamples: number;
  processedSeconds: number;
  maximumQueueSeconds: number;
  maximumServiceSeconds: number;
  warned: boolean;
  armedMilliseconds: number;
  openedMilliseconds: number;
  prefix: string;
  suffix: string;
  committed: string;
  interim: string;
  skipFinal: boolean;
  painting: boolean;
  stopping: boolean;
  closed: boolean;
  closeOnOpen: boolean;
  lastPainted: string | null;
  lagTimer: ReturnType<typeof setInterval> | null;
  flushTimer: ReturnType<typeof setTimeout> | null;
  stopTimer: ReturnType<typeof setTimeout> | null;
};

const LAG_INTERVAL_MS = 5_000;
const BACKLOG_WARNING_SECONDS = 3;
const PREROLL_MAX_SECONDS = 60;
const FLUSH_TIMEOUT_MS = 2_000;
const STOP_GRACE_MS = 6_000;

let workletUrl: string | null = null;
let activeController: DictationController | null = null;

export class DictationController {
  state = $state<DictationState>('idle');
  failure = $state<string | null>(null);
  private run: DictationRun | null = null;

  constructor(
    private readonly reportFailure: (message: string) => void,
    private readonly telemetry: DictationTelemetry = () => undefined,
  ) {}

  async start(input: DictationStart): Promise<void> {
    if (this.run !== null || this.state === 'starting') return;
    activeController?.stop();
    claimActiveController(this);
    this.state = 'starting';
    this.failure = null;

    let audioContext: AudioContext;
    try {
      audioContext = new AudioContext();
    } catch {
      this.failWithoutRun('dictation needs Web Audio support in this browser');
      return;
    }
    if (audioContext.state === 'suspended') void audioContext.resume();
    const outputRate = Math.min(
      DICTATION_RATE,
      Math.round(audioContext.sampleRate),
    );
    const position = input.textarea.selectionStart;
    const grantAbort = new AbortController();
    const run: DictationRun = {
      startedAt: performance.now(),
      outputRate,
      bytesPerSecond: outputRate * 2,
      textarea: input.textarea,
      sessionId: input.sessionId,
      audioContext,
      grantAbort,
      preroll: [],
      onEdit: () => {
        this.reanchor(run);
      },
      stream: null,
      socket: null,
      heldBytes: 0,
      sentSamples: 0,
      processedSeconds: 0,
      maximumQueueSeconds: 0,
      maximumServiceSeconds: 0,
      warned: false,
      armedMilliseconds: 0,
      openedMilliseconds: 0,
      prefix: input.textarea.value.slice(0, position),
      suffix: input.textarea.value.slice(position),
      committed: '',
      interim: '',
      skipFinal: false,
      painting: false,
      stopping: false,
      closed: false,
      closeOnOpen: false,
      lastPainted: null,
      lagTimer: null,
      flushTimer: null,
      stopTimer: null,
    };
    this.run = run;

    workletUrl ??= URL.createObjectURL(
      new Blob([DICTATION_WORKLET], { type: 'text/javascript' }),
    );

    const microphone = navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    const moduleLoad = audioContext.audioWorklet.addModule(workletUrl);
    const grant = requestDictationGrant(
      outputRate,
      input.harness,
      input.workingDirectory,
      grantAbort.signal,
    );
    // The microphone permission can outlive either network leg. Mark every
    // independent promise observed while the capture path waits for the pair.
    void microphone.catch(() => undefined);
    void moduleLoad.catch(() => undefined);
    void grant.catch(() => undefined);

    try {
      const [stream] = await Promise.all([
        microphone,
        moduleLoad.then(() => undefined),
      ]);
      if (run.closed) {
        stopTracks(stream);
        return;
      }
      run.stream = stream;
      const source = audioContext.createMediaStreamSource(stream);
      const sink = new AudioWorkletNode(audioContext, 'dictate-pcm', {
        processorOptions: { outRate: outputRate },
      });
      sink.port.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data instanceof ArrayBuffer) this.pump(run, event.data);
      };
      source.connect(sink);
      sink.connect(audioContext.destination);
    } catch {
      this.finish(run, 'failed', 'microphone or audio pipeline failed');
      return;
    }

    run.armedMilliseconds = elapsed(run);
    input.textarea.addEventListener('input', run.onEdit);
    this.state = 'capturing';

    let token: Awaited<typeof grant>;
    try {
      token = await grant;
    } catch {
      if (!isClosed(run))
        this.finish(run, 'failed', 'dictation token could not be created');
      return;
    }
    if (isClosed(run)) return;

    let socket: WebSocket;
    try {
      socket = new WebSocket(token.websocketUrl, ['bearer', token.token]);
    } catch {
      this.finish(run, 'failed', 'could not reach the dictation service');
      return;
    }
    run.socket = socket;
    socket.onmessage = (event: MessageEvent<unknown>) => {
      const result = deepgramResult(event.data);
      if (result !== null) this.receive(run, result);
    };
    socket.onopen = () => {
      this.open(run);
    };
    socket.onclose = () => {
      const dropped = !run.stopping && !run.closed;
      this.finish(
        run,
        dropped ? 'failed' : 'idle',
        dropped ? 'connection to the dictation service closed' : null,
      );
    };
  }

  stop(): void {
    const run = this.run;
    if (run === null || run.stopping || run.closed) return;
    run.stopping = true;
    this.state = 'stopping';
    if (run.socket?.readyState === WebSocket.OPEN) {
      this.closeStream(run);
      return;
    }
    if (run.preroll.length === 0) {
      this.finish(run, 'idle', null);
      return;
    }
    run.closeOnOpen = true;
    run.stopTimer = setTimeout(() => {
      this.finish(run, 'idle', null);
    }, STOP_GRACE_MS);
  }

  private pump(run: DictationRun, buffer: ArrayBuffer): void {
    if (run.stopping || run.closed) return;
    if (run.socket?.readyState === WebSocket.OPEN) {
      this.send(run, buffer);
      return;
    }
    run.preroll.push(buffer);
    run.heldBytes += buffer.byteLength;
    const maximum = PREROLL_MAX_SECONDS * run.bytesPerSecond;
    while (run.heldBytes > maximum) {
      const discarded = run.preroll.shift();
      if (discarded === undefined) break;
      run.heldBytes -= discarded.byteLength;
    }
  }

  private send(run: DictationRun, buffer: ArrayBuffer): void {
    run.socket?.send(buffer);
    run.sentSamples += buffer.byteLength / 2;
  }

  private open(run: DictationRun): void {
    if (run.closed) {
      run.socket?.close();
      return;
    }
    const prerollSeconds = run.heldBytes / run.bytesPerSecond;
    for (const buffer of run.preroll) this.send(run, buffer);
    run.preroll.length = 0;
    run.heldBytes = 0;
    run.openedMilliseconds = elapsed(run);
    this.state = run.stopping ? 'stopping' : 'streaming';
    this.telemetry('dictate.start', {
      rate: run.outputRate,
      native: Math.round(run.audioContext.sampleRate),
      arm_ms: run.armedMilliseconds,
      open_ms: run.openedMilliseconds,
      preroll_s: rounded(prerollSeconds, 2),
    });
    if (run.closeOnOpen) {
      this.closeStream(run);
      return;
    }
    run.lagTimer = setInterval(() => {
      this.measureLag(run);
    }, LAG_INTERVAL_MS);
  }

  private receive(run: DictationRun, result: DeepgramResult): void {
    if (result.processedSeconds !== null)
      run.processedSeconds = result.processedSeconds;
    if (result.final) {
      if (run.skipFinal) run.skipFinal = false;
      else if (result.transcript.length > 0)
        run.committed += `${result.transcript} `;
      run.interim = '';
    } else if (!run.skipFinal) {
      run.interim = result.transcript;
    }
    this.paint(run);
  }

  private paint(run: DictationRun): void {
    if (
      run.stopping &&
      run.lastPainted !== null &&
      run.textarea.value !== run.lastPainted
    )
      return;
    const head = run.prefix + run.committed + run.interim;
    run.painting = true;
    run.lastPainted = head + run.suffix;
    run.textarea.value = run.lastPainted;
    run.textarea.setSelectionRange(head.length, head.length);
    run.textarea.dispatchEvent(new Event('input', { bubbles: true }));
    run.painting = false;
  }

  private reanchor(run: DictationRun): void {
    if (run.painting) return;
    const position = run.textarea.selectionStart;
    run.skipFinal = run.interim.length > 0;
    run.prefix = run.textarea.value.slice(0, position);
    run.suffix = run.textarea.value.slice(position);
    run.committed = '';
    run.interim = '';
  }

  private closeStream(run: DictationRun): void {
    try {
      run.socket?.send('{"type":"CloseStream"}');
    } catch {
      this.finish(run, 'idle', null);
      return;
    }
    run.flushTimer = setTimeout(() => {
      run.socket?.close();
      this.finish(run, 'idle', null);
    }, FLUSH_TIMEOUT_MS);
  }

  private measureLag(run: DictationRun): void {
    if (run.closed) return;
    const queueSeconds =
      run.socket?.readyState === WebSocket.OPEN
        ? run.socket.bufferedAmount / run.bytesPerSecond
        : 0;
    const sentSeconds = run.sentSamples / run.outputRate;
    const serviceSeconds = Math.max(
      0,
      sentSeconds - queueSeconds - run.processedSeconds,
    );
    run.maximumQueueSeconds = Math.max(run.maximumQueueSeconds, queueSeconds);
    run.maximumServiceSeconds = Math.max(
      run.maximumServiceSeconds,
      serviceSeconds,
    );
    this.telemetry('dictate.lag', {
      queue_s: rounded(queueSeconds, 2),
      svc_s: rounded(serviceSeconds, 2),
      sent_s: rounded(sentSeconds, 1),
      buffered: run.socket?.bufferedAmount ?? 0,
    });
    if (!run.warned && queueSeconds > BACKLOG_WARNING_SECONDS) {
      run.warned = true;
      this.telemetry('dictate.backlog', {
        queue_s: rounded(queueSeconds, 2),
      });
      this.reportFailure('dictation is lagging behind your voice');
    }
  }

  private finish(
    run: DictationRun,
    finalState: 'idle' | 'failed',
    message: string | null,
  ): void {
    if (run.closed) return;
    run.closed = true;
    run.grantAbort.abort();
    if (run.lagTimer !== null) clearInterval(run.lagTimer);
    if (run.flushTimer !== null) clearTimeout(run.flushTimer);
    if (run.stopTimer !== null) clearTimeout(run.stopTimer);
    stopTracks(run.stream);
    if (run.audioContext.state !== 'closed') void run.audioContext.close();
    try {
      run.socket?.close();
    } catch {
      // Closing a socket that is already closing is harmless.
    }
    if (
      run.interim.length > 0 &&
      run.lastPainted !== null &&
      run.textarea.value === run.lastPainted
    ) {
      run.committed += run.interim;
      run.interim = '';
      this.paint(run);
    }
    run.textarea.removeEventListener('input', run.onEdit);
    this.telemetry('dictate.stop', {
      rate: run.outputRate,
      spoke_s: rounded(run.sentSamples / run.outputRate, 1),
      max_queue_s: rounded(run.maximumQueueSeconds, 2),
      max_svc_s: rounded(run.maximumServiceSeconds, 2),
      arm_ms: run.armedMilliseconds,
      open_ms: run.openedMilliseconds,
    });
    if (this.run === run) this.run = null;
    if (activeController === this) activeController = null;
    this.state = finalState;
    if (message !== null) {
      this.failure = message;
      this.reportFailure(message);
    }
  }

  private failWithoutRun(message: string): void {
    if (activeController === this) activeController = null;
    this.state = 'failed';
    this.failure = message;
    this.reportFailure(message);
  }
}

function stopTracks(stream: MediaStream | null): void {
  for (const track of stream?.getTracks() ?? []) track.stop();
}

function claimActiveController(controller: DictationController): void {
  activeController = controller;
}

function isClosed(run: DictationRun): boolean {
  return run.closed;
}

function elapsed(run: DictationRun): number {
  return Math.round(performance.now() - run.startedAt);
}

function rounded(value: number, digits: number): number {
  return Number(value.toFixed(digits));
}

function deepgramResult(value: unknown): DeepgramResult | null {
  if (typeof value !== 'string') return null;
  let document: unknown;
  try {
    document = JSON.parse(value);
  } catch {
    return null;
  }
  if (!record(document) || document.type !== 'Results') return null;
  const channel = document.channel;
  if (!record(channel) || !Array.isArray(channel.alternatives)) return null;
  const alternative: unknown = channel.alternatives[0];
  const transcript =
    record(alternative) && typeof alternative.transcript === 'string'
      ? alternative.transcript
      : '';
  const processedSeconds =
    typeof document.start === 'number' && typeof document.duration === 'number'
      ? document.start + document.duration
      : null;
  return {
    final: document.is_final === true,
    transcript,
    processedSeconds,
  };
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
