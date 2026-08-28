#!/usr/bin/env python3
"""sdrd.py - SDR daemon for the SDRADIO web app.

Owns /dev/swradio0 (in-kernel rtl2832_sdr driver), demodulates in numpy,
and serves browsers over a WebSocket JSON + binary protocol.

Client -> daemon (JSON text):
  {"cmd":"tune","freq":108027000,"mode":"wfm|nfm|am|usb|lsb"}
  {"cmd":"squelch","level":0.0-1.0}        # 0 = always open
  {"cmd":"fft","enable":true}
  {"cmd":"scan","channels":[{"f":156800000,"m":"nfm","label":"Ch 16"},...],
   "dwell_ms":120,"hold_ms":1500,"threshold_db":12}
  {"cmd":"stop_scan"}
  {"cmd":"sweep","start":88000000,"stop":108000000}   # spectrum analyzer
  {"cmd":"stop_sweep"}

Daemon -> client:
  JSON: {"type":"status","freq":..,"mode":..,"rssi_db":..,"sql_open":..,
         "scanning":..,"scan_label":..,"sweeping":..}
        {"type":"scan_hit","freq":..,"label":..}
        {"type":"error","message":..}
  Binary: 0x01 + s16le mono 48 kHz audio
          0x02 + struct '<B I f H' (type, center_hz, bin_hz, count) + uint8 dB bins
"""
import argparse
import asyncio
import faulthandler
import glob
import json
import os
import queue
import signal
import struct
import sys
import threading
import time

import numpy as np
import websockets

sys.path.insert(0, '/home/jon/kimi/SDRADIO/bin')
from v4l2sdr import RtlSdr

SAMPLE_RATE = 2_400_000
AUDIO_RATE = 48_000
FFT_SIZE = 2048

FRAME_AUDIO = 0x01
FRAME_FFT = 0x02


def wsinc(cutoff, ntaps, fs):
    """Windowed-sinc lowpass FIR. cutoff in Hz."""
    m = np.arange(ntaps) - (ntaps - 1) / 2.0
    h = np.sinc(2.0 * cutoff / fs * m)
    h *= np.blackman(ntaps)
    return h / np.sum(h)


class StreamingFIR:
    """Continuous FIR across blocks (no per-block clicks)."""

    def __init__(self, taps):
        self.taps = taps.astype(np.complex128)
        self.zi = np.zeros(len(taps) - 1, dtype=np.complex128)

    def __call__(self, x):
        cat = np.concatenate([self.zi, x])
        y = np.convolve(cat, self.taps)[len(self.taps) - 1:
                                        len(self.taps) - 1 + len(x)]
        self.zi = cat[-(len(self.taps) - 1):] if len(self.taps) > 1 else self.zi
        return y


