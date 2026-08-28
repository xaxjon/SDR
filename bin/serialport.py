"""Minimal stdlib serial port wrapper (no pyserial dependency).

Just what the ICOM PCR driver needs: open, 9600 8N1, read with timeout,
write bytes. Uses termios directly.
"""
import os
import termios


class SerialError(Exception):
    pass


class SerialPort:
    def __init__(self, device, baud=9600, timeout=0.1):
        self.device = device
        try:
            self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise SerialError('cannot open %s: %s' % (device, e))
        attrs = termios.tcgetattr(self.fd)
        speed = {9600: termios.B9600, 19200: termios.B19200,
                 38400: termios.B38400, 4800: termios.B4800,
                 300: termios.B300, 1200: termios.B1200}[baud]
        # raw 8N1
        attrs[0] = 0                                   # iflag
        attrs[1] = 0                                   # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
        attrs[3] = 0                                   # lflag
        attrs[4] = speed                               # ispeed
        attrs[5] = speed                               # ospeed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = int(timeout * 10)
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def write(self, data: bytes):
        return os.write(self.fd, data)

    def read(self, n=256) -> bytes:
        try:
            return os.read(self.fd, n)
        except BlockingIOError:
            return b''

    def fileno(self):
        return self.fd

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass
