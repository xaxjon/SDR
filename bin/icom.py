"""ICOM PCR-1000 / PCR-1500 control driver (control-only, no audio).

Protocol (ASCII, CRLF-terminated, 9600 8N1 default):
  power        H10%d            (1=on 0=off; query H1?)
  auto-update  G30%d            (1: radio pushes squelch/level changes)
  tune         K0%010d%02d%02d00  (freq Hz, mode, filter)
  volume       J40%02x
  squelch      J41%02x          (0..255)
  queries      I0? squelch status -> I007 open / I004 closed
               I1? signal level  -> I1xx (hex 00..FF)

Modes:  0=LSB 1=USB 2=AM 3=CW 5=NFM 6=WFM
Filters: 0=2.8k 1=6k 2=15k 3=50k 4=230k

PCR-1500 uses the same command family; audio (if ever wanted) is a
separate USB sound device, not this serial link.
"""
import re
import select
import threading
import time

from serialport import SerialPort, SerialError

MODES = {'lsb': 0, 'usb': 1, 'am': 2, 'cw': 3, 'nfm': 5, 'wfm': 6}
# sensible default IF filter per mode
FILTERS = {'lsb': 0, 'usb': 0, 'am': 2, 'cw': 0, 'nfm': 2, 'wfm': 4}


class PcrError(Exception):
    pass


class Pcr:
    """Control channel to one PCR receiver. Thread-safe."""

    def __init__(self, device, baud=9600):
        self.lock = threading.Lock()
        try:
            self.port = SerialPort(device, baud)
        except SerialError as e:
            raise PcrError(str(e))
        self.buf = ''
        self.squelch_open = False
        self.level = 0            # 0..255 raw signal level
        self.freq = 156_800_000
        self.mode = 'nfm'

    # -- low level -----------------------------------------------------------
    def _write(self, cmd):
        self.port.write((cmd + '\r\n').encode('ascii'))

    def pump(self):
        """Read available bytes, update squelch/level state."""
        while True:
            r, _, _ = select.select([self.port.fileno()], [], [], 0)
            if not r:
                return
            data = self.port.read()
            if not data:
                return
            self.buf += data.decode('ascii', 'replace')
            self._parse()

    def _parse(self):
        # frames are 4 chars; resync by scanning for known frame starts
        while len(self.buf) >= 4:
            m = re.match(r'(I[0-3][0-9A-F]{2}|H10[01]|G[0-9][0-9].|D0[01])',
                         self.buf)
            if not m:
                self.buf = self.buf[1:]
                continue
            frame, self.buf = self.buf[:4], self.buf[4:]
            if frame[0] == 'I':
                if frame[1] == '0':
                    self.squelch_open = frame[2:4] == '07'   # I007=open I004=closed
                elif frame[1] == '1':
                    try:
                        self.level = int(frame[2:4], 16)
                    except ValueError:
                        pass

    # -- commands ------------------------------------------------------------
    def init_radio(self):
        with self.lock:
            self._write('H101')          # power on
            time.sleep(0.05)
            self._write('G301')          # auto-update squelch/level
            time.sleep(0.05)
            self.pump()

    def tune(self, freq_hz, mode=None):
        mode = mode or self.mode
        if mode not in MODES:
            raise PcrError('bad mode %r' % mode)
        with self.lock:
            self.freq, self.mode = int(freq_hz), mode
            self._write('K0%010d%02d%02d00'
                        % (self.freq, MODES[mode], FILTERS[mode]))
            self.pump()

    def set_squelch(self, level):       # 0.0..1.0
        with self.lock:
            self._write('J41%02x' % max(0, min(255, int(level * 255))))
            self.pump()

    def set_volume(self, level):        # 0.0..1.0
        with self.lock:
            self._write('J40%02x' % max(0, min(255, int(level * 255))))
            self.pump()

    def poll(self):
        """Query squelch status + signal level. Call ~5x/s."""
        with self.lock:
            self._write('I0?')
            self._write('I1?')
            time.sleep(0.02)
            self.pump()
        return self.squelch_open, self.level

    def close(self):
        try:
            with self.lock:
                self._write('H100')      # power off
        finally:
            self.port.close()