class Demod:
    """IQ @2.4 MSPS -> 48 kHz mono float audio, per mode."""

    def __init__(self, mode):
        self.mode = mode
        self.last = 0j
        self.deemph_y = 0.0
        self.dc_x = 0.0      # DC blocker state
        self.dc_y = 0.0
        if mode == 'wfm':
            self.f1 = StreamingFIR(wsinc(90e3, 51, SAMPLE_RATE))
            self.f2 = StreamingFIR(wsinc(15e3, 31, 240e3))
            self.tau = 75e-6
            self.fm_scale = 240e3 / (2 * np.pi * 75e3)
        elif mode == 'nfm':
            self.f1 = StreamingFIR(wsinc(100e3, 31, SAMPLE_RATE))
            self.f2 = StreamingFIR(wsinc(8e3, 31, 480e3))
            self.fa = StreamingFIR(wsinc(3.4e3, 31, 96e3))
            self.tau = 750e-6
            self.fm_scale = 96e3 / (2 * np.pi * 5e3)
        elif mode == 'am':
            self.f1 = StreamingFIR(wsinc(100e3, 31, SAMPLE_RATE))
            self.f2 = StreamingFIR(wsinc(6e3, 31, 480e3))
            self.fa = StreamingFIR(wsinc(4e3, 31, 96e3))
        elif mode in ('usb', 'lsb'):
            self.f1 = StreamingFIR(wsinc(100e3, 31, SAMPLE_RATE))
            # complex bandpass: shift a 1.5 kHz lowpass to +/-1.65 kHz
            lp = wsinc(1.5e3, 63, 96e3)
            m = np.arange(63)
            shift = 1.65e3 if mode == 'usb' else -1.65e3
            bp = lp * np.exp(1j * 2 * np.pi * shift / 96e3 * m)
            self.fssb = StreamingFIR(bp)
            self.f2 = StreamingFIR(wsinc(100e3, 31, SAMPLE_RATE))
        else:
            raise ValueError('unknown mode ' + mode)

    def _deemph(self, x, fs):
        # one-pole de-emphasis; scalar loop over one block is cheap
        a = 1.0 / (fs * self.tau + 1.0)
        y = np.empty_like(x)
        prev = self.deemph_y
        for i in range(len(x)):
            prev = prev + a * (x[i] - prev)
            y[i] = prev
        self.deemph_y = prev
        return y

    def _dcblock(self, x):
        # y[n] = x[n] - x[n-1] + R*y[n-1]; removes discriminator DC
        # (channel frequency offset) which would otherwise rail the clipper
        R = 0.999
        y = np.empty_like(x)
        px, py = self.dc_x, self.dc_y
        for i in range(len(x)):
            cur = x[i] - px + R * py
            px, py = x[i], cur
            y[i] = cur
        self.dc_x, self.dc_y = px, py
        return y

    def process(self, iq):
        if self.mode == 'wfm':
            f = self.f1(iq)[::10]                       # 240 kHz
            d = np.angle(f[1:] * np.conj(f[:-1])) * self.fm_scale
            au = self.f2(d.astype(np.complex128))[::5]  # 48 kHz
            au = self._dcblock(self._deemph(np.real(au), 48000.0))
            chan_power = float(np.mean(np.abs(f) ** 2))
            return au, chan_power
        f = self.f2(self.f1(iq)[::5])[::5]              # 96 kHz complex
        chan_power = float(np.mean(np.abs(f) ** 2))
        if self.mode == 'nfm':
            d = np.angle(f[1:] * np.conj(f[:-1])) * self.fm_scale
            au = np.real(self.fa(d.astype(np.complex128)))[::2]
            au = self._dcblock(self._deemph(au, 48000.0))
        elif self.mode == 'am':
            env = np.abs(f)
            au = np.real(self.fa(env.astype(np.complex128)))[::2]
            mean = np.mean(au) + 1e-9
            au = (au - mean) / mean * 0.5
        else:  # ssb
            au = np.real(self.fssb(f))[::2]
            au = au * 4.0
        return au, chan_power


