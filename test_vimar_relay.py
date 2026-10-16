import base64
import unittest
from unittest.mock import Mock, patch
from vimar_relay import main, open_relay, encode_unlock_password

ABILITY = base64.b64encode(bytes(4)).decode()


class RelayTests(unittest.TestCase):
    def test_default_never_connects(self):
        with patch('vimar_relay.Client') as client:
            self.assertEqual(main([]), 0)
            client.assert_not_called()

    def test_actuation_requires_explicit_relay(self):
        with patch('vimar_relay.Client') as client:
            with self.assertRaises(SystemExit):
                main(['--open'])
            client.assert_not_called()

    def test_exact_destination_and_no_retry_after_timeout(self):
        client = Mock(secret='hash')
        client.send.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            open_relay(client, 2, 1, 'test&pin', ABILITY)
        client.send.assert_called_once()
        root = client.send.call_args.args[0]
        self.assertEqual(root.findtext('./body/command'), 'set.device.opendoor')
        self.assertEqual(root.findtext('./body/content/door'), '2')
        self.assertEqual(root.findtext('./body/content/locknumber'), '1')
        self.assertEqual(root.findtext('./body/content/password'), 'test&pin')

    def test_capability_encoding(self):
        self.assertEqual(encode_unlock_password('test', ABILITY), 'test')
        import hashlib
        flags = base64.b64encode(bytes([0, 0, 0, 1])).decode()
        self.assertEqual(encode_unlock_password('test', flags), hashlib.sha256(b'test').hexdigest())


if __name__ == '__main__':
    unittest.main()
