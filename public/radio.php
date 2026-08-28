<?php
/* SDRADIO receiver panel. One page, per-service config array. */
require __DIR__ . '/bootstrap.php';
$SERVICES = [
    'airband' => [
        'title'    => 'AIRBAND RECEIVER',
        'model'    => 'XA-118A',
        'mode'     => 'am',
        'skin'     => 'skin-airband',
        'data'     => 'data/airband.json',
        'band'     => [118000000, 136975000],
        'step'     => 25000,
        'decimals' => 3,
        'knobSpan' => 1000000,
        'defaultFreq' => 121500000,
        'features' => ['presets' => true, 'spacing' => true],
    ],
    'marine' => [
        'title'    => 'MARINE VHF',
        'model'    => 'XA-156M',
        'mode'     => 'nfm',
        'skin'     => 'skin-marine',
        'data'     => 'data/marine_channels.json',
        'band'     => [156000000, 163000000],
        'step'     => 25000,
        'decimals' => 3,
        'knobSpan' => 1000000,
        'defaultFreq' => 156800000,
        'features' => ['channels' => true, 'region' => true],
    ],
    'fm' => [
        'title'    => 'FM BROADCAST',
        'model'    => 'XA-108F',
        'mode'     => 'wfm',
        'skin'     => 'skin-fm',
        'data'     => 'data/fm_broadcast.json',
        'band'     => [87500000, 108000000],
        'step'     => 100000,
        'decimals' => 1,
        'knobSpan' => 2000000,
        'defaultFreq' => 107282000,
        'features' => ['presets' => true],
    ],
    'vhf' => [
        'title'    => 'COMMERCIAL VHF',
        'model'    => 'XA-160C',
        'mode'     => 'nfm',
        'skin'     => 'skin-vhf',
        'data'     => 'data/vhf_commercial.json',
        'band'     => [150000000, 174000000],
        'step'     => 12500,
        'decimals' => 4,
        'knobSpan' => 1000000,
        'defaultFreq' => 154600000,
        'features' => ['presets' => true],
    ],
    'sw' => [
        'title'    => 'SHORTWAVE RECEIVER',
        'model'    => 'XA-31S',
        'mode'     => 'am',
        'skin'     => 'skin-airband',
        'data'     => 'data/shortwave.json',
        'band'     => [3000000, 26100000],
        'step'     => 5000,
        'decimals' => 3,
        'knobSpan' => 100000,
        'defaultFreq' => 10000000,
        'features' => ['presets' => true, 'modes' => true],
    ],
    'ham' => [
        'title'    => 'HAM SSB RECEIVER',
        'model'    => 'XA-14H',
        'mode'     => 'usb',
        'skin'     => 'skin-vhf',
        'data'     => 'data/ham.json',
        'band'     => [1800000, 29700000],
        'step'     => 1000,
        'decimals' => 4,
        'knobSpan' => 100000,
        'defaultFreq' => 14200000,
        'features' => ['presets' => true, 'modes' => true],
    ],
    'marinehf' => [
        'title'    => 'MARINE / AIR HF',
        'model'    => 'XA-22M',
        'mode'     => 'usb',
        'skin'     => 'skin-marine',
        'data'     => 'data/marinehf.json',
        'band'     => [2000000, 26100000],
        'step'     => 1000,
        'decimals' => 4,
        'knobSpan' => 100000,
        'defaultFreq' => 8294000,
        'features' => ['presets' => true, 'modes' => true],
    ],
];

$svc = isset($_GET['svc']) ? (string)$_GET['svc'] : '';
if (!isset($SERVICES[$svc])) {
    header('Location: index.php');
    exit;
}
$cfg = $SERVICES[$svc];
$feat = $cfg['features'] + ['presets' => false, 'spacing' => false, 'channels' => false, 'region' => false, 'modes' => false];

$initialFreq = null;
if (isset($_GET['freq'])) {
    // accept anything the tuner can physically reach (24-1766 MHz);
    // the service band is only the default scan range
    $f = (int)$_GET['freq'];
    if ($f >= 24000000 && $f <= 1766000000) $initialFreq = $f;
}

$jsConfig = [
    'svc'         => $svc,
    'mode'        => $cfg['mode'],
    'dataUrl'     => $cfg['data'],
    'band'        => $cfg['band'],
    'step'        => $cfg['step'],
    'decimals'    => $cfg['decimals'],
    'knobSpan'    => $cfg['knobSpan'],
    'initialFreq' => $initialFreq,
    'defaultFreq' => $cfg['defaultFreq'],
];
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDRADIO — <?= htmlspecialchars($cfg['title']) ?></title>
<link rel="stylesheet" href="assets/css/base.css">
<link rel="stylesheet" href="assets/css/<?= htmlspecialchars($cfg['skin']) ?>.css">
</head>
<body class="<?= htmlspecialchars($cfg['skin']) ?>">