class Radio:
    """Worker thread: owns the SDR, demods, scans, sweeps."""

    def __init__(self, loop, hub):
        self.loop = loop
        self.hub = hub
        self._tx = hub.broadcast        # fn(kind, payload) thread-safe
        self.active = True              # only the active receiver broadcasts
        self.cmdq = queue.Queue()
        self.freq = 107_282_000
        self.mode = 'wfm'
        self.squelch = 0.15                 # 0..1 knob
        self.fft_on = False
        self.scan = None                    # dict(channels, dwell, hold, thr, idx)
        self.sweep = None                   # dict(start, stop, cur)
        self.hold_until = 0.0
        self.noise_floor = None     # seeded from first measurement
        self.sql_open = False
        self.running = True
        self.blocks = 0
        self.last_rate_log = 0.0

    def broadcast(self, kind, payload):
        if self.active:
            self._tx(kind, payload)

    # -- commands from asyncio side ----------------------------------------
    def handle(self, msg):
        cmd = msg.get('cmd')
        if cmd == 'tune':
            self.freq = int(msg['freq'])
            self.mode = msg.get('mode', self.mode)
            self.scan = None
            self.sweep = None
            self.need_retune = True
        elif cmd == 'squelch':
            self.squelch = max(0.0, min(1.0, float(msg['level'])))
        elif cmd == 'fft':
            self.fft_on = bool(msg.get('enable'))
        elif cmd == 'scan':
            # dwell floor: retuning hammers the tuner's i2c bus; too fast
            # for too long can wedge the kernel driver's USB control pipe
            self.scan = dict(channels=msg['channels'],
                             dwell=max(200, int(msg.get('dwell_ms', 250))) / 1000.0,
                             hold=max(1, int(msg.get('hold_ms', 1500))) / 1000.0,
                             thr=float(msg.get('threshold_db', 12)),
                             idx=0, blocks_on_chan=0)
            self.sweep = None
            self.need_retune = True
        elif cmd == 'stop_scan':
            self.scan = None
        elif cmd == 'sweep':
            self.sweep = dict(start=int(msg['start']), stop=int(msg['stop']),
                              cur=int(msg['start']))
            self.scan = None
            self.fft_on = True
        elif cmd == 'stop_sweep':
            self.sweep = None

    # -- worker -------------------------------------------------------------
    def run(self):
        try:
            sdr = RtlSdr()
            sdr.set_sample_rate(SAMPLE_RATE)
            sdr.set_format(0x40000)
            sdr.set_center_freq(self.freq)
            sdr.start()
        except Exception as e:
            self.broadcast('json', {'type': 'error',
                                    'message': 'SDR open failed: %s' % e})
            return
        self.need_retune = True
        demod = Demod(self.mode)
        gen = sdr.read()

        def drain(nblocks):
            # Discard stale blocks after a retune. Never use
            # STREAMOFF/STREAMON here: rapid cycling wedges the kernel
            # driver's USB URB queue.
            for _ in range(nblocks):
                next(gen)

        try:
            while self.running:
                while True:
                    try:
                        self.handle(self.cmdq.get_nowait())
                    except queue.Empty:
                        break
                if self.need_retune or (demod.mode != self.mode):
                    sdr.set_center_freq(self.freq)
                    drain(8)                       # ~110 ms of stale data
                    demod = Demod(self.mode)
                    self.need_retune = False
                    self.noise_floor = None        # floor is per-frequency

                # sweep: one block per step
                if self.sweep:
                    sw = self.sweep
                    if sw['cur'] > sw['stop']:
                        sw['cur'] = sw['start']
                    sdr.set_center_freq(sw['cur'])
                    drain(3)
                    blk = next(gen)
                    self.blocks += 1
                    self._fft_frame(blk, sw['cur'])
                    sw['cur'] += SAMPLE_RATE * 4 // 5  # 20% overlap
                    self._status()
                    continue

                # scan: hop when dwell expired and squelch closed
                if self.scan:
                    sc = self.scan
                    ch = sc['channels'][sc['idx']]
                    if sc['blocks_on_chan'] == 0:
                        sdr.set_center_freq(ch['f'])
                        drain(3)
                        self.freq = ch['f']
                        self.mode = ch.get('m', 'nfm')
                        demod = Demod(self.mode)
                        self.noise_floor = None
                    blk = next(gen)
                    self.blocks += 1
                    sc['blocks_on_chan'] += 1
                    level = self._channel_level(blk, demod)
                    now = time.monotonic()
                    if level > sc['thr'] and now >= self.hold_until:
                        # signal found: hold and stream audio
                        self.broadcast('json', {'type': 'scan_hit',
                                                'freq': ch['f'],
                                                'label': ch.get('label', '')})
                        while self.running:
                            try:
                                m = self.cmdq.get_nowait()
                                self.handle(m)
                                if self.scan is None:
                                    break
                            except queue.Empty:
                                pass
                            blk = next(gen)
                            self.blocks += 1
                            au, p = demod.process(self._to_iq(blk))
                            lvl = float(self._db(p))
                            self._maybe_audio(au, True)
                            if self.blocks % 4 == 0:
                                self._status(extra={
                                    'rssi_db': lvl, 'sql_open': True,
                                    'scan_label': ch.get('label', '')})
                            if lvl < sc['thr']:
                                self.hold_until = time.monotonic() + sc['hold']
                                break
                    else:
                        t_block = len(blk) / 2.0 / SAMPLE_RATE
                        if sc['blocks_on_chan'] * t_block >= sc['dwell']:
                            sc['blocks_on_chan'] = 0
                            sc['idx'] = (sc['idx'] + 1) % len(sc['channels'])
                        self._status()
                    continue

                # normal tuned receive
                blk = next(gen)
                self.blocks += 1
                iq = self._to_iq(blk)
                au, p = demod.process(iq)
                lvl = self._db(p)
                # squelch with slow noise-floor tracker
                if self.noise_floor is None:
                    self.noise_floor = lvl          # seed on first block
                elif lvl < self.noise_floor:
                    self.noise_floor = lvl          # fast attack down
                else:
                    self.noise_floor += 0.002       # slow rise
                open_ = bool(self.squelch <= 0.0 or
                             lvl > self.noise_floor + self.squelch * 40.0)
                self.sql_open = open_
                self._maybe_audio(au, open_)
                if self.fft_on and self.blocks % 3 == 0:
                    self._fft_frame(blk, self.freq)
                if self.blocks % 4 == 0:
                    self._status(extra={'rssi_db': float(lvl),
                                        'sql_open': open_})
        except Exception as e:
            self.broadcast('json', {'type': 'error', 'message': str(e)})
        finally:
            sdr.close()

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _to_iq(blk):
        x = np.frombuffer(blk, np.uint8).astype(np.float32) - 127.5
        iq = (x[0::2] + 1j * x[1::2]) / 128.0
        return iq - np.mean(iq)   # remove tuner DC offset spike

    @staticmethod
    def _db(power):
        return 10.0 * np.log10(power + 1e-12)

    def _channel_level(self, blk, demod):
        iq = self._to_iq(blk)
        _, p = demod.process(iq)
        lvl = float(self._db(p))
        # scan threshold is relative to its own slow noise floor
        if self.noise_floor is None:
            self.noise_floor = lvl
        self.noise_floor = 0.98 * self.noise_floor + 0.02 * min(lvl, self.noise_floor + 6)
        return lvl - self.noise_floor

    def _maybe_audio(self, au, open_):
        if not open_:
            au = np.zeros_like(au)
        au = np.clip(au * 1.2, -1.0, 1.0)
        pcm = (au * 32767).astype('<i2').tobytes()
        self.broadcast('bin', bytes([FRAME_AUDIO]) + pcm)

    def _fft_frame(self, blk, center):
        x = np.frombuffer(blk, np.uint8).astype(np.float32) - 127.5
        iq = (x[0::2] + 1j * x[1::2]) / 128.0
        iq = iq - np.mean(iq)     # remove tuner DC offset spike
        n = len(iq) // FFT_SIZE
        if n == 0:
            return
        w = np.hanning(FFT_SIZE)
        iq = iq[:n * FFT_SIZE].reshape(n, FFT_SIZE)
        sp = np.mean(np.abs(np.fft.fftshift(
            np.fft.fft(iq * w, axis=1), axes=1)) ** 2, axis=0)
        db = 10 * np.log10(sp / FFT_SIZE ** 2 + 1e-12)
        q = np.clip((db + 100.0) * (255.0 / 80.0), 0, 255).astype(np.uint8)
        hdr = struct.pack('<BIfH', FRAME_FFT, int(center),
                          SAMPLE_RATE / FFT_SIZE, FFT_SIZE)
        self.broadcast('bin', hdr + q.tobytes())

    def _status(self, extra=None):
        st = {'type': 'status', 'freq': self.freq, 'mode': self.mode,
              'squelch': self.squelch, 'scanning': self.scan is not None,
              'sweeping': self.sweep is not None,
              'rssi_db': -120.0, 'sql_open': self.sql_open,
              'scan_label': '', 'receiver': 'rtl0',
              'audio': True, 'fft': True}
        if extra:
            st.update(extra)
        self.broadcast('json', st)


