<?php
/* SDRADIO main menu — equipment rack look + receiver settings panel. */
require __DIR__ . '/bootstrap.php';
$entries = [
    ['AIRBAND RECEIVER',   'radio.php?svc=airband', '118.000 – 136.975 MHz · AM · channel scan', true],
    ['MARINE VHF',         'radio.php?svc=marine',  '156 – 162 MHz · NFM · regional channel sets', true],
    ['COMMERCIAL VHF',     'radio.php?svc=vhf',     '150 – 174 MHz · NFM · land mobile', true],
    ['FM BROADCAST',       'radio.php?svc=fm',      '87.5 – 108 MHz · WFM stereo band', true],
    ['SPECTRUM ANALYZER',  'spectrum.php',          'wideband sweep · waterfall · peak find', true],
    ['SHORTWAVE',          'radio.php?svc=sw',      '3 – 26 MHz · AM · needs external receiver', true],
    ['HAM SSB',            'radio.php?svc=ham',     '160 – 10 m · SSB · needs external receiver', true],
    ['MARINE / AIR HF',    'radio.php?svc=marinehf','2 – 25 MHz · USB · needs external receiver', true],
];
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDRADIO — Main Menu</title>
<link rel="stylesheet" href="assets/css/base.css">
</head>
<body class="skin-index">

<div class="rack">
  <div class="rack-ear left"></div>
  <div class="rack-ear right"></div>

  <div class="rack-header">
    <div class="brandplate big"><span class="brand">SDRADIO</span><span class="model">SOFTWARE-DEFINED RADIO CONSOLE</span></div>
    <div class="connbox">
      <span id="conn-led" class="led"></span>
      <span id="conn-text">CHECKING…</span>
    </div>
  </div>

  <?php foreach ($entries as $e): ?>
    <a class="rack-unit" href="<?= htmlspecialchars($e[1]) ?>">
      <span class="ru-name"><?= htmlspecialchars($e[0]) ?></span>
      <span class="ru-desc"><?= htmlspecialchars($e[2]) ?></span>
      <span class="ru-go">SELECT ▸</span>
    </a>
  <?php endforeach; ?>

  <div class="rack-unit settings-unit">
    <div class="settings-title">RECEIVER SETTINGS</div>

    <div class="setrow">
      <label for="rx-select">ACTIVE RECEIVER</label>
      <select id="rx-select" class="rx-select"><option>rtl0</option></select>
    </div>

    <div class="setrow">
      <label for="model-select">EXTERNAL RECEIVER</label>
      <select id="model-select" class="rx-select">
        <option value="pcr1000">ICOM PCR1000</option>
        <option value="pcr1500">ICOM PCR1500</option>
      </select>
    </div>

    <div class="setrow">
      <label for="port-select">COM PORT</label>
      <select id="port-select" class="rx-select wide"></select>
      <button id="ports-refresh" class="sbtn" title="Re-detect serial ports">⟳</button>
    </div>

    <div class="setrow">
      <label></label>
      <button id="icom-apply" class="sbtn wide-btn">CONNECT</button>
      <button id="icom-remove" class="sbtn danger">REMOVE</button>
      <span id="icom-state" class="setstate">checking…</span>
    </div>

    <div class="setrow" id="pwr-row" style="display:none">
      <label>PCR POWER</label>
      <button id="pwr-btn" class="sbtn power-btn">…</button>
    </div>
  </div>

  <div class="rack-footer">
    RTL2832 · 24–1766 MHz · ws://<span id="ws-host"></span>:8765
  </div>
</div>

