"""Minimal V4L2 SDR wrapper for the in-kernel rtl2832_sdr driver (/dev/swradio0).

Pure stdlib: fcntl/ioctl + mmap. No librtlsdr, no root required (device ACL
must grant the user rw access, which udev does on this host).

Supports: format setup (CU8), RF tune / sample-rate set, manual gain control,
mmap streaming with a blocking read() generator.
"""
import os
import fcntl
import struct
import mmap
import select

# ---- ioctl encoding ------------------------------------------------------
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction, type_, nr, size):
    return (direction << 30) | (ord(type_) << 8) | nr | (size << 16)


def _IOR(t, n, s):
    return _IOC(_IOC_READ, t, n, s)


def _IOW(t, n, s):
    return _IOC(_IOC_WRITE, t, n, s)


def _IOWR(t, n, s):
    return _IOC(_IOC_READ | _IOC_WRITE, t, n, s)


VIDIOC_QUERYCAP = _IOR('V', 0, 104)
VIDIOC_ENUM_FMT = _IOWR('V', 2, 64)
VIDIOC_G_FMT = _IOWR('V', 4, 208)
VIDIOC_S_FMT = _IOWR('V', 5, 208)
VIDIOC_REQBUFS = _IOWR('V', 8, 20)
VIDIOC_QUERYBUF = _IOWR('V', 9, 88)
VIDIOC_QBUF = _IOWR('V', 15, 88)
VIDIOC_DQBUF = _IOWR('V', 17, 88)
VIDIOC_STREAMON = _IOW('V', 18, 4)
VIDIOC_STREAMOFF = _IOW('V', 19, 4)
VIDIOC_QUERYCTRL = _IOWR('V', 36, 68)
VIDIOC_G_CTRL = _IOWR('V', 27, 8)
VIDIOC_S_CTRL = _IOWR('V', 28, 8)
VIDIOC_G_FREQUENCY = _IOWR('V', 56, 44)
VIDIOC_S_FREQUENCY = _IOW('V', 57, 44)
VIDIOC_G_TUNER = _IOWR('V', 29, 84)
VIDIOC_ENUM_FREQ_BANDS = _IOWR('V', 65, 64)

V4L2_BUF_TYPE_SDR_CAPTURE = 11
V4L2_MEMORY_MMAP = 1
V4L2_TUNER_ADC = 4
V4L2_TUNER_RF = 5
V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000

FMT_CU8 = ord('C') | ord('U') << 8 | ord('0') << 16 | ord('8') << 24


class SdrError(Exception):
    pass


