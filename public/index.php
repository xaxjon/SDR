<?php
/* SDRADIO main menu — equipment rack look. */
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
      <select id="rx-select" class="rx-select" style="display:none" title="Active receiver"></select>
    </div>
  </div>

  <?php foreach ($entries as $e): ?>
    <?php if ($e[3]): ?>
      <a class="rack-unit" href="<?= htmlspecialchars($e[1]) ?>">
        <span class="ru-name"><?= htmlspecialchars($e[0]) ?></span>
        <span class="ru-desc"><?= htmlspecialchars($e[2]) ?></span>
        <span class="ru-go">SELECT ▸</span>
      </a>
    <?php else: ?>
      <div class="rack-unit disabled">
        <span class="ru-name"><?= htmlspecialchars($e[0]) ?></span>
        <span class="ru-desc"><?= htmlspecialchars($e[2]) ?></span>
        <span class="ru-go">N/A</span>
      </div>
    <?php endif; ?>
  <?php endforeach; ?>

  <div class="rack-footer">
    RTL2832 · 24–1766 MHz · ws://<span id="ws-host"></span>:8765
  </div>
</div>

<script>
/* daemon liveness ping: try opening the control WebSocket */
(function () {
  var led = document.getElementById('conn-led');
  var txt = document.getElementById('conn-text');
  document.getElementById('ws-host').textContent = location.hostname;
  function ping() {
    var done = false;
    var ws;
    try { ws = new WebSocket('ws://' + location.hostname + ':8765'); }
    catch (e) { set(false); return; }
    function set(up) {
      if (done) return; done = true;
      led.classList.toggle('on', up);
      txt.textContent = up ? 'DAEMON ONLINE' : 'DAEMON OFFLINE';
      try { ws.close(); } catch (e) {}
      setTimeout(ping, 5000);
    }
    ws.onopen = function () {
      // keep the socket briefly to fetch the receiver list
      ws.onmessage = function (ev) {
        try {
          var d = JSON.parse(ev.data);
          if (d.type === 'receivers') {
            var sel = document.getElementById('rx-select');
            if (d.list.length > 1) {
              sel.innerHTML = '';
              d.list.forEach(function (r) {
                var o = document.createElement('option');
                o.value = r.id; o.textContent = r.name;
                if (r.id === d.active) o.selected = true;
                sel.appendChild(o);
              });
              sel.style.display = '';
              sel.onchange = function () {
                var ws2 = new WebSocket('ws://' + location.hostname + ':8765');
                ws2.onopen = function () {
                  ws2.send(JSON.stringify({cmd: 'receiver', id: sel.value}));
                  setTimeout(function () { ws2.close(); }, 300);
                };
              };
            }
          }
        } catch (e) {}
      };
      ws.send(JSON.stringify({cmd: 'receivers'}));
      set(true);
    };
    ws.onerror = function () { set(false); };
    ws.onclose = function () { set(false); };
  }
  ping();
})();
</script>
</body>
</html>