class IcomRadio:
    """Worker thread: drives an ICOM PCR receiver over serial.

    Control-only: audio comes from the receiver's own speaker, so no audio
    frames and no FFT are ever produced. Squelch runs on the radio itself;
    scan is done here by stepping channels and watching the radio's
    squelch-status responses."""

    SETTLE = 0.08     # let the radio's squelch react after a retune

    def __init__(self, loop, hub, device, model='pcr1000'):
        self.loop = loop
        self.hub = hub
        self._tx = hub.broadcast
        self.active = False
        self.cmdq = queue.Queue()
        self.device = device
        self.model = model
        self.freq = 156_800_000
        self.mode = 'nfm'
        self.squelch = 0.3
        self.scan = None
        self.hold_until = 0.0
        self.sql_open = False
        self.level = 0
        self.volume = 0.5
        self.running = True
        self.connected = False
        self.power = False
        self.power_request = None

    def broadcast(self, kind, payload):
        if self.active:
            self._tx(kind, payload)

    # -- commands ------------------------------------------------------------
    def handle(self, msg):
        cmd = msg.get('cmd')
        if cmd == 'tune':
            self.freq = int(msg['freq'])
            self.mode = msg.get('mode', self.mode)
            self.scan = None
            self.need_retune = True
        elif cmd == 'squelch':
            self.squelch = max(0.0, min(1.0, float(msg['level'])))
            self.need_squelch = True
        elif cmd == 'volume':
            self.volume = max(0.0, min(1.0, float(msg['level'])))
            self.need_volume = True
        elif cmd == 'scan':
            self.scan = dict(channels=msg['channels'],
                             dwell=max(0.1, int(msg.get('dwell_ms', 150)) / 1000.0),
                             hold=max(1, int(msg.get('hold_ms', 1500))) / 1000.0,
                             idx=0, t0=0.0, t_tune=0.0)
        elif cmd == 'stop_scan':
            self.scan = None
        elif cmd == 'power':
            self.power_request = bool(msg.get('on'))
        elif cmd in ('fft', 'sweep'):
            self.broadcast('json', {'type': 'error',
                                    'message': 'no spectrum on external receiver'})
        elif cmd == 'stop_sweep':
            pass

    # -- worker --------------------------------------------------------------
    def run(self):
        sys.path.insert(0, '/home/jon/kimi/SDRADIO/bin')
        try:
            from icom import Pcr
            radio = Pcr(self.device)
            radio.init_radio()
            radio.tune(self.freq, self.mode)
            radio.set_squelch(self.squelch)
            radio.set_volume(self.volume)
        except Exception as e:
            self._tx('json', {'type': 'error',
                              'message': 'ICOM open failed: %s' % e})
            self.connected = False
            return
        self.connected = True
        self.power = radio.power
        self.need_retune = False
        self.need_squelch = False
        last_status = 0.0
        try:
            while self.running:
                while True:
                    try:
                        self.handle(self.cmdq.get_nowait())
                    except queue.Empty:
                        break
                if self.power_request is not None:
                    try:
                        radio.set_power(self.power_request)
                    except Exception:
                        pass
                    self.power_request = None
                if getattr(self, 'need_volume', False):
                    try:
                        radio.set_volume(self.volume)
                    except Exception:
                        pass
                    self.need_volume = False
                if self.need_retune:
                    radio.tune(self.freq, self.mode)
                    self.need_retune = False
                if self.need_squelch:
                    radio.set_squelch(self.squelch)
                    self.need_squelch = False
                now = time.monotonic()

                if self.scan:
                    sc = self.scan
                    if now - sc['t0'] >= sc['dwell']:
                        sc['t0'] = now
                        sc['cur'] = sc['channels'][sc['idx']]
                        sc['idx'] = (sc['idx'] + 1) % len(sc['channels'])
                        self.freq = sc['cur']['f']
                        self.mode = sc['cur'].get('m', 'nfm')
                        radio.tune(self.freq, self.mode)
                        sc['t_tune'] = now
                    open_, lvl = radio.poll()
                    self.sql_open, self.level = open_, lvl
                    if (open_ and now >= self.hold_until
                            and now - sc['t_tune'] > self.SETTLE):
                        ch = sc['cur']
                        self.broadcast('json', {'type': 'scan_hit',
                                                'freq': ch['f'],
                                                'label': ch.get('label', '')})
                        # hold on the channel while the squelch stays open
                        while self.running:
                            try:
                                m = self.cmdq.get_nowait()
                                self.handle(m)
                                if self.scan is None:
                                    break
                            except queue.Empty:
                                pass
                            o2, l2 = radio.poll()
                            self.sql_open, self.level = o2, l2
                            self._status()
                            if not o2:
                                self.hold_until = time.monotonic() + sc['hold']
                                break
                            time.sleep(0.1)
                    self._status()
                    continue

                open_, lvl = radio.poll()
                self.sql_open, self.level = open_, lvl
                self.power = radio.power
                if now - last_status > 0.25:
                    last_status = now
                    self._status()
                time.sleep(0.05)
        except Exception as e:
            self._tx('json', {'type': 'error', 'message': 'ICOM error: %s' % e})
        finally:
            self.connected = False
            try:
                radio.close()
            except Exception:
                pass

    def _status(self):
        # raw 0..255 level -> familiar S-meter-ish dB scale
        rssi = -70.0 + self.level * (60.0 / 255.0)
        self.broadcast('json', {
            'type': 'status', 'freq': self.freq, 'mode': self.mode,
            'squelch': self.squelch, 'scanning': self.scan is not None,
            'sweeping': False, 'rssi_db': round(rssi, 1),
            'sql_open': bool(self.sql_open), 'scan_label': '',
            'receiver': self.model, 'audio': False, 'fft': False,
            'power': bool(self.power)})