class RtlSdr:
    """Owns the device. One instance per daemon."""

    def __init__(self, dev=None):
        if dev is None:
            # stick may enumerate as swradio1+ after a replug
            import glob as _glob
            nodes = sorted(_glob.glob('/dev/swradio*'))
            if not nodes:
                raise SdrError('no /dev/swradio* device (stick plugged in?)')
            dev = nodes[0]
        self.dev = dev
        self.fd = os.open(dev, os.O_RDWR)
        self.buffers = []
        self.streaming = False
        self.buffersize = 0

    # -- info ---------------------------------------------------------------
    def querycap(self):
        buf = bytearray(104)
        fcntl.ioctl(self.fd, VIDIOC_QUERYCAP, buf, True)
        driver = bytes(buf[0:16]).split(b'\0')[0].decode()
        card = bytes(buf[16:48]).split(b'\0')[0].decode()
        return driver, card

    def enum_fmts(self):
        out = []
        for i in range(16):
            buf = bytearray(64)
            struct.pack_into('<II', buf, 0, i, V4L2_BUF_TYPE_SDR_CAPTURE)
            try:
                fcntl.ioctl(self.fd, VIDIOC_ENUM_FMT, buf, True)
            except OSError:
                break
            pf = struct.unpack_from('<I', buf, 44)[0]
            desc = bytes(buf[8:40]).split(b'\0')[0].decode()
            fourcc = struct.pack('<I', pf).decode('latin1')
            out.append((fourcc, desc))
        return out

    def enum_controls(self):
        """All V4L2 controls: [{id, name, min, max, step, default}]."""
        out = []
        cid = V4L2_CTRL_FLAG_NEXT_CTRL
        while True:
            buf = bytearray(68)
            struct.pack_into('<I', buf, 0, cid)
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERYCTRL, buf, True)
            except OSError:
                break
            cid, ctype = struct.unpack_from('<II', buf, 0)
            name = bytes(buf[8:40]).split(b'\0')[0].decode()
            mn, mx, step, dflt = struct.unpack_from('<iiii', buf, 40)
            out.append(dict(id=cid, name=name, min=mn, max=mx, step=step,
                            default=dflt))
            cid = (cid & ~V4L2_CTRL_FLAG_NEXT_CTRL) | V4L2_CTRL_FLAG_NEXT_CTRL
        return out

    def enum_tuners(self):
        out = []
        for i in range(4):
            buf = bytearray(84)
            struct.pack_into('<I', buf, 0, i)
            try:
                fcntl.ioctl(self.fd, VIDIOC_G_TUNER, buf, True)
            except OSError:
                break
            name = bytes(buf[4:36]).split(b'\0')[0].decode()
            ttype, cap, lo, hi = struct.unpack_from('<IIII', buf, 36)
            out.append(dict(index=i, name=name, type=ttype, cap=cap,
                            rangelow=lo, rangehigh=hi))
        return out

    # -- controls -----------------------------------------------------------
    def set_control(self, cid, value):
        buf = struct.pack('<Ii', cid, int(value))
        fcntl.ioctl(self.fd, VIDIOC_S_CTRL, buf)

    def get_control(self, cid):
        buf = bytearray(struct.pack('<Ii', cid, 0))
        fcntl.ioctl(self.fd, VIDIOC_G_CTRL, buf, True)
        return struct.unpack_from('<i', buf, 4)[0]

    # -- tuning -------------------------------------------------------------
    # Both tuners advertise V4L2_TUNER_CAP_1HZ: raw Hz units, no scaling.
    def _set_freq(self, hz, tuner, ttype):
        buf = struct.pack('<III8I', tuner, ttype, int(hz),
                          0, 0, 0, 0, 0, 0, 0, 0)
        fcntl.ioctl(self.fd, VIDIOC_S_FREQUENCY, buf)

    def _get_freq(self, tuner, ttype):
        buf = bytearray(struct.pack('<III8I', tuner, ttype, 0,
                                    0, 0, 0, 0, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, VIDIOC_G_FREQUENCY, buf, True)
        return float(struct.unpack_from('<I', buf, 8)[0])

    def set_center_freq(self, hz):
        self._set_freq(hz, tuner=1, ttype=V4L2_TUNER_RF)
        return self._get_freq(1, V4L2_TUNER_RF)

    def set_sample_rate(self, hz):
        return self._set_freq(hz, 0, V4L2_TUNER_ADC) or \
            self._get_freq(0, V4L2_TUNER_ADC)

    # -- streaming ----------------------------------------------------------
    def set_format(self, bufsize=0x40000):
        buf = bytearray(208)
        struct.pack_into('<I', buf, 0, V4L2_BUF_TYPE_SDR_CAPTURE)
        struct.pack_into('<I', buf, 4, FMT_CU8)
        struct.pack_into('<I', buf, 8, bufsize)
        fcntl.ioctl(self.fd, VIDIOC_S_FMT, buf, True)
        return bufsize  # real per-buffer length comes from QUERYBUF

    def _reqbufs(self, count):
        buf = bytearray(20)
        struct.pack_into('<III2I', buf, 0, count,
                         V4L2_BUF_TYPE_SDR_CAPTURE, V4L2_MEMORY_MMAP, 0, 0)
        fcntl.ioctl(self.fd, VIDIOC_REQBUFS, buf, True)
        return struct.unpack_from('<I', buf, 0)[0]

    def _buffer_ioctl(self, request, index):
        buf = bytearray(88)
        struct.pack_into('<I', buf, 0, index)
        struct.pack_into('<I', buf, 4, V4L2_BUF_TYPE_SDR_CAPTURE)
        struct.pack_into('<I', buf, 60, V4L2_MEMORY_MMAP)
        fcntl.ioctl(self.fd, request, buf, True)
        return buf

    def start(self, nbufs=8):
        n = self._reqbufs(nbufs)
        self.buffers = []
        for i in range(n):
            qbuf = self._buffer_ioctl(VIDIOC_QUERYBUF, i)
            offset = struct.unpack_from('<I', qbuf, 64)[0]
            length = struct.unpack_from('<I', qbuf, 72)[0]
            mm = mmap.mmap(self.fd, length, mmap.MAP_SHARED,
                           mmap.PROT_READ, offset=offset)
            self.buffers.append(mm)
            self._buffer_ioctl(VIDIOC_QBUF, i)
        fcntl.ioctl(self.fd, VIDIOC_STREAMON,
                    struct.pack('<I', V4L2_BUF_TYPE_SDR_CAPTURE))
        self.streaming = True

    def stop(self):
        if self.streaming:
            fcntl.ioctl(self.fd, VIDIOC_STREAMOFF,
                        struct.pack('<I', V4L2_BUF_TYPE_SDR_CAPTURE))
            self.streaming = False
        for mm in self.buffers:
            mm.close()
        self.buffers = []

    def read(self, timeout=1.0):
        """Generator yielding bytes blocks of interleaved uint8 IQ."""
        while self.streaming:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if not r:
                raise SdrError('read timeout')
            buf = self._buffer_ioctl(VIDIOC_DQBUF, 0)  # index filled by drv
            index = struct.unpack_from('<I', buf, 0)[0]
            used = struct.unpack_from('<I', buf, 8)[0]
            data = self.buffers[index][:used]
            self._buffer_ioctl(VIDIOC_QBUF, index)
            yield data

    def flush(self):
        """Requeue all buffers (drops stale samples after a retune)."""
        fcntl.ioctl(self.fd, VIDIOC_STREAMOFF,
                    struct.pack('<I', V4L2_BUF_TYPE_SDR_CAPTURE))
        for i in range(len(self.buffers)):
            self._buffer_ioctl(VIDIOC_QBUF, i)
        fcntl.ioctl(self.fd, VIDIOC_STREAMON,
                    struct.pack('<I', V4L2_BUF_TYPE_SDR_CAPTURE))

    def close(self):
        self.stop()
        os.close(self.fd)
