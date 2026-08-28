<?php
/* SDRADIO spectrum analyzer — wideband sweep, waterfall + trace, peak find. */
require __DIR__ . '/bootstrap.php';
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDRADIO — Spectrum Analyzer</title>
<link rel="stylesheet" href="assets/css/base.css">
<link rel="stylesheet" href="assets/css/skin-analyzer.css">
</head>
<body class="skin-analyzer">

<div class="panel wide">
  <div class="panel-top">
    <div class="brandplate"><span class="brand">SDRADIO</span><span class="model">XA-SWEEP</span></div>
    <div class="pagetitle">SPECTRUM ANALYZER</div>
    <div class="connbox"><span id="conn-led" class="led"></span><span id="conn-text">OFFLINE</span></div>
    <a class="homebtn" href="index.php">⌂ HOME</a>
  </div>

  <div class="analyzer-controls">
    <div class="field">
      <label for="sw-start">START (MHz)</label>
      <input id="sw-start" type="text" inputmode="decimal" value="88">
    </div>
    <div class="field">
      <label for="sw-stop">STOP (MHz)</label>
      <input id="sw-stop" type="text" inputmode="decimal" value="108">
    </div>
    <button id="sweep-btn" class="pbtn big">START SWEEP</button>
    <button id="reset-max" class="pbtn">RESET MAX</button>
    <span id="sweep-state" class="analyzer-state">IDLE</span>
  </div>

  <div class="trace-wrap">
    <canvas id="trace"></canvas>
    <button id="tune-here" class="pbtn tune-here hidden">TUNE HERE</button>
  </div>

  <canvas id="wf-big" class="wf-big"></canvas>

  <div class="analyzer-hint">
    Click the trace to mark a frequency (or click a ▲ peak) → TUNE HERE opens a receiver there.
  </div>
</div>

<script src="assets/js/sdr.js"></script>
<script src="assets/js/waterfall.js"></script>
<script>
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };

  var trace = new Trace($('trace'));
  var wf = new Waterfall($('wf-big'));
  var sdr = new SDRClient();
  var sweeping = false;
  var startHz = 88e6, stopHz = 108e6;
  var COLS = 1600;
  var lastCenter = 0;

  /* pick the receiver profile whose band contains f (for TUNE HERE) */
  function svcFor(f) {
    if (f >= 87.5e6 && f <= 108e6)    return 'fm';
    if (f >= 118e6 && f <= 136.975e6) return 'airband';
    if (f >= 156e6 && f <= 162.6e6)   return 'marine';
    if (f >= 150e6 && f <= 174e6)     return 'vhf';
    return null;
  }

  function connUI(up) {
    $('conn-led').classList.toggle('on', up);
    $('conn-text').textContent = up ? 'ONLINE' : 'OFFLINE';
  }

  function setSweeping(on) {
    sweeping = on;
    $('sweep-btn').textContent = on ? 'STOP SWEEP' : 'START SWEEP';
    $('sweep-btn').classList.toggle('active', on);
    $('sweep-state').textContent = on ? 'SWEEPING' : 'IDLE';
  }

  function startSweep() {
    var a = parseFloat($('sw-start').value);
    var b = parseFloat($('sw-stop').value);
    if (isNaN(a) || isNaN(b) || a >= b || a < 24 || b > 1766) {
      $('sweep-state').textContent = 'BAD RANGE (24–1766 MHz)';
      return;
    }
    startHz = a * 1e6; stopHz = b * 1e6;
    trace.configure(startHz, stopHz, COLS);
    wf.clear();
    hideTune();
    sdr.sweep(startHz, stopHz);
    setSweeping(true);
  }

  $('sweep-btn').addEventListener('click', function () {
    if (sweeping) { sdr.stopSweep(); setSweeping(false); }
    else startSweep();
  });
  $('reset-max').addEventListener('click', function () { trace.resetMaxHold(); });

  /* TUNE HERE */
  var tuneFreq = null;
  function hideTune() { $('tune-here').classList.add('hidden'); tuneFreq = null; trace.selected = null; }
  function showTune(f) {
    var svc = svcFor(f);
    if (!svc) { $('sweep-state').textContent = 'NO RECEIVER BAND FOR ' + (f / 1e6).toFixed(3) + ' MHz'; return; }
    tuneFreq = f;
    trace.selected = f;
    var btn = $('tune-here');
    btn.textContent = 'TUNE HERE · ' + (f / 1e6).toFixed(4) + ' MHz (' + svc.toUpperCase() + ')';
    btn.classList.remove('hidden');
    trace.render(true);
  }
  $('tune-here').addEventListener('click', function () {
    if (!tuneFreq) return;
    location.href = 'radio.php?svc=' + svcFor(tuneFreq) + '&freq=' + Math.round(tuneFreq);
  });
  $('trace').addEventListener('click', function (e) {
    if (!trace.cur) return;
    var r = e.currentTarget.getBoundingClientRect();
    var frac = (e.clientX - r.left) / r.width;
    var peak = trace.peakNear(frac, 0.015);
    showTune(peak ? peak.freq : trace.freqAt(frac));
  });

  sdr.on('connect', function () {
    connUI(true);
    if (sweeping) sdr.sweep(startHz, stopHz); // re-arm after reconnect
  });
  sdr.on('disconnect', function () { connUI(false); setSweeping(false); });
  sdr.on('fft', function (f) {
    if (!sweeping || !trace.cur) return;
    if (f.centerHz < startHz - 3e6 || f.centerHz > stopHz + 3e6) return; // stray frame
    trace.ingest(f.centerHz, f.binHz, f.bins);
    if (lastCenter && f.centerHz < lastCenter - 1e6) trace.detectPeaks(10); // sweep wrapped
    lastCenter = f.centerHz;
    trace.render(false);
    wf.pushRow(trace.waterfallRow(wf.cv.clientWidth || 1000));
  });
  sdr.on('proto_error', function (e) {
    $('sweep-state').textContent = 'ERROR: ' + (e.message || '?');
  });

  connUI(false);
  sdr.connect();
})();
</script>
</body>
</html>