class Hub:
    """Tracks connected websockets; broadcast() is thread-safe."""

    def __init__(self, loop):
        self.loop = loop
        self.clients = set()
        self.lock = threading.Lock()

    def add(self, ws):
        with self.lock:
            self.clients.add(ws)

    def remove(self, ws):
        with self.lock:
            self.clients.discard(ws)

    def broadcast(self, kind, payload):
        with self.lock:
            targets = list(self.clients)
        for ws in targets:
            data = json.dumps(payload) if kind == 'json' else payload
            asyncio.run_coroutine_threadsafe(self._send(ws, data), self.loop)

    @staticmethod
    async def _send(ws, data):
        try:
            await ws.send(data)
        except Exception:
            pass


RECEIVER_INFO = {
    'rtl0':    {'id': 'rtl0',    'name': 'RTL-SDR (built-in SDR)',
                'audio': True,  'fft': True},
    'pcr1000': {'id': 'pcr1000', 'name': 'ICOM PCR1000 (external)',
                'audio': False, 'fft': False},
    'pcr1500': {'id': 'pcr1500', 'name': 'ICOM PCR1500 (external)',
                'audio': False, 'fft': False},
}

CONFIG_PATH = '/home/jon/kimi/SDRADIO/data/receivers.json'


def list_serial_ports():
    """Serial device nodes that can actually be opened (filters the phantom
    /dev/ttyS* entries). Includes PTYs so the emulator appears for testing."""
    ports = []
    for pat in ('/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyS*', '/dev/pts/*'):
        for dev in sorted(glob.glob(pat)):
            try:
                fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                os.close(fd)
                ports.append(dev)
            except OSError:
                pass
    return ports


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print('config save failed: %s' % e, file=sys.stderr)


