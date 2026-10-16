import socket
import time
import unittest
from vimar_video import read_exact
from vimar_watch import ProtocolError


class MediaReadTests(unittest.TestCase):
    def test_packet_boundaries_preserved(self):
        left, right = socket.socketpair()
        try:
            left.sendall(b'abc')
            left.sendall(b'defgh')
            self.assertEqual(read_exact(right, 5, time.monotonic() + 1), b'abcde')
            self.assertEqual(read_exact(right, 3, time.monotonic() + 1), b'fgh')
        finally:
            left.close()
            right.close()

    def test_eof_mid_packet_is_an_error(self):
        left, right = socket.socketpair()
        try:
            left.sendall(b'ab')
            left.close()
            with self.assertRaises(ConnectionError):
                read_exact(right, 4, time.monotonic() + 1)
        finally:
            left.close()
            right.close()

    def test_length_limit_before_read(self):
        with self.assertRaises(ProtocolError):
            read_exact(None, 2_000_001, time.monotonic() + 1)

    def test_expired_deadline(self):
        with self.assertRaises(TimeoutError):
            read_exact(None, 32, time.monotonic() - 1)


if __name__ == '__main__':
    unittest.main()
