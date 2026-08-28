/* SDRADIO canvas renderers.
 * Waterfall — scrolling heatmap strip fed with uint8 FFT bins (0..255 ->
 * -100..-20 dBFS), green/heat colormap, click-to-tune callback.
 * Trace    — spectrum line display over an assembled frequency grid:
 *            current / max-hold / average traces, grid lines, peak marks. */
(function () {
  'use strict';

  /* value 0..255 -> dBFS -100..-20 */
  function binToDb(v) { return -100 + (v * 80) / 255; }
  function dbToBin(db) { return Math.max(0, Math.min(255, Math.round(((db + 100) / 80) * 255))); }

  function greenHeatLUT() {
    const l = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
      let r, g, b;
      if (i < 96) {           // black -> green
        r = 0; g = Math.round(i * 1.6); b = Math.round(i * 0.35);
      } else if (i < 180) {   // green -> yellow
        const t = (i - 96) / 84;
        r = Math.round(t * 255); g = Math.round(154 + t * 101); b = Math.round(34 * (1 - t));
      } else {                // yellow -> white-hot
        const t = Math.min(1, (i - 180) / 75);
        r = 255; g = Math.round(255 - t * 60); b = Math.round(t * 200);
      }
      l[i * 4] = r; l[i * 4 + 1] = g; l[i * 4 + 2] = b; l[i * 4 + 3] = 255;
    }
    return l;
  }

  class Waterfall {
    constructor(canvas) {
      this.cv = canvas;
      this.ctx = canvas.getContext('2d');
      this.onTune = null; // set to fn(fraction 0..1 across width)
      this._img = document.createElement('canvas');
      this._ictx = this._img.getContext('2d');
      this._lut = greenHeatLUT();

      const resize = () => {
        const dpr = window.devicePixelRatio || 1;
        const w = this.cv.clientWidth || 600;
        const h = this.cv.clientHeight || 150;
        this.cv.width = Math.round(w * dpr);
        this.cv.height = Math.round(h * dpr);
        // preserve current picture across resize
        const old = document.createElement('canvas');
        old.width = this._img.width; old.height = this._img.height;
        old.getContext('2d').drawImage(this._img, 0, 0);
        this._img.width = this.cv.width; this._img.height = this.cv.height;
        if (old.width) this._ictx.drawImage(old, 0, 0, this._img.width, this._img.height);
        this.ctx.imageSmoothingEnabled = false;
      };
      this._resize = resize;
      window.addEventListener('resize', resize);
      requestAnimationFrame(resize);

      this.cv.addEventListener('click', (e) => {
        if (!this.onTune) return;
        const r = this.cv.getBoundingClientRect();
        this.onTune((e.clientX - r.left) / r.width);
      });
    }

    /* bins: Uint8Array of any length, stretched across the strip width */
    pushRow(bins) {
      if (!this.cv.width) this._resize();
      const W = this.cv.width;
      const n = bins.length;
      if (!W || !n) return;
      // scroll existing picture down 1 device pixel
      this._ictx.drawImage(this._img, 0, 1);
      const row = this._ictx.createImageData(W, 1);
      const d = row.data;
      const lut = this._lut;
      for (let x = 0; x < W; x++) {
        const v = bins[Math.min(n - 1, (x * n / W) | 0)];
        const o = v * 4, p = x * 4;
        d[p] = lut[o]; d[p + 1] = lut[o + 1]; d[p + 2] = lut[o + 2]; d[p + 3] = 255;
      }
      this._ictx.putImageData(row, 0, 0);
      this.ctx.drawImage(this._img, 0, 0);
    }

    clear() {
      this._ictx.clearRect(0, 0, this._img.width, this._img.height);
      if (this.cv.width) this.ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    }
  }

  /* Assembles per-step FFT frames from a sweep into one wideband trace. */
  class Trace {
    constructor(canvas) {
      this.cv = canvas;
      this.ctx = canvas.getContext('2d');
      this.startHz = 0;
      this.stopHz = 0;
      this.cols = 0;
      this.cur = null;   // latest pass, dBFS
      this.max = null;   // max hold
      this.avg = null;   // running average
      this.peaks = [];   // [{freq, db, col}]
      this.selected = null; // Hz, for TUNE HERE marker
      this._lastRender = 0;

      const resize = () => {
        const dpr = window.devicePixelRatio || 1;
        this.cv.width = Math.round((this.cv.clientWidth || 800) * dpr);
        this.cv.height = Math.round((this.cv.clientHeight || 260) * dpr);
        this.render(true);
      };
      window.addEventListener('resize', resize);
      requestAnimationFrame(resize);
    }

    configure(startHz, stopHz, cols) {
      this.startHz = startHz;
      this.stopHz = stopHz;
      this.cols = cols;
      this.cur = new Float32Array(cols).fill(-100);
      this.max = new Float32Array(cols).fill(-100);
      this.avg = new Float32Array(cols).fill(-100);
      this.peaks = [];
      this.selected = null;
    }

    /* Merge one FFT step frame into the grid. */
    ingest(centerHz, binHz, bins) {
      if (!this.cur) return;
      const n = bins.length;
      const f0 = centerHz - (n * binHz) / 2;
      const span = this.stopHz - this.startHz;
      for (let i = 0; i < n; i++) {
        const col = Math.floor(((f0 + i * binHz) - this.startHz) / span * this.cols);
        if (col < 0 || col >= this.cols) continue;
        const db = binToDb(bins[i]);
        this.cur[col] = db;
        if (db > this.max[col]) this.max[col] = db;
        this.avg[col] = this.avg[col] <= -99.5 ? db : this.avg[col] * 0.85 + db * 0.15;
      }
    }

    resetMaxHold() {
      if (this.max) this.max.fill(-100);
      this.peaks = [];
    }

    /* Local maxima of the average trace, min separation, top N by level. */
    detectPeaks(maxCount) {
      if (!this.avg) return [];
      const cols = this.cols, avg = this.avg;
      // adaptive threshold: mean + 8 dB, at least -70 dBFS
      let sum = 0;
      for (let i = 0; i < cols; i++) sum += avg[i];
      const thresh = Math.max(-70, sum / cols + 8);
      const cand = [];
      const sep = Math.max(8, Math.floor(cols / 80));
      for (let i = sep; i < cols - sep; i++) {
        const v = avg[i];
        if (v < thresh) continue;
        let top = true;
        for (let j = i - sep; j <= i + sep; j++) {
          if (avg[j] > v) { top = false; break; }
        }
        if (top) cand.push({ col: i, db: v });
      }
      cand.sort((a, b) => b.db - a.db);
      this.peaks = cand.slice(0, maxCount || 10).map(p => ({
        col: p.col, db: p.db,
        freq: this.startHz + (p.col + 0.5) / cols * (this.stopHz - this.startHz)
      }));
      return this.peaks;
    }

    freqAt(frac) {
      return this.startHz + frac * (this.stopHz - this.startHz);
    }

    /* Peak nearest to a click fraction, or null if none within tolerance. */
    peakNear(frac, tolFrac) {
      if (!this.peaks.length) return null;
      const span = this.stopHz - this.startHz;
      const f = this.freqAt(frac);
      let best = null, bestD = Infinity;
      for (const p of this.peaks) {
        const d = Math.abs(p.freq - f) / span;
        if (d < bestD) { bestD = d; best = p; }
      }
      return bestD <= (tolFrac || 0.02) ? best : null;
    }

    render(force) {
      if (!this.cur || !this.cv.width) return;
      const now = performance.now();
      if (!force && now - this._lastRender < 100) return; // ~10 fps is plenty
      this._lastRender = now;

      const ctx = this.ctx, W = this.cv.width, H = this.cv.height;
      const cols = this.cols;
      const DB_LO = -100, DB_HI = -20;
      const x2c = (c) => (c + 0.5) / cols * W;
      const db2y = (db) => H - ((db - DB_LO) / (DB_HI - DB_LO)) * H;

      ctx.fillStyle = '#060a08';
      ctx.fillRect(0, 0, W, H);

      // horizontal grid every 10 dB
      ctx.strokeStyle = 'rgba(70,120,90,0.25)';
      ctx.fillStyle = 'rgba(120,200,150,0.55)';
      ctx.font = `${Math.max(10, H * 0.045)}px monospace`;
      ctx.lineWidth = 1;
      for (let db = DB_LO; db <= DB_HI; db += 10) {
        const y = Math.round(db2y(db)) + 0.5;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        ctx.fillText(db + ' dB', 4, y - 2);
      }
      // vertical grid at "nice" frequency steps
      const spanMHz = (this.stopHz - this.startHz) / 1e6;
      const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];
      let stepMHz = steps[steps.length - 1];
      for (const s of steps) { if (spanMHz / s <= 12) { stepMHz = s; break; } }
      const firstMHz = Math.ceil(this.startHz / 1e6 / stepMHz) * stepMHz;
      for (let m = firstMHz; m <= this.stopHz / 1e6; m += stepMHz) {
        const x = Math.round((m * 1e6 - this.startHz) / (this.stopHz - this.startHz) * W) + 0.5;
        ctx.strokeStyle = 'rgba(70,120,90,0.25)';
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
        ctx.fillStyle = 'rgba(120,200,150,0.55)';
        ctx.fillText(m.toFixed(stepMHz < 1 ? 1 : 0), x + 3, H - 4);
      }

      const drawLine = (arr, color, width) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.beginPath();
        for (let c = 0; c < cols; c++) {
          const x = x2c(c), y = db2y(arr[c]);
          if (c === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      };

      drawLine(this.max, 'rgba(255,190,90,0.55)', 1);   // max hold (amber)
      drawLine(this.avg, 'rgba(80,220,140,0.45)', 1);   // average (dim green)
      drawLine(this.cur, '#7dff9a', 1.5);               // current (bright)

      // peak markers
      ctx.fillStyle = '#ffd75e';
      ctx.strokeStyle = '#ffd75e';
      ctx.lineWidth = 1;
      for (const p of this.peaks) {
        const x = x2c(p.col), y = db2y(p.db);
        ctx.beginPath();
        ctx.moveTo(x, y - 10); ctx.lineTo(x - 5, y - 2); ctx.lineTo(x + 5, y - 2);
        ctx.closePath(); ctx.fill();
        const label = (p.freq / 1e6).toFixed(3);
        ctx.fillText(label, Math.min(x + 6, W - 60), Math.max(12, y - 12));
      }

      // selected frequency marker (TUNE HERE)
      if (this.selected) {
        const x = Math.round((this.selected - this.startHz) / (this.stopHz - this.startHz) * W) + 0.5;
        ctx.strokeStyle = '#57c8ff';
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
        ctx.fillStyle = '#57c8ff';
        ctx.fillText((this.selected / 1e6).toFixed(4) + ' MHz', Math.min(x + 4, W - 90), 14);
      }
    }

    /* Downsample current trace into a Uint8 row for the waterfall. */
    waterfallRow(outLen) {
      const row = new Uint8Array(outLen);
      if (!this.cur) return row;
      for (let x = 0; x < outLen; x++) {
        // max of the source columns mapped to this output pixel
        const c0 = Math.floor(x * this.cols / outLen);
        const c1 = Math.max(c0 + 1, Math.floor((x + 1) * this.cols / outLen));
        let m = -100;
        for (let c = c0; c < c1 && c < this.cols; c++) if (this.cur[c] > m) m = this.cur[c];
        row[x] = dbToBin(m);
      }
      return row;
    }
  }

  window.Waterfall = Waterfall;
  window.Trace = Trace;
  window.SDRBin = { binToDb: binToDb, dbToBin: dbToBin };
})();