class Router:
    """Routes client commands to the active receiver worker."""

    def __init__(self, loop, hub, workers, active_id):
        self.loop = loop
        self.hub = hub
        self.workers = workers            # {id: Radio|IcomRadio}
        self.active_id = active_id
        for wid, w in workers.items():
            w.active = (wid == active_id)

    @property
    def active(self):
        return self.workers[self.active_id]

    def icom_worker(self):
        for wid, w in self.workers.items():
            if wid != 'rtl0':
                return wid, w
        return None, None

    def route(self, msg):
        cmd = msg.get('cmd')
        if cmd == 'receivers':
            self.broadcast_receivers()
        elif cmd == 'receiver':
            self.switch(msg.get('id'))
        elif cmd == 'ports':
            self.active._tx('json', {'type': 'ports',
                                     'ports': list_serial_ports()})
        elif cmd == 'icom_config':
            self.configure_icom(msg)
        elif cmd == 'icom_state':
            self.broadcast_icom_state()
        elif cmd == 'power':
            _, w = self.icom_worker()
            if w:
                w.cmdq.put({'cmd': 'power', 'on': bool(msg.get('on'))})
        else:
            self.active.cmdq.put(msg)

    def switch(self, wid):
        if wid not in self.workers or wid == self.active_id:
            self.broadcast_receivers()
            return
        old = self.active
        old.cmdq.put({'cmd': 'stop_scan'})
        old.cmdq.put({'cmd': 'stop_sweep'})
        old.active = False
        self.active_id = wid
        self.active.active = True
        self.broadcast_receivers()

    def configure_icom(self, msg):
        """Attach/detach the ICOM worker at runtime; persist the config."""
        enable = bool(msg.get('enable', True))
        port = msg.get('port')
        model = msg.get('model', 'pcr1000')
        if model not in ('pcr1000', 'pcr1500'):
            model = 'pcr1000'
        self.detach_icom()
        cfg = {'icom': {'enabled': False}}
        if enable and port:
            w = IcomRadio(self.loop, self.hub, port, model)
            self.workers[model] = w
            threading.Thread(target=w.run, daemon=True).start()
            cfg = {'icom': {'enabled': True, 'port': port, 'model': model}}
        save_config(cfg)
        self.broadcast_receivers()
        # state arrives once the worker has tried the port
        def delayed_state():
            time.sleep(1.0)
            self.broadcast_icom_state()
        threading.Thread(target=delayed_state, daemon=True).start()

    def detach_icom(self):
        wid, w = self.icom_worker()
        if not w:
            return
        was_active = (self.active_id == wid)
        w.running = False
        del self.workers[wid]
        if was_active:
            self.active_id = 'rtl0'
            self.workers['rtl0'].active = True

    def broadcast_receivers(self):
        self.active._tx('json', {
            'type': 'receivers',
            'list': [RECEIVER_INFO[w] for w in self.workers],
            'active': self.active_id})

    def broadcast_icom_state(self):
        wid, w = self.icom_worker()
        self.workers['rtl0']._tx('json', {
            'type': 'icom_state',
            'configured': w is not None,
            'port': w.device if w else None,
            'model': wid,
            'connected': bool(w and w.connected),
            'power': bool(w and w.power)})

    def stop_all(self):
        for w in self.workers.values():
            w.cmdq.put({'cmd': 'stop_scan'})
            w.cmdq.put({'cmd': 'stop_sweep'})


