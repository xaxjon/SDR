#!/usr/bin/env python3
"""Probe /dev/swradio0: enumerate, tune, capture a few IQ blocks, report power."""
import sys
import numpy as np

sys.path.insert(0, '/home/jon/kimi/SDRADIO/bin')
from v4l2sdr import RtlSdr

sdr = RtlSdr()
print('caps:', sdr.querycap())
print('formats:', sdr.enum_fmts())
print('tuners:')
for t in sdr.enum_tuners():
    print('  ', t)
print('controls:')
ctrls = sdr.enum_controls()
for c in ctrls:
    print('  id=0x%08x %-28s min=%d max=%d step=%d default=%d' % (
        c['id'], c['name'], c['min'], c['max'], c['step'], c['default']))

sr = sdr.set_sample_rate(2_400_000)
print('sample rate set/readback: 2400000 ->', sr)
cf = sdr.set_center_freq(100_000_000)
print('center freq set/readback: 100000000 ->', cf)
bs = sdr.set_format(0x40000)
print('buffersize:', bs)

sdr.start()
total = 0.0
n = 0
for blk in sdr.read():
    iq_u8 = np.frombuffer(blk, dtype=np.uint8).astype(np.float32)
    iq_u8 -= 127.5
    iq = iq_u8[0::2] + 1j * iq_u8[1::2]
    p = float(np.mean(np.abs(iq) ** 2))
    total += p
    n += 1
    if n >= 10:
        break
sdr.stop()
print('mean |iq|^2 over %d blocks: %.4f (rms %.2f of 128 full-scale)' % (
    n, total / max(n, 1), (total / max(n, 1)) ** 0.5))
print('PROBE OK')
sdr.close()
