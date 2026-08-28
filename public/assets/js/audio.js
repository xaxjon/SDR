/* SDRADIO audio playback.
 * AudioContext @ 48 kHz. Incoming s16le frames are converted to Float32 and
 * ring-buffered. AudioWorklet playback when available (worklet source is
 * inlined via Blob URL so the app stays fully offline, single file), with a
 * ScriptProcessorNode fallback. Output runs through a volume GainNode. */
(function () {
  'use strict';

  const WORKLET_SRC = `
class SdrPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunks = [];      // queue of Float32Array
    this.len = 0;          // total buffered samples
    this.cap = 96000;      // ~2 s @ 48 kHz
    this.port.onmessage = (e) => {
      const c = e.data;
      // drop oldest on overflow rather than grow latency
      while (this.len + c.length > this.cap && this.chunks.length) {
        this.len -= this.chunks[0].length;
        this.chunks.shift();
      }
      this.chunks.push(c);
      this.len += c.length;
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    let done = 0;
    while (done < out.length) {
      if (!this.chunks.length) { out.fill(0, done); break; }
      const c = this.chunks[0];
      const take = Math.min(out.length - done, c.length);
      out.set(c.subarray(0, take), done);
      done += take;
      this.len -= take;
      if (take === c.length) this.chunks.shift();
      else this.chunks[0] = c.subarray(take);
    }
    return true;
  }
}
registerProcessor('sdr-pcm', SdrPcmProcessor);
`;

  class SDRAudio {
    constructor() {
      this.ctx = null;
      SDRAudio.instance = this;               // debug handle
      this.gain = null;
      this.volume = 0.7;
      this.useWorklet = false;
      this.workletNode = null;
      // ScriptProcessor fallback ring buffer (~1 s)
      this.ring = new Float32Array(48000);
      this.rp = 0;
      this.wp = 0;
      this.count = 0;
      // browsers require a user gesture before audio starts
      const unlock = () => { this._ensure(); if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume(); };
      window.addEventListener('pointerdown', unlock);
      window.addEventListener('keydown', unlock);
    }

    async _ensure() {
      if (this.ctx) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this.ctx = new AC({ sampleRate: 48000 });
      this.gain = this.ctx.createGain();
      this.gain.gain.value = this.volume;
      this.gain.connect(this.ctx.destination);

      try {
        const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'application/javascript' }));
        await this.ctx.audioWorklet.addModule(url);
        URL.revokeObjectURL(url);
        this.workletNode = new AudioWorkletNode(this.ctx, 'sdr-pcm');
        this.workletNode.connect(this.gain);
        this.useWorklet = true;
      } catch (e) {
        // ScriptProcessor fallback
        const sp = this.ctx.createScriptProcessor(4096, 0, 1);
        const self = this;
        sp.onaudioprocess = function (ev) {
          const out = ev.outputBuffer.getChannelData(0);
          const n = out.length;
          const take = Math.min(n, self.count);
          const R = self.ring;
          for (let k = 0; k < take; k++) { out[k] = R[self.rp]; self.rp = (self.rp + 1) % R.length; }
          if (take < n) out.fill(0, take);
          self.count -= take;
        };
        sp.connect(this.gain);
      }
    }

    /* int16: Int16Array of s16le mono samples @ 48 kHz */
    push(int16) {
      SDRAudio.frames = (SDRAudio.frames || 0) + 1;   // debug counter
      if (!this.ctx) { this._ensure(); return; } // drop until context ready
      const n = int16.length;
      const f = new Float32Array(n);
      for (let i = 0; i < n; i++) f[i] = int16[i] / 32768;

      if (this.useWorklet) {
        this.workletNode.port.postMessage(f, [f.buffer]);
      } else {
        const R = this.ring;
        if (this.count + n > R.length) { // overflow: drop oldest
          const drop = this.count + n - R.length;
          this.rp = (this.rp + drop) % R.length;
          this.count -= drop;
        }
        for (let i = 0; i < n; i++) { R[this.wp] = f[i]; this.wp = (this.wp + 1) % R.length; }
        this.count += n;
      }
    }

    setVolume(v) {
      this.volume = v;
      if (this.gain) this.gain.gain.value = v;
    }
  }

  window.SDRAudio = SDRAudio;
})();