<div class="panel">
  <div class="panel-top">
    <div class="brandplate"><span class="brand">SDRADIO</span><span class="model"><?= htmlspecialchars($cfg['model']) ?></span></div>
    <div class="pagetitle"><?= htmlspecialchars($cfg['title']) ?></div>
    <div class="connbox"><span id="conn-led" class="led"></span><span id="conn-text">OFFLINE</span>
      <select id="rx-switch" class="rx-select" style="display:none" title="Active receiver"></select>
    </div>
    <a class="homebtn" href="index.php">⌂ HOME</a>
  </div>

  <div class="lcd-wrap">
    <div class="lcd">
      <div class="lcd-row">
        <div class="lcd-left">
          <div id="lcd-main">---</div>
          <div id="lcd-sub"><?= strtoupper($cfg['mode']) ?> · MHz</div>
        </div>
        <div class="lcd-right">
          <div class="lcd-flags">
            <span id="flag-sql" class="lcd-flag">SQL</span>
            <span id="flag-mode" class="lcd-flag static"><?= strtoupper($cfg['mode']) ?></span>
          </div>
          <div class="smeter" id="smeter"></div>
        </div>
      </div>
    </div>
    <div id="scan-banner" class="scan-banner hidden"></div>
  </div>

  <canvas id="wf" class="wf-strip" title="Click to tune"></canvas>

  <?php if ($feat['modes']): ?>
  <div class="mode-row" id="mode-row">
    <button class="pbtn mode-btn" data-mode="lsb">LSB</button>
    <button class="pbtn mode-btn" data-mode="usb">USB</button>
    <button class="pbtn mode-btn" data-mode="cw">CW</button>
    <button class="pbtn mode-btn" data-mode="am">AM</button>
    <button class="pbtn mode-btn" data-mode="amw">AM WIDE</button>
  </div>
  <?php endif; ?>

  <div class="controls">
    <div class="ctl-col knob-col">
      <div class="knob-wrap">
        <div class="knob-ticks"></div>
        <div id="knob" class="knob"></div>
      </div>
      <div class="ctl-label">TUNE</div>
      <div class="btnrow">
        <button id="tune-dn" class="pbtn arrow">▼</button>
        <button id="tune-up" class="pbtn arrow">▲</button>
      </div>
      <?php if ($feat['channels']): ?>
      <div class="btnrow">
        <button id="ch-dn" class="pbtn">CH−</button>
        <button id="ch-up" class="pbtn">CH+</button>
      </div>
      <?php endif; ?>
    </div>

    <div class="ctl-col sliders-col">
      <div class="slider-block">
        <label for="vol">VOLUME</label>
        <input type="range" id="vol" min="0" max="1" step="0.01" value="0.7">
      </div>
      <div class="slider-block">
        <label for="sql">SQUELCH <span id="sql-led" class="led small"></span></label>
        <input type="range" id="sql" min="0" max="1" step="0.01" value="0.15">
      </div>
      <?php if ($feat['spacing']): ?>
      <div class="spacing-block">
        <div class="ctl-label" id="step-label">STEP 25 kHz</div>
        <div id="spacing-toggle" class="btnrow">
          <button class="pbtn" data-spacing="25000">25 kHz</button>
          <button class="pbtn" data-spacing="8333">8.33 kHz</button>
        </div>
      </div>
      <?php else: ?>
      <div class="ctl-label" id="step-label">STEP <?= rtrim(rtrim(number_format($cfg['step'] / 1000, 3), '0'), '.') ?> kHz</div>
      <?php endif; ?>
      <div class="scan-block">
        <button id="scan-btn" class="pbtn big scan">SCAN</button>
        <span id="led-scan" class="led"></span>
        <?php if ($feat['region']): ?>
        <select id="region" class="region-sel"></select>
        <?php endif; ?>
      </div>
    </div>

    <div class="ctl-col keypad-col">
      <div id="keypad" class="keypad">
        <button class="kbtn" data-k="7">7</button>
        <button class="kbtn" data-k="8">8</button>
        <button class="kbtn" data-k="9">9</button>
        <button class="kbtn" data-k="4">4</button>
        <button class="kbtn" data-k="5">5</button>
        <button class="kbtn" data-k="6">6</button>
        <button class="kbtn" data-k="1">1</button>
        <button class="kbtn" data-k="2">2</button>
        <button class="kbtn" data-k="3">3</button>
        <button class="kbtn" data-k="0">0</button>
        <button class="kbtn" data-k=".">·</button>
        <button class="kbtn func" data-k="CLR">CLR</button>
        <button class="kbtn func wide" data-k="ENT">ENTER</button>
      </div>
      <div class="ctl-label">DIRECT ENTRY (MHz)</div>
    </div>
  </div>

  <?php if ($feat['presets']): ?>
  <div class="ctl-label presets-label">PRESETS</div>
  <div id="preset-row" class="preset-row"></div>
  <?php endif; ?>
</div>

<script>
window.RADIO_CONFIG = <?= json_encode($jsConfig, JSON_UNESCAPED_SLASHES) ?>;
</script>
<script src="assets/js/sdr.js"></script>
<script src="assets/js/audio.js"></script>
<script src="assets/js/waterfall.js"></script>
<script src="assets/js/radio.js"></script>
</body>
</html>
