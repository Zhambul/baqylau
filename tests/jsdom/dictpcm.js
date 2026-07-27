// tests/jsdom/dictpcm.js — drives the REAL dictation worklet (the DICT_WORKLET
// source in dashboard/static/app.07-dialogs.js) and prints one JSON verdict
// object, which tests/test_l0_dash_probes.py asserts on.
//
// Why this exists: the worklet is the one piece of this repo that is signal
// processing, and every way it can be wrong is invisible to a grep and to
// `node --check` alike (it lives inside a template string, so the outer file's
// syntax check never even parses it). A decimator with the filter accidentally
// bypassed still produces plausible-looking PCM — it just folds every sibilant
// above 8 kHz down onto the vowels and quietly makes transcription worse. A
// phase accumulator that resets per render quantum still produces roughly the
// right SAMPLE COUNT — it just clicks 375 times a second. Neither shows up
// until you listen to a transcript that got worse and cannot say why.
//
// So it executes the thing: feed synthetic tones through the actual worklet
// class at real device rates and measure what comes out.
//   - rate conversion: 48000 and 44100 (the fractional case — 44100/16000 is
//     2.756, so a decimate-by-N would be wrong here and only here)
//   - the passband survives (1 kHz comes through at full amplitude)
//   - the STOPBAND is actually stopped (12 kHz, which without the anti-alias
//     filter would alias to 4 kHz at full amplitude, comes through at ~a tenth)
//   - hardware already at the target rate is a byte-exact passthrough
//
// Usage: node tests/jsdom/dictpcm.js dashboard/static/app.07-dialogs.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const QUANTUM = 128;              // the WebAudio render quantum process() sees
const FULL_SCALE = 32768;

/* ---- pull the worklet SOURCE out of the real app part ---------------------
   Top level of app.07 is declarations only (no I/O, no DOM at load), so the
   file evaluates in a bare context; the trailing line is how a top-level
   `const` (lexical, never a property of the sandbox) is handed back out. */
const app = { console, Math, JSON, Date, Object, Array, Number, String,
              Boolean, Promise, Set, Map, RegExp,
              Float32Array, Int16Array, ArrayBuffer,
              fetch: () => new Promise(() => {}),
              setTimeout: () => 1, clearTimeout: () => {},
              setInterval: () => 1, clearInterval: () => {} };
vm.createContext(app);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8")
  + "\n;globalThis.__W = DICT_WORKLET;"
  + "\n;globalThis.__RATE = DICT_RATE;"
  + "\n;globalThis.__CHUNK = DICT_CHUNK;", app);

/* ---- the worklet global scope (AudioWorkletGlobalScope, shimmed) ---------- */
function processor(inRate, outRate) {
  let Cls = null;
  const w = {
    Math, Float32Array, Int16Array, ArrayBuffer, console,
    sampleRate: inRate,                       // the real global's name
    AudioWorkletProcessor: class {
      constructor() { this.port = { postMessage: null }; }
    },
    registerProcessor: (name, c) => { Cls = c; },
  };
  vm.createContext(w);
  vm.runInContext(app.__W, w);
  if (!Cls) throw new Error("worklet registered no processor");
  const chunks = [];
  const p = new Cls({ processorOptions: { outRate } });
  p.port.postMessage = (ab) => chunks.push(new Int16Array(ab));
  return { p, chunks };
}

// `secs` of a `hz` sine at `inRate`, pushed through in render quanta
function feed(p, inRate, secs, hz) {
  const n = Math.round(inRate * secs);
  const blk = new Float32Array(QUANTUM);
  for (let i = 0; i < n; i += QUANTUM) {
    for (let j = 0; j < QUANTUM; j++)
      blk[j] = Math.sin(2 * Math.PI * hz * (i + j) / inRate);
    p.process([[blk]]);
  }
}

function samples(chunks) {
  const out = [];
  for (const c of chunks) for (let i = 0; i < c.length; i++) out.push(c[i]);
  return out;
}

// RMS over the LATTER part only — the biquads start from rest and their
// transient is not what is under test
function rms(v) {
  const from = Math.floor(v.length * 0.3);
  let s = 0;
  for (let i = from; i < v.length; i++) s += v[i] * v[i];
  return Math.sqrt(s / (v.length - from)) / FULL_SCALE;
}

const SINE_RMS = Math.SQRT1_2;    // what a full-scale sine should measure

function convert(inRate, hz, secs) {
  const { p, chunks } = processor(inRate, app.__RATE);
  feed(p, inRate, secs, hz);
  const v = samples(chunks);
  return {
    emitted: v.length,
    // the ideal, floored to whole chunks — a partial chunk is still buffered
    expected: Math.floor(Math.round(inRate * secs) * (app.__RATE / inRate)
                         / app.__CHUNK) * app.__CHUNK,
    gain: +(rms(v) / SINE_RMS).toFixed(3),
    widths: Array.from(new Set(chunks.map(c => c.length))),
  };
}

/* ---- hardware already at the target: byte-exact passthrough -------------- */
const thru = (() => {
  const { p, chunks } = processor(app.__RATE, app.__RATE);
  feed(p, app.__RATE, 1, 1000);
  const v = samples(chunks);
  let maxerr = 0;
  for (let i = 0; i < v.length; i++) {
    const want = Math.sin(2 * Math.PI * 1000 * i / app.__RATE);
    maxerr = Math.max(maxerr, Math.abs(v[i] / FULL_SCALE - want));
  }
  return { emitted: v.length, maxerr: +maxerr.toFixed(4) };
})();

console.log(JSON.stringify({
  rate: app.__RATE,
  chunk: app.__CHUNK,
  // 48k and 44.1k are the two rates real hardware actually runs at
  c48: convert(48000, 1000, 2),
  c44: convert(44100, 1000, 2),
  // 12 kHz is above the 8 kHz Nyquist of the output: with the anti-alias
  // filter it nearly vanishes, without it it comes back as a full-amplitude
  // 4 kHz tone sitting in the middle of the speech band
  alias: convert(48000, 12000, 2).gain,
  thru,
}));
