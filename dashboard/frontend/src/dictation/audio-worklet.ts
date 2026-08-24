export const DICTATION_RATE = 16_000;
const DICTATION_CHUNK = 1_024;

// This source runs in AudioWorkletGlobalScope, not in the page's TypeScript
// realm. Keep it as one reviewed asset so the token sample rate and processor
// output rate have one caller-owned value.
export const DICTATION_WORKLET = `
const CHUNK = ${String(DICTATION_CHUNK)};
const LP_HZ = 0.425;
const LP_Q = [0.5412, 1.3065];

class Biquad {
  constructor(rate, hz, q) {
    const w = 2 * Math.PI * hz / rate;
    const c = Math.cos(w);
    const al = Math.sin(w) / (2 * q);
    const a0 = 1 + al;
    this.b0 = (1 - c) / 2 / a0;
    this.b1 = (1 - c) / a0;
    this.b2 = this.b0;
    this.a1 = -2 * c / a0;
    this.a2 = (1 - al) / a0;
    this.x1 = this.x2 = this.y1 = this.y2 = 0;
  }
  step(x) {
    const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2
      - this.a1 * this.y1 - this.a2 * this.y2;
    this.x2 = this.x1;
    this.x1 = x;
    this.y2 = this.y1;
    this.y1 = y;
    return y;
  }
}

class DictatePCM extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const out = options?.processorOptions?.outRate || sampleRate;
    this.step = sampleRate / out;
    this.lowPass = this.step > 1.0001
      ? LP_Q.map((q) => new Biquad(sampleRate, LP_HZ * out, q))
      : [];
    this.position = 0;
    this.previous = 0;
    this.filtered = null;
    this.buffer = new Int16Array(CHUNK);
    this.length = 0;
  }
  emit(value) {
    const sample = Math.max(-1, Math.min(1, value));
    this.buffer[this.length] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    this.length += 1;
    if (this.length === this.buffer.length) {
      this.port.postMessage(this.buffer.slice(0).buffer);
      this.length = 0;
    }
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    if (this.lowPass.length === 0) {
      for (const sample of channel) this.emit(sample);
      return true;
    }
    if (!this.filtered || this.filtered.length < channel.length)
      this.filtered = new Float32Array(channel.length);
    for (let index = 0; index < channel.length; index += 1) {
      let value = channel[index];
      for (const filter of this.lowPass) value = filter.step(value);
      this.filtered[index] = value;
    }
    while (this.position < channel.length - 1) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const before = index < 0 ? this.previous : this.filtered[index];
      const after = this.filtered[index + 1];
      this.emit(before + (after - before) * fraction);
      this.position += this.step;
    }
    this.previous = this.filtered[channel.length - 1];
    this.position -= channel.length;
    return true;
  }
}

registerProcessor('dictate-pcm', DictatePCM);
`;
