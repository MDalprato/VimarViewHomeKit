import socket
import struct
import time
import xml.etree.ElementTree as ET
from .vimar_probe import envelope
from .vimar_watch import ProtocolError
from .quii_crypto import pack_setup, pack_play_encrypted, decrypt_quii_header, pack_keepalive_encrypted
from .quii_capture_frame import media_body_len, unpack_media_payload
from .quii_frame import parse_frame


def read_exact(sock, length, deadline):
    if not 0 <= length <= 2_000_000:
        raise ProtocolError('Invalid media packet size')
    data = bytearray()
    while len(data) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Capture duration reached')
        sock.settimeout(min(8, remaining))
        part = sock.recv(length - len(data))
        if not part:
            raise ConnectionError('Media stream closed')
        data.extend(part)
    return bytes(data)

def video_packets(client, host, port, channel, seconds):
    root = ET.fromstring(envelope('adminapp2', client.secret))
    root.find('./body/command').text = 'get.device.streamkey'
    root.find('./body/content').clear()
    key = client.send(root).findtext('.//key', '')
    if len(key) != 32:
        raise ProtocolError('Unexpected stream key format')
    deadline = time.monotonic() + seconds if seconds else float('inf')
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(pack_setup())
        setup = read_exact(sock, 32, min(deadline, time.monotonic() + 5))
        if setup[0] != 0xA9 or setup[9] != 0:
            raise ProtocolError('QUII setup rejected')
        enc, sha = setup[10], setup[11]
        if enc not in (0, 1, 2) or sha not in (0, 1):
            raise ProtocolError('Unsupported QUII encryption mode')
        sock.sendall(pack_play_encrypted('adminapp2', client.secret, key,
                     encrypt_mode=enc, sha_mode=sha, idc=channel, ids=1, play_arg=1))
        keepalive = time.monotonic() + 5
        while time.monotonic() < deadline:
            if time.monotonic() >= keepalive:
                sock.sendall(pack_keepalive_encrypted(key, encrypt_mode=enc, sha_mode=sha))
                keepalive = time.monotonic() + 5
            try:
                header = decrypt_quii_header(read_exact(sock, 32, deadline), key, encrypt_mode=enc)
                media = 0xA0 <= header[0] <= 0xA3
                length = media_body_len(header) if media else struct.unpack_from('<H', header, 9)[0]
                body = read_exact(sock, length, deadline)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    return
                raise
            if media:
                payload = unpack_media_payload(header, body, key=key, encrypt_mode=enc)
                frame = parse_frame(payload)
                if frame and frame.is_video:
                    if frame.length > len(payload) - 20:
                        raise ProtocolError('Truncated video frame')
                    yield frame.payload
