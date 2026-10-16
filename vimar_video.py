#!/usr/bin/env python3
"""Capture or view the Vimar's local QUII H.264 preview. No audio or relay control."""
import argparse
import getpass
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from vimar_probe import envelope
from vimar_watch import Client, ProtocolError

sys.path.insert(0, str(Path(__file__).parent / 'vendor' / 'quii_lan'))
from quii_crypto import (pack_setup, pack_play_encrypted, decrypt_quii_header,
                         pack_keepalive_encrypted)
from quii_capture_frame import media_body_len, unpack_media_payload
from quii_frame import parse_frame


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='10.0.1.192')
    p.add_argument('--port', type=int, default=34567)
    p.add_argument('--channel', type=int, default=1, help='QUII channel index (not recording channel ID)')
    p.add_argument('--seconds', type=float, default=10, help='0 means until Ctrl+C')
    p.add_argument('--output', type=Path, default=Path('output/live.h264'))
    p.add_argument('--live', action='store_true', help='View with ffplay; do not save')
    p.add_argument('--snapshot', type=Path, help='Decode first captured frame to a JPEG with ffmpeg')
    args = p.parse_args()
    if args.seconds < 0 or not 1 <= args.port <= 65535 or not 0 <= args.channel <= 65535:
        p.error('Invalid duration, port or channel')
    if args.live and args.snapshot:
        p.error('--snapshot requires file capture, not --live')
    program = 'ffplay' if args.live else ('ffmpeg' if args.snapshot else None)
    if program and not shutil.which(program):
        p.error(f'{program} is required')
    password = os.environ.get('VIMAR_PASSWORD') or getpass.getpass('Password dispositivo (non salvata): ')
    if not password:
        p.error('Empty password')
    os.umask(0o077)
    process = None
    stream = None
    frames = 0
    failed = False
    try:
        client = Client(args.host, password)
        if args.live:
            process = subprocess.Popen(['ffplay', '-hide_banner', '-loglevel', 'error',
                '-fflags', 'nobuffer', '-flags', 'low_delay', '-probesize', '32',
                '-analyzeduration', '0', '-f', 'h264', '-i', 'pipe:0'], stdin=subprocess.PIPE)
            stream = process.stdin
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            stream = args.output.open('xb')  # Never overwrite an existing capture.
        for packet in video_packets(client, args.host, args.port, args.channel, args.seconds):
            stream.write(packet)
            stream.flush()
            frames += 1
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        pass  # User closed the live player.
    except (OSError, ValueError, ET.ParseError, ProtocolError) as exc:
        print(f'Video failed: {type(exc).__name__}', file=sys.stderr)
        failed = True
    finally:
        if stream:
            try:
                stream.close()
            except BrokenPipeError:
                pass
        if process:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
    print(f'Video frames received: {frames}', file=sys.stderr)
    if failed or not frames:
        return 1
    if args.snapshot:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-i', str(args.output), '-frames:v', '1', str(args.snapshot)])
        return result.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
