#!/usr/bin/env python3
"""Fake ICOM PCR-1000 on a PTY, for testing the SDRADIO Icom driver
without hardware. Prints the slave device path; point sdrd's
--icom-port at it.

Simulates signals: strong on 156.800 MHz, weaker on 121.500 MHz,
plus a configurable extra one via --signal FREQ_HZ.
"""
import os
import pty
import re
import select
import sys
import time


class FakePcr:
    def __init__(self, signals):
        self.signals = signals          # {freq_hz: level 0..255}
        self.freq = 0
        self.mode = 5
        self.sql_level = 64             # J41 value 0..255
        self.power = False
        self.auto = False

    def signal_here(self):
        for f, lvl in self.signals.items():
            if abs(f - self.freq) < 10_000:
                return lvl
        return 6                        # noise floor

    def squelch_open(self):
        return self.signal_here() * 2 > self.sql_level + 20

    def handle(self, cmd):
        out = []
        if cmd.startswith('H10') and len(cmd) == 4:
            self.power = cmd[3] == '1'
            out.append('H10' + cmd[3])
        elif cmd == 'H1?':
            out.append('H101' if self.power else 'H100')
        elif cmd.startswith('G3'):
            self.auto = cmd[3] == '1'
        elif cmd.startswith('G1'):
            pass                        # baud change: accept silently
        elif cmd.startswith('K0'):
            self.freq = int(cmd[2:12])
            self.mode = int(cmd[12:14])
        elif cmd.startswith('J41'):
            self.sql_level = int(cmd[3:5], 16)
        elif cmd.startswith('J40'):
            pass                        # volume
        elif cmd == 'I0?':
            out.append('I007' if self.squelch_open() else 'I004')
        elif cmd == 'I1?':
            out.append('I1%02X' % min(255, self.signal_here()))
        elif cmd == 'GD?':
            out.append('D00')
        return out


def main():
    signals = {156_800_000: 200, 121_500_000: 120}
    if '--signal' in sys.argv:
        f = int(sys.argv[sys.argv.index('--signal') + 1])
        signals[f] = 220
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    print('fake PCR1000 on %s (signals: %s)'
          % (path, {f / 1e6: l for f, l in signals.items()}), flush=True)
    radio = FakePcr(signals)
    buf = b''
    while True:
        r, _, _ = select.select([master], [], [], 1.0)
        if not r:
            continue
        try:
            data = os.read(master, 256)
        except OSError:
            break
        if not data:
            break
        buf += data
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            cmd = line.strip().decode('ascii', 'replace')
            if not cmd:
                continue
            for resp in radio.handle(cmd):
                os.write(master, (resp + '\r\n').encode('ascii'))


if __name__ == '__main__':
    main()
