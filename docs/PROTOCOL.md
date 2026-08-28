# SDRADIO Architecture & Protocol

## Components

- `bin/sdrd.py` — SDR daemon. Owns `/dev/swradio0` (in-kernel rtl2832_sdr
  driver, pure-Python V4L2 via `bin/v4l2sdr.py`). Demodulates with numpy,
  streams to browsers over WebSocket. Runs unprivileged. Start:
  `python3 bin/sdrd.py --host 0.0.0.0 --port 8765`
- `public/` — PHP web root (plain PHP, vanilla JS, **no CDNs / no build
  step** — must work offline). Serve in dev:
  `php -S 0.0.0.0:8090 -t public`
- `data/*.json` — service/channel definitions served to the UI as static
  files.

## WebSocket protocol (ws://<host>:8765)

### Client -> daemon (JSON text frames)

```json
{"cmd":"tune","freq":108027000,"mode":"wfm"}
{"cmd":"squelch","level":0.15}
{"cmd":"fft","enable":true}
{"cmd":"scan","channels":[{"f":156800000,"m":"nfm","label":"Ch 16"}],"dwell_ms":120,"hold_ms":1500,"threshold_db":12}
{"cmd":"stop_scan"}
{"cmd":"sweep","start":87500000,"stop":108000000}
{"cmd":"stop_sweep"}
{"cmd":"receivers"}
{"cmd":"receiver","id":"pcr1000"}
{"cmd":"ports"}
{"cmd":"icom_config","enable":true,"port":"/dev/ttyUSB0","model":"pcr1000"}
{"cmd":"icom_state"}
{"cmd":"power","on":false}
```

- `ports`: daemon answers `{"type":"ports","ports":["/dev/ttyUSB0",...]}`
  — serial devices that can actually be opened (includes PTYs for testing).
- `icom_config`: attach (`enable:true`, needs `port`) or detach
  (`enable:false`) the ICOM worker at runtime; the choice is persisted to
  `data/receivers.json` and restored on daemon start.
- `icom_state`: daemon answers `{"type":"icom_state","configured":true,
  "port":"...","model":"pcr1000","connected":true,"power":true}`.
- `power`: soft power switch on the attached PCR (`H101`/`H100`), works
  from standby; answered indirectly via status/icom_state updates.

- `mode`: `wfm` (broadcast FM), `nfm` (marine/land mobile), `am`
  (airband), `usb`, `lsb` (SSB, for future HF).
- `squelch.level` 0..1: 0 = always open; higher = stronger signal required.
- `scan`: daemon hops channels at `dwell_ms` each; when a channel exceeds
  `threshold_db` over the noise floor it holds and streams audio until the
  signal drops plus `hold_ms`. Minimum effective dwell 200 ms.
- `sweep`: continuously sweeps start..stop in ~1.92 MHz steps (20% overlap)
  streaming one FFT frame per step; wraps until `stop_sweep`.
- `receivers` / `receiver`: list / switch the active receiver (below).

### Receivers

The daemon always has `rtl0` (the RTL2832U SDR). Started with
`--icom-port /dev/ttyUSBx [--icom-model pcr1000|pcr1500]` it also exposes
that ICOM PCR receiver — control-only: audio comes from the radio's own
speaker, so no audio/FFT frames while it is active, and `fft`/`sweep`
commands return an error. On switch (and on `receivers`), the daemon
broadcasts:

```json
{"type":"receivers","list":[{"id":"rtl0","name":"RTL-SDR (built-in SDR)","audio":true,"fft":true},{"id":"pcr1000","name":"ICOM PCR1000 (external)","audio":false,"fft":false}],"active":"pcr1000"}
```

`status` frames carry `receiver`, `audio`, `fft` fields so the UI can adapt
(dim volume, hide waterfall) when an external receiver is active.

### Daemon -> client

JSON text:

```json
{"type":"status","freq":108027000,"mode":"wfm","squelch":0.15,"scanning":false,"sweeping":false,"rssi_db":-28.4,"sql_open":true,"scan_label":""}
{"type":"scan_hit","freq":156800000,"label":"Ch 16"}
{"type":"error","message":"..."}
```

Binary frames (first byte = type):

- `0x01` + s16le mono PCM @ 48 kHz (~655 samples per frame)
- `0x02` + header `struct '<B I f H'` = (type, center_hz uint32,
  bin_hz float32, count uint16) + `count` uint8 bins. Bin value 0..255 maps
  to -100..-20 dBFS. Bins are fftshifted (ascending frequency), spanning
  `center_hz +/- count*bin_hz/2` (span = 2.4 MHz, bin_hz = 1171.875).

## Notes

- Only one client controls the radio at a time by convention; daemon applies
  every command it receives. When the last client disconnects the daemon
  stops scan/sweep.
- The kernel DVB driver must not grab the stick for librtlsdr use — we use
  the in-kernel SDR API instead, so no blacklisting is needed.
- Tuner range: ~24-1766 MHz (R820T). HF/shortwave/AM broadcast need an
  upconverter — those menu entries are placeholders.
