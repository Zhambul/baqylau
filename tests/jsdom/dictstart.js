// tests/jsdom/dictstart.js — drives the REAL dictation() startup state machine
// (dashboard/static/app.08-composer.js, over the constants in app.07-dialogs.js)
// and prints one JSON verdict object, which tests/test_l0_dash_probes.py
// asserts on.
//
// Why this exists: "the mic takes a long time to be ready" was fixed by making
// capture start BEFORE the socket exists and holding the audio in a preroll —
// which turns a straight-line async function into a small state machine with
// four orderings that only ever occur against a slow network. A grep sees none
// of them, and every one of them silently loses speech when it breaks:
//   A: audio captured before the socket opens must be HELD, then flushed IN
//      ORDER once it does — a preroll that flushed backwards, or dropped what
//      it held, would look exactly like a working mic that mis-transcribes.
//   B: stop() BEFORE the socket opens must still deliver — the words are
//      already in the preroll, and this is the common case for a short
//      dictation over a slow link (press, say six words, press stop).
//   C: stop() with nothing said must NOT open a connection to say nothing.
//   D: a denied mic must leave the textarea and the button exactly as found,
//      and must let the next press retry (the `starting` re-entrancy latch).
//
// The audio pipeline is shimmed to the port boundary on purpose: what the
// worklet DOES with samples is tests/jsdom/dictpcm.js's job, and what this
// harness needs is control over WHEN a chunk appears relative to the socket.
//
// Usage: node tests/jsdom/dictstart.js dashboard/static/app.07-dialogs.js \
//                                       dashboard/static/app.08-composer.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El } = require("./domshim");

const out = { errors: [] };
const ck = (name, got, want) => {
  // SNAPSHOT: the recorded arrays are live socket buffers that keep growing
  // after the check, and a verdict that reports later state is a verdict that
  // lies about the moment it tested
  out[name] = got === undefined ? null : JSON.parse(JSON.stringify(got));
  if (want !== undefined && JSON.stringify(got) !== JSON.stringify(want))
    out.errors.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
};
const defer = () => {
  let res, rej;
  const p = new Promise((a, b) => { res = a; rej = b; });
  p.resolve = res; p.reject = rej;
  p.catch(() => {});          // the source attaches its own; keep node quiet
  return p;
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const tick = () => sleep(5);   // let the awaited microtasks drain

/* ---------- the harness-controlled browser surface ------------------------ */
const H = { sockets: [], nodes: [], toasts: [], clogs: [], tokens: [] };

class FakeSocket {
  constructor(url, protos) {
    this.url = url; this.protos = protos;
    this.readyState = 0;                 // CONNECTING
    this.sent = []; this.bufferedAmount = 0;
    this.onopen = this.onclose = this.onmessage = null;
    H.sockets.push(this);
  }
  send(d) {
    if (this.readyState !== 1) throw new Error("send on non-open socket");
    // a control frame is a string, audio is an ArrayBuffer — record what each
    // actually was, since the ORDER of the two is the thing under test
    this.sent.push(typeof d === "string" ? d : `pcm:${d.byteLength}`);
  }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  open() { this.readyState = 1; if (this.onopen) this.onopen(); }
}

class FakeWorkletNode {
  constructor(ctx, name, opts) {
    this.name = name; this.opts = opts;
    this.port = { onmessage: null };
    H.nodes.push(this);
  }
  connect() {}
  // the audio thread handing the main thread one chunk of Int16 PCM
  emit(bytes) { this.port.onmessage({ data: new ArrayBuffer(bytes) }); }
}

function makeCtx() {
  return class FakeAudioContext {
    constructor() {
      this.state = "running";
      this.sampleRate = 48000;
      this.audioWorklet = { addModule: () => H.mod };
    }
    createMediaStreamSource() { return { connect() {} }; }
    resume() {}
    close() { this.state = "closed"; }
  };
}

const track = { stop() { track.stopped = true; } };
const stream = { getTracks: () => [track] };

function textarea() {
  const ta = new El("textarea");
  ta.value = ""; ta.disabled = false; ta.selectionStart = 0;
  ta.setSelectionRange = () => {};
  ta.dispatchEvent = (e) => { for (const f of ta._on[e.type] || []) f(e); };
  return ta;
}

const sandbox = {
  console, Math, JSON, Date, Object, Array, Number, String, Boolean,
  Promise, Set, Map, RegExp, ArrayBuffer, Float32Array, Int16Array,
  encodeURIComponent, decodeURIComponent, parseInt, isNaN,
  setTimeout, clearTimeout, setInterval, clearInterval,
  performance: { now: () => Date.now() },
  Event: class { constructor(t) { this.type = t; } },
  Blob: class { constructor(parts) { this.parts = parts; } },
  URL: { createObjectURL: () => "blob:worklet" },
  WebSocket: FakeSocket,
  AudioWorkletNode: FakeWorkletNode,
  navigator: { mediaDevices: { getUserMedia: () => H.mic } },
  document: { createElementNS: () => ({ setAttribute() {}, append() {} }) },
  addEventListener: () => {},
  fetch: () => new Promise(() => {}),
  // app globals the composer reaches for, each shimmed to the smallest thing
  // that keeps the startup path honest
  el: (tag, cls, text) => new El(tag, cls, text),
  toast: (kind, title, detail) => H.toasts.push(`${title}|${detail}`),
  clog: (sessionId, ev, data) => H.clogs.push({ sessionId, ev, data }),
  postJSON: (path, body) => { H.tokens.push({ path, body }); return H.tok; },
  IS_IPAD: false,
};
sandbox.AudioContext = makeCtx();
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(f, "utf8"), sandbox);
// dictAvailable() probes the server; the button's visibility is not the subject
vm.runInContext("dictProbe = Promise.resolve(true);", sandbox);

