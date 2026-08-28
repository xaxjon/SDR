/* SDRADIO WebSocket client.
 * Connects to ws://<page-host>:8765, dispatches JSON status/scan_hit/error
 * messages and binary audio (0x01) / FFT (0x02) frames, auto-reconnects.
 * See docs/PROTOCOL.md. */
(function () {
  'use strict';

  class SDRClient {
    constructor(opts) {
      opts = opts || {};
      this.url = opts.url || ('ws://' + location.hostname + ':8765');
      this.reconnectMs = opts.reconnectMs || 2000;
      this.handlers = {};
      this.ws = null;
      this.connected = false;
      this._closed = false;
      this._timer = null;
    }

    /* Register an event handler.
     * Events: connect, disconnect, status, scan_hit, proto_error, audio, fft */
    on(ev, fn) { this.handlers[ev] = fn; return this; }

    _emit(ev, arg) {
      const h = this.handlers[ev];
      if (h) { try { h(arg); } catch (e) { console.error('SDR handler', ev, e); } }
    }

    connect() {
      if (this._closed) return;
      clearTimeout(this._timer);
      let ws;
      try { ws = new WebSocket(this.url); }
      catch (e) { this._schedule(); return; }
      this.ws = ws;
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        this.connected = true;
        this._emit('connect');
      };
      ws.onclose = () => {
        if (this.connected) { this.connected = false; this._emit('disconnect'); }
        else this._emit('disconnect'); // failed first connect also reports
        this._schedule();
      };
      ws.onerror = () => { /* onclose always follows */ };
      ws.onmessage = (ev) => {
        if (typeof ev.data === 'string') this._json(ev.data);
        else this._binary(ev.data);
      };
    }

    _schedule() {
      if (this._closed) return;
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this.connect(), this.reconnectMs);
    }

    _json(text) {
      let m;
      try { m = JSON.parse(text); } catch (e) { return; }
      if (m.type === 'status') this._emit('status', m);
      else if (m.type === 'scan_hit') this._emit('scan_hit', m);
      else if (m.type === 'receivers') this._emit('receivers', m);
      else if (m.type === 'error') this._emit('proto_error', m);
    }

    _binary(buf) {
      if (buf.byteLength < 1) return;
      const dv = new DataView(buf);
      const type = dv.getUint8(0);
      if (type === 0x01) {
        // s16le mono PCM @ 48 kHz (slice: Int16Array needs 2-byte alignment)
        this._emit('audio', new Int16Array(buf.slice(1)));
      } else if (type === 0x02 && buf.byteLength >= 11) {
        // header struct '<BIfH': type, center_hz u32, bin_hz f32, count u16
        const centerHz = dv.getUint32(1, true);
        const binHz = dv.getFloat32(5, true);
        const count = dv.getUint16(9, true);
        if (buf.byteLength < 11 + count) return;
        this._emit('fft', {
          centerHz: centerHz,
          binHz: binHz,
          count: count,
          bins: new Uint8Array(buf, 11, count) // 0..255 -> -100..-20 dBFS
        });
      }
    }

    send(obj) {
      if (this.connected && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(obj));
      }
    }

    tune(freqHz, mode) { this.send({ cmd: 'tune', freq: Math.round(freqHz), mode: mode }); }
    setSquelch(level) { this.send({ cmd: 'squelch', level: level }); }
    fft(enable) { this.send({ cmd: 'fft', enable: !!enable }); }
    getReceivers() { this.send({ cmd: 'receivers' }); }
    setReceiver(id) { this.send({ cmd: 'receiver', id: id }); }
    scan(channels) {
      this.send({ cmd: 'scan', channels: channels, dwell_ms: 220, hold_ms: 1500, threshold_db: 12 });
    }
    stopScan() { this.send({ cmd: 'stop_scan' }); }
    sweep(startHz, stopHz) { this.send({ cmd: 'sweep', start: Math.round(startHz), stop: Math.round(stopHz) }); }
    stopSweep() { this.send({ cmd: 'stop_sweep' }); }

    close() {
      this._closed = true;
      clearTimeout(this._timer);
      if (this.ws) this.ws.close();
    }
  }

  window.SDRClient = SDRClient;
})();
