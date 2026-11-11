// Runs on the audio thread for live listening (listen.js): RMS of 4 channels (full, low, mid, high), once a second.
// A file rather than a blob: URL so the page's Content-Security-Policy can stay "scripts from this server only".
class Meter extends AudioWorkletProcessor {
  constructor() { super(); this.sums = [0, 0, 0, 0]; this.n = 0; }
  process(inputs) {
    const ins = inputs[0];
    if (ins && ins.length) {
      const len = ins[0].length;
      for (let c = 0; c < 4; c++) {
        const ch = ins[Math.min(c, ins.length - 1)];
        let s = 0;
        for (let i = 0; i < len; i++) s += ch[i] * ch[i];
        this.sums[c] += s;
      }
      this.n += len;
      if (this.n >= sampleRate) {
        this.port.postMessage(this.sums.map((s) => Math.sqrt(s / this.n)));
        this.sums = [0, 0, 0, 0];
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("db-meter", Meter);