/* ---------- a fresh, fully-armed dictation ------------------------------- */
async function arm() {
  H.sockets.length = 0; H.nodes.length = 0;
  H.toasts.length = 0; H.clogs.length = 0; H.tokens.length = 0;
  H.mic = defer(); H.mod = defer(); H.tok = defer();
  const ta = textarea();
  const dic = vm.runInContext("dictation", sandbox)(
    ta,
    () => "/tmp/proj",
    () => "claude_code",
    "s1",
  );
  dic.btn.onclick();                       // press
  H.mic.resolve(stream);
  H.mod.resolve();
  await tick();
  return { ta, dic, node: H.nodes[0] };
}

(async () => {
  /* ---- A: capture precedes the socket; the preroll flushes in order ------ */
  {
    const { ta, dic, node } = await arm();
    // ARMED: capturing, socket not yet created (the token is still pending).
    // The button says BOTH things at once — .rec because your voice is being
    // kept, .pre because the connection isn't up.
    ck("a_armed_btn", dic.btn.className, "micbtn rec pre");
    ck("a_sockets_before", H.sockets.length, 0);
    // the sample rate must be the WIRE rate, decided before the mint
    ck("a_mint_rate", H.tokens[0].body.sample_rate, 16000);
    ck("a_mint_cwd", H.tokens[0].body.working_directory, "/tmp/proj");
    ck("a_worklet_rate", H.nodes[0].opts.processorOptions.outRate, 16000);
    // three chunks spoken while the socket is still coming up
    node.emit(100); node.emit(200); node.emit(300);
    ck("a_held_not_sent", H.sockets.length, 0);
    H.tok.resolve({ ws_url: "wss://dg/listen?x=1", token: "jwt" });
    await tick();
    ck("a_socket_made", H.sockets.length, 1);
    ck("a_proto", H.sockets[0].protos, ["bearer", "jwt"]);
    ck("a_sent_before_open", H.sockets[0].sent.length, 0);
    H.sockets[0].open();
    await tick();
    // …and out they go, oldest first, before any live audio
    ck("a_flushed", H.sockets[0].sent, ["pcm:100", "pcm:200", "pcm:300"]);
    ck("a_open_btn", dic.btn.className, "micbtn rec");   // .pre retired
    node.emit(400);                         // now streaming live
    ck("a_live", H.sockets[0].sent.slice(-1), ["pcm:400"]);
    const start = H.clogs.find(c => c.ev === "dictate.start");
    ck("a_start_logged", !!start, true);
    ck("a_start_fields",
       start && Object.keys(start.data).sort(),
       ["arm_ms", "native", "open_ms", "preroll_s", "rate"]);
    // the preroll is reported in seconds of audio: 600 bytes @16k/16-bit
    ck("a_preroll_s", start && start.data.preroll_s, 0.02);
    ck("a_input_listener", (ta._on.input || []).length, 1);
  }

  /* ---- B: stop BEFORE the socket opens still delivers the words --------- */
  {
    const { dic, node } = await arm();
    node.emit(100); node.emit(200);
    dic.stop();                              // token still in flight
    await tick();
    ck("b_no_socket_yet", H.sockets.length, 0);
    H.tok.resolve({ ws_url: "wss://dg/listen", token: "jwt" });
    await tick();
    // it still connects — the words are already captured and worth delivering
    ck("b_connected_anyway", H.sockets.length, 1);
    H.sockets[0].open();
    await tick();
    // flush FIRST, then the close that makes Deepgram emit the final
    ck("b_order", H.sockets[0].sent,
       ["pcm:100", "pcm:200", '{"type":"CloseStream"}']);
    node.emit(300);                          // a stopped mic takes no more
    ck("b_no_audio_after_stop", H.sockets[0].sent.length, 3);
  }

  /* ---- C: stop with nothing said opens nothing -------------------------- */
  {
    const { dic } = await arm();
    dic.stop();
    await tick();
    H.tok.resolve({ ws_url: "wss://dg/listen", token: "jwt" });
    await tick();
    ck("c_no_socket", H.sockets.length, 0);
    ck("c_mic_released", track.stopped === true, true);
    ck("c_stop_logged", H.clogs.filter(c => c.ev === "dictate.stop").length, 1);
  }

  /* ---- D: a denied mic leaves nothing behind and can be retried --------- */
  {
    H.sockets.length = 0; H.toasts.length = 0; H.tokens.length = 0;
    H.mic = defer(); H.mod = defer(); H.tok = defer();
    const ta = textarea();
    const dic = vm.runInContext("dictation", sandbox)(
      ta,
      () => "",
      () => "claude_code",
      "s1",
    );
    dic.btn.onclick();
    H.mic.reject(new Error("NotAllowedError"));
    H.mod.resolve();
    await tick();
    ck("d_toast", H.toasts, ["microphone blocked|allow mic access for this site and retry"]);
    ck("d_no_input_listener", (ta._on.input || []).length, 0);
    ck("d_btn_idle", dic.btn.className, "micbtn");
    // the latch released, so the next press actually tries again
    H.mic = defer(); H.mod = defer(); H.tok = defer();
    dic.btn.onclick();
    H.mic.resolve(stream); H.mod.resolve();
    await tick();
    ck("d_retry_armed", dic.btn.className.includes("rec"), true);
  }

  out.ok = out.errors.length === 0;
  console.log(JSON.stringify(out, null, 1));
  process.exit(out.ok ? 0 : 1);
})().catch(e => {
  console.log(JSON.stringify({ ok: false, errors: [String(e && e.stack || e)] }));
  process.exit(1);
});