async def client_handler(ws, path, hub, router):
    hub.add(ws)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            router.route(msg)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        hub.remove(ws)
        with hub.lock:
            empty = not hub.clients
        if empty:
            router.stop_all()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--icom-port', default=None,
                    help='serial device of an ICOM PCR receiver (e.g. /dev/ttyUSB0)')
    ap.add_argument('--icom-model', default='pcr1000',
                    choices=['pcr1000', 'pcr1500'])
    args = ap.parse_args()
    faulthandler.register(signal.SIGUSR1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    hub = Hub(loop)

    workers = {'rtl0': Radio(loop, hub)}
    router = Router(loop, hub, workers, 'rtl0')
    threading.Thread(target=workers['rtl0'].run, daemon=True).start()

    # attach the ICOM from saved config; --icom-port overrides config once
    icom_cfg = (load_config().get('icom') or {})
    port = args.icom_port or (icom_cfg.get('port')
                              if icom_cfg.get('enabled') else None)
    if port:
        model = args.icom_model if args.icom_port \
            else icom_cfg.get('model', 'pcr1000')
        router.configure_icom({'enable': True, 'port': port, 'model': model})

    async def handler(ws, path=None):
        await client_handler(ws, path, hub, router)

    server = websockets.serve(handler, args.host, args.port,
                              ping_interval=20, ping_timeout=20,
                              max_queue=32)
    loop.run_until_complete(server)
    print('sdrd listening on ws://%s:%d (receivers: %s)'
          % (args.host, args.port, ', '.join(workers)))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    for w in workers.values():
        w.running = False


if __name__ == '__main__':
    main()