<script>
/* Settings controller: one persistent WebSocket driving the settings panel. */
(function () {
  'use strict';
  var led = document.getElementById('conn-led');
  var txt = document.getElementById('conn-text');
  var rxSel = document.getElementById('rx-select');
  var modelSel = document.getElementById('model-select');
  var portSel = document.getElementById('port-select');
  var stateEl = document.getElementById('icom-state');
  var pwrRow = document.getElementById('pwr-row');
  var pwrBtn = document.getElementById('pwr-btn');
  var ws = null;
  var configured = false;

  document.getElementById('ws-host').textContent = location.hostname;

  function send(o) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(o));
  }

  function setOnline(up) {
    led.classList.toggle('on', up);
    txt.textContent = up ? 'DAEMON ONLINE' : 'DAEMON OFFLINE';
  }

  function fillPorts(list, keep) {
    portSel.innerHTML = '';
    if (!list.length) {
      var o = document.createElement('option');
      o.value = ''; o.textContent = '(no serial ports found)';
      portSel.appendChild(o);
      return;
    }
    list.forEach(function (p) {
      var o = document.createElement('option');
      o.value = p; o.textContent = p;
      if (p === keep) o.selected = true;
      portSel.appendChild(o);
    });
  }

  function connect() {
    try { ws = new WebSocket('ws://' + location.hostname + ':8765'); }
    catch (e) { setOnline(false); setTimeout(connect, 5000); return; }
    ws.onopen = function () {
      setOnline(true);
      send({cmd: 'receivers'});
      send({cmd: 'ports'});
      send({cmd: 'icom_state'});
    };
    ws.onclose = function () { setOnline(false); setTimeout(connect, 5000); };
    ws.onerror = function () {};
    ws.onmessage = function (ev) {
      var d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === 'receivers') {
        rxSel.innerHTML = '';
        d.list.forEach(function (r) {
          var o = document.createElement('option');
          o.value = r.id; o.textContent = r.name;
          if (r.id === d.active) o.selected = true;
          rxSel.appendChild(o);
        });
      } else if (d.type === 'ports') {
        fillPorts(d.ports, portSel.value || undefined);
      } else if (d.type === 'icom_state') {
        configured = d.configured;
        if (d.configured) {
          if (d.model) modelSel.value = d.model;
          if (d.port) {
            var found = false;
            for (var i = 0; i < portSel.options.length; i++)
              if (portSel.options[i].value === d.port) { found = true; break; }
            if (!found) {
              var o = document.createElement('option');
              o.value = d.port; o.textContent = d.port;
              portSel.appendChild(o);
            }
            portSel.value = d.port;
          }
          stateEl.textContent = d.port + ' — ' +
            (d.connected ? 'CONNECTED' : 'NOT RESPONDING');
          stateEl.className = 'setstate ' + (d.connected ? 'ok' : 'bad');
          pwrRow.style.display = '';
          pwrBtn.textContent = d.power ? 'POWER: ON' : 'POWER: OFF';
          pwrBtn.classList.toggle('on', !!d.power);
        } else {
          stateEl.textContent = 'not configured';
          stateEl.className = 'setstate';
          pwrRow.style.display = 'none';
        }
      }
    };
  }

  rxSel.addEventListener('change', function () {
    send({cmd: 'receiver', id: rxSel.value});
  });
  document.getElementById('ports-refresh').addEventListener('click', function () {
    send({cmd: 'ports'});
  });
  document.getElementById('icom-apply').addEventListener('click', function () {
    if (!portSel.value) return;
    stateEl.textContent = portSel.value + ' — connecting…';
    stateEl.className = 'setstate';
    send({cmd: 'icom_config', enable: true,
          port: portSel.value, model: modelSel.value});
    setTimeout(function () { send({cmd: 'icom_state'}); }, 1500);
    setTimeout(function () { send({cmd: 'receivers'}); }, 1600);
  });
  document.getElementById('icom-remove').addEventListener('click', function () {
    send({cmd: 'icom_config', enable: false});
    setTimeout(function () { send({cmd: 'icom_state'}); }, 600);
    setTimeout(function () { send({cmd: 'receivers'}); }, 700);
  });
  pwrBtn.addEventListener('click', function () {
    var on = !pwrBtn.classList.contains('on');
    send({cmd: 'power', on: on});
    setTimeout(function () { send({cmd: 'icom_state'}); }, 600);
  });

  connect();
})();
</script>
</body>
</html>
