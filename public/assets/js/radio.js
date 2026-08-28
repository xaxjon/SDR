/* SDRADIO radio panel logic.
 * Driven by window.RADIO_CONFIG injected by radio.php:
 *   { svc, mode, title, dataUrl, band:[start,stop], step, decimals,
 *     knobSpan, initialFreq|null, features:{presets,region,channels,spacing} }
 * Panels are service-agnostic; optional DOM blocks are probed by id. */
(function () {
  'use strict';

  const CFG = window.RADIO_CONFIG;
  const $ = (id) => document.getElementById(id);
  const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };

  // ---------- state ----------
  let freq = CFG.initialFreq || CFG.defaultFreq || CFG.band[0];
  let step = CFG.step;
  let scanning = false;
  let regions = null;          // marine: {name: [{ch,freq,label}]}
  let channels = [];           // current region channel list
  let presets = [];
  let lastFFT = null;          // last FFT frame (for waterfall click-tune)
  let entryBuf = '';           // keypad entry buffer
  let hitTimer = null;

  // ---------- helpers ----------
  function fmtFreq(f) { return (f / 1e6).toFixed(CFG.decimals); }
  /* Band edges define scan range / step grid origin; the tuner itself covers
   * 24–1766 MHz, so explicit tunes (presets, keypad, ?freq=, waterfall click)
   * may land slightly outside the nominal service band (e.g. FM preset at
   * 108.027). */
  const TUNER_LO = 24e6, TUNER_HI = 1766e6;
  function clampBand(f) { return Math.max(TUNER_LO, Math.min(TUNER_HI, f)); }
  function snap(f) { return CFG.band[0] + Math.round((f - CFG.band[0]) / step) * step; }

  function channelAt(f) {
    if (CFG.svc !== 'marine') return null;
    let best = null, bestD = Infinity;
    for (const c of channels) {
      const d = Math.abs(c.freq - f);
      if (d < bestD) { bestD = d; best = c; }
    }
    return bestD < 3000 ? best : null;
  }

  // ---------- display ----------
  function updateDisplay() {
    const main = $('lcd-main'), sub = $('lcd-sub');
    if (entryBuf) {
      main.textContent = entryBuf + '_';
      sub.textContent = 'ENTER FREQ (MHz) · ENT TO TUNE';
      return;
    }
    const ch = channelAt(freq);
    if (ch) {
      main.textContent = 'CH ' + ch.ch;
      sub.textContent = fmtFreq(ch.freq) + ' MHz · ' + ch.label.toUpperCase();
    } else {
      main.textContent = fmtFreq(freq);
      sub.textContent = CFG.mode.toUpperCase() + ' · MHz';
    }
  }

  // ---------- S-meter ----------
  const SEGS = 12;
  function buildSmeter() {
    const box = $('smeter');
    if (!box) return;
    for (let i = 0; i < SEGS; i++) {
      const s = document.createElement('span');
      s.className = 'seg ' + (i < 7 ? 'low' : i < 10 ? 'mid' : 'high');
      box.appendChild(s);
    }
  }
  function setSmeter(rssiDb) {
    const box = $('smeter');
    if (!box) return;
    // map roughly -60..-10 dB to 0..100 %
    const pct = Math.max(0, Math.min(1, (rssiDb + 60) / 50));
    const lit = Math.round(pct * SEGS);
    const segs = box.children;
    for (let i = 0; i < segs.length; i++) segs[i].classList.toggle('on', i < lit);
  }

  // ---------- tuning ----------
  const sdr = new SDRClient();
  const audio = new SDRAudio();

  function tuneTo(f, noSnap) {
    freq = clampBand(noSnap ? f : snap(f));
    updateDisplay();
    sdr.tune(freq, CFG.mode);
  }

  on($('tune-up'), 'click', () => tuneTo(freq + step));
  on($('tune-dn'), 'click', () => tuneTo(freq - step));

  // marine channel stepping
  function stepChannel(dir) {
    if (!channels.length) return;
    let idx = channels.findIndex(c => c.freq >= freq - 1000);
    if (idx < 0) idx = 0;
    idx = (idx + dir + channels.length) % channels.length;
    tuneTo(channels[idx].freq);
  }
  on($('ch-up'), 'click', () => stepChannel(1));
  on($('ch-dn'), 'click', () => stepChannel(-1));

  // tuning knob: circular drag, one full turn = CFG.knobSpan Hz
  (function knob() {
    const el = $('knob');
    if (!el) return;
    let dragging = false, lastAngle = 0, total = 0;
    const angleOf = (e) => {
      const r = el.getBoundingClientRect();
      return Math.atan2(e.clientY - (r.top + r.height / 2), e.clientX - (r.left + r.width / 2));
    };
    el.addEventListener('pointerdown', (e) => {
      dragging = true; lastAngle = angleOf(e);
      el.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    el.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const a = angleOf(e);
      let d = a - lastAngle;
      if (d > Math.PI) d -= 2 * Math.PI;      // unwrap across ±π
      if (d < -Math.PI) d += 2 * Math.PI;
      lastAngle = a;
      total += d;
      el.style.transform = 'rotate(' + total + 'rad)';
      if (Math.abs(d) > 0.0001) tuneTo(freq + (d / (2 * Math.PI)) * CFG.knobSpan);
    });
    const end = () => { dragging = false; };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  })();

  // ---------- keypad ----------
  (function keypad() {
    const pad = $('keypad');
    if (!pad) return;
    pad.querySelectorAll('button[data-k]').forEach(btn => {
      btn.addEventListener('click', () => {
        const k = btn.getAttribute('data-k');
        if (k === 'ENT') {
          if (!entryBuf) return;
          const v = parseFloat(entryBuf);
          entryBuf = '';
          if (!isNaN(v)) tuneTo(v > 1000 ? v : v * 1e6, true); // bare MHz or raw Hz
          else updateDisplay();
        } else if (k === 'CLR') {
          entryBuf = '';
          updateDisplay();
        } else {
          if (k === '.' && entryBuf.indexOf('.') >= 0) return;
          if (entryBuf.replace('.', '').length >= 9) return;
          entryBuf += k;
          updateDisplay();
        }
      });
    });
  })();

  // ---------- sliders / LEDs ----------
  on($('vol'), 'input', (e) => audio.setVolume(parseFloat(e.target.value)));
  on($('sql'), 'input', (e) => sdr.setSquelch(parseFloat(e.target.value)));

  function setLed(id, lit) { const el = $(id); if (el) el.classList.toggle('on', !!lit); }

  // ---------- scan ----------
  function buildScanList() {
    if (CFG.svc === 'marine' && channels.length) {
      return channels.map(c => ({ f: c.freq, m: CFG.mode, label: 'CH ' + c.ch }));
    }
    const list = [];
    for (let f = CFG.band[0]; f <= CFG.band[1]; f += step) {
      list.push({ f: f, m: CFG.mode, label: fmtFreq(f) });
    }
    return list;
  }

  function updateScanUI() {
    const btn = $('scan-btn');
    if (btn) {
      btn.textContent = scanning ? 'STOP' : 'SCAN';
      btn.classList.toggle('active', scanning);
    }
    setLed('led-scan', scanning);
    if (!scanning) hideHit();
  }

  function toggleScan() {
    if (scanning) {
      sdr.stopScan();
      scanning = false;
    } else {
      const list = buildScanList();
      if (!list.length) return;
      sdr.scan(list);
      scanning = true; // status message will confirm
    }
    updateScanUI();
  }
  on($('scan-btn'), 'click', toggleScan);

  function showHit(label) {
    const b = $('scan-banner');
    if (!b) return;
    b.textContent = (label || '') + ' ACTIVE';
    b.classList.remove('hidden');
    b.classList.add('hot');
    clearTimeout(hitTimer);
    hitTimer = setTimeout(() => { b.classList.remove('hot'); }, 1200);
  }
  function hideHit() {
    const b = $('scan-banner');
    if (b) b.classList.add('hidden');
  }

  // ---------- marine regions ----------
  function loadRegion(name) {
    channels = (regions && regions[name]) || [];
    const rl = $('region-label');
    if (rl) rl.textContent = name.toUpperCase();
    updateDisplay();
  }

  // ---------- service data ----------
  function loadData() {
    fetch(CFG.dataUrl)
      .then(r => r.json())
      .then(d => {
        presets = d.presets || [];
        renderPresets();
        if (CFG.svc === 'marine' && d.regions) {
          regions = d.regions;
          const sel = $('region');
          if (sel) {
            Object.keys(regions).forEach(name => {
              const o = document.createElement('option');
              o.value = name; o.textContent = name;
              sel.appendChild(o);
            });
            sel.addEventListener('change', () => loadRegion(sel.value));
            loadRegion(sel.value || 'International');
          }
        }
        if (CFG.svc === 'airband' && d.spacings) {
          document.querySelectorAll('#spacing-toggle button').forEach(btn => {
            const hz = parseInt(btn.getAttribute('data-spacing'), 10);
            btn.addEventListener('click', () => {
              step = hz;
              document.querySelectorAll('#spacing-toggle button')
                .forEach(b => b.classList.toggle('active', b === btn));
              const si = $('step-label');
              if (si) si.textContent = 'STEP ' + btn.textContent;
            });
            if (hz === (d.default_spacing || CFG.step)) btn.classList.add('active');
          });
        }
      })
      .catch(() => { /* data file missing: panel still works without presets */ });
  }

  function renderPresets() {
    const row = $('preset-row');
    if (!row || !presets.length) return;
    presets.forEach(p => {
      const b = document.createElement('button');
      b.className = 'pbtn preset';
      b.innerHTML = '<span class="preset-label"></span><span class="preset-freq"></span>';
      b.querySelector('.preset-label').textContent = p.label;
      b.querySelector('.preset-freq').textContent = fmtFreq(p.freq);
      b.addEventListener('click', () => {
        if (p.mode) CFG.mode = p.mode;      // per-preset mode (e.g. ham LSB/USB)
        tuneTo(p.freq, true);
      });
      row.appendChild(b);
    });
  }

  // ---------- waterfall strip ----------
  const wfCanvas = $('wf');
  const wf = wfCanvas ? new Waterfall(wfCanvas) : null;
  if (wf) {
    wf.onTune = (frac) => {
      if (!lastFFT) return;
      const span = lastFFT.count * lastFFT.binHz;
      tuneTo(lastFFT.centerHz + (frac - 0.5) * span, true);
    };
  }

  // ---------- connection ----------
  function connUI(up) {
    setLed('conn-led', up);
    const t = $('conn-text');
    if (t) t.textContent = up ? 'ONLINE' : 'OFFLINE';
    const panel = document.querySelector('.panel');
    if (panel) panel.classList.toggle('disconnected', !up);
  }

  sdr.on('connect', () => {
    connUI(true);
    sdr.getReceivers();
    tuneTo(freq, !!CFG.initialFreq); // initial tune (honors ?freq= via CFG.initialFreq)
    const sql = $('sql');
    if (sql) sdr.setSquelch(parseFloat(sql.value));
  });
  sdr.on('disconnect', () => {
    connUI(false);
    scanning = false;
    updateScanUI();
    hideHit();
  });
  sdr.on('status', (st) => {
    // The daemon drives the frequency only while scanning; otherwise the
    // panel is the source of truth (avoids a stale status echo overwriting
    // a tune we just requested).
    if (typeof st.freq === 'number' && (scanning || st.scanning)) {
      freq = st.freq; updateDisplay();
    }
    if (typeof st.rssi_db === 'number') setSmeter(st.rssi_db);
    setLed('sql-led', st.sql_open);
    const flag = $('flag-sql');
    if (flag) flag.classList.toggle('lit', !!st.sql_open);
    if (!!st.scanning !== scanning) { scanning = !!st.scanning; updateScanUI(); }
    if (st.scan_label) showHit(st.scan_label);
  });
  sdr.on('scan_hit', (h) => showHit(h.label || fmtFreq(h.freq)));
  sdr.on('proto_error', (e) => {
    const b = $('scan-banner');
    if (b) { b.textContent = 'ERROR: ' + (e.message || '?'); b.classList.remove('hidden'); }
  });
  sdr.on('audio', (samples) => audio.push(samples));
  sdr.on('fft', (f) => { lastFFT = f; if (wf) wf.pushRow(f.bins); });

  // ---------- external receiver awareness ----------
  /* When the active receiver is an ICOM PCR (audio:false, fft:false), the
   * panel becomes a remote control head: dim volume, hide the waterfall,
   * show an EXT RX badge. HF pages also warn when the RTL stick is active. */
  function applyReceiverCaps(caps) {
    const panel = document.querySelector('.panel');
    let badge = $('ext-badge');
    if (caps && caps.audio === false) {
      if (!badge) {
        badge = document.createElement('div');
        badge.id = 'ext-badge';
        badge.textContent = 'EXT RX · AUDIO ON RECEIVER SPEAKER';
        (document.querySelector('.panel-top') || panel).appendChild(badge);
      }
      badge.style.display = '';
      const vol = $('vol');
      if (vol) { vol.disabled = true; vol.parentElement.style.opacity = 0.4; }
      if (wfCanvas) wfCanvas.style.display = 'none';
    } else {
      if (badge) badge.style.display = 'none';
      const vol = $('vol');
      if (vol) { vol.disabled = false; vol.parentElement.style.opacity = ''; }
      if (wfCanvas && caps && caps.fft !== false) wfCanvas.style.display = '';
    }
    // HF pages need the external receiver; warn when the RTL stick is active
    let warn = $('hf-warn');
    if (caps && caps.id === 'rtl0' && CFG.band[0] < 24e6) {
      if (!warn) {
        warn = document.createElement('div');
        warn.id = 'hf-warn';
        warn.textContent = 'RTL stick cannot reach this band — select the ICOM receiver on the HOME page';
        (document.querySelector('.panel-top') || panel).appendChild(warn);
      }
      warn.style.display = '';
    } else if (warn) {
      warn.style.display = 'none';
    }
  }
  sdr.on('receivers', (d) => {
    const active = (d.list || []).find(r => r.id === d.active);
    applyReceiverCaps(active || null);
    // switching receiver retunes the panel frequency; FFT only where supported
    tuneTo(freq, true);
    if (!active || active.fft !== false) sdr.fft(true);
  });
  sdr.on('status', (st) => {
    if (st.receiver) applyReceiverCaps({id: st.receiver, audio: st.audio, fft: st.fft});
  });

  // ---------- boot ----------
  buildSmeter();
  updateDisplay();
  updateScanUI();
  connUI(false);
  loadData();
  sdr.connect();
})();
