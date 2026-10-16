#!/usr/bin/env python3
"""
QUII Setup→Play→media: decrypt one frame and save JPEG or annex-B H264.

Offline RE (liblive_player.so):
  playVideoEx(url, dataEncodeKey) → QVPlayerSetKey(key, key, key)
    SetKey arg1/arg2/arg3 are identical (streamkey / dataEncodeKey).
  Control RX: EncryptData + SetKey arg2, IV='0'*16
  Media body: EncryptMediaData + SetKey arg3, IV='0'*16 (this+0x2a4 defaults 1)
  Media assemble: OnRecvData — need = u32le(hdr+0x0B) + 0x20
  PackFrame: optional EncryptData(param_len@+9); if hdr[0x0F]!=0 → EncryptMediaData

Device-safety: ONE short session; never opendoor; no CGI flood.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

from quii_crypto import (
    MSG_HDR,
    decrypt_data,
    decrypt_media_data,
    decrypt_quii_header,
    pack_play_encrypted,
    pack_setup,
)
from config import settings

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PICS = ROOT / "out"
DEFAULT_RX = ROOT / "out" / "capture.bin"
DEFAULT_JPG = PICS / "frame.jpg"
DEFAULT_H264 = PICS / "frame.h264"


def _password() -> str:
    return settings.password_sha256_hex()


def recv_exact(sock: socket.socket, n: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def recv_some(sock: socket.socket, max_bytes: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    try:
        return sock.recv(max_bytes)
    except socket.timeout:
        return b""


def find_jpeg(blob: bytes) -> bytes | None:
    i = blob.find(b"\xff\xd8\xff")
    if i < 0:
        return None
    j = blob.find(b"\xff\xd9", i + 3)
    if j < 0:
        return blob[i:]  # truncated but still useful
    return blob[i : j + 2]


def find_annexb(blob: bytes) -> bytes | None:
    """Return from first 00 00 01 / 00 00 00 01 if it looks like H.264 NAL."""
    for sc in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        i = blob.find(sc)
        if i < 0:
            continue
        nal0 = blob[i + len(sc)] if i + len(sc) < len(blob) else 0
        nal_type = nal0 & 0x1F
        # SPS/PPS/IDR/non-IDR/SEI are common
        if nal_type in (1, 5, 6, 7, 8, 9):
            return blob[i:]
        # also accept if later NALs look right
        if b"\x00\x00\x00\x01" in blob[i + 4 :] or b"\x00\x00\x01" in blob[i + 3 :]:
            return blob[i:]
    return None


def media_body_len(hdr: bytes) -> int:
    """OnRecvData copies 4 bytes from hdr+0x0B as need-0x20."""
    if len(hdr) < 0x0F:
        return 0
    u32 = struct.unpack_from("<I", hdr, 0x0B)[0]
    u16 = struct.unpack_from("<H", hdr, 0x0B)[0]
    # Prefer uint32 when high half is zero / reasonable
    if 0 < u32 <= 2_000_000:
        return u32
    if 0 < u16 <= 0xFFFF:
        return u16
    # fallback: command-style param_len at +9
    return struct.unpack_from("<H", hdr, 9)[0]


def unpack_media_payload(
    clear_hdr: bytes,
    body_cipher: bytes,
    *,
    key: str,
    encrypt_mode: int,
) -> bytes:
    """
    Mirror PackFrame decrypt on assembled [clear_hdr||wire_body].

    Observed live A0 frames (hdr[0x0F]==0, param_len@+9==32, off10==0):
      - AES-CBC EncryptData only the first param_len body bytes (SetKey arg2)
      - remainder is already plaintext (PES / annex-B)
      - EncryptMediaData runs only when hdr[0x0F] != 0 (SetKey arg3)
    """
    param9 = struct.unpack_from("<H", clear_hdr, 9)[0]
    off10 = struct.unpack_from("<H", clear_hdr, 0x10)[0]
    media_flag = clear_hdr[0x0F]

    body = body_cipher
    if (
        param9 >= 1
        and encrypt_mode
        and len(body) >= param9
        and param9 % 16 == 0
    ):
        head = decrypt_data(body[:param9], key, encrypt_mode=encrypt_mode)
        body = head + body[param9:]

    if media_flag != 0 and encrypt_mode:
        start = min(off10, len(body))
        media_ct = body[start:]
        aligned = len(media_ct) - (len(media_ct) % 16)
        if aligned > 0:
            pt = decrypt_media_data(
                media_ct[:aligned],
                key,
                encrypt_mode=encrypt_mode,
                media_encrypt_on=True,
            )
            body = body[:start] + pt + media_ct[aligned:]

    # CFramePack consumes from assembled_buf + 0x20 + off10
    if off10 and off10 < len(body):
        return body[off10:]
    return body


def strip_to_annexb(payload: bytes) -> bytes | None:
    """Prefer annex-B from first SPS/IDR; else any NAL; else PES 00 00 01 Ex."""
    for needle in (
        b"\x00\x00\x00\x01\x67",  # SPS some profiles
        b"\x00\x00\x00\x01\x27",
        b"\x00\x00\x01\x67",
        b"\x00\x00\x01\x27",
        b"\x00\x00\x00\x01\x65",  # IDR
        b"\x00\x00\x00\x01\x25",
        b"\x00\x00\x01\x65",
        b"\x00\x00\x01\x25",
    ):
        i = payload.find(needle)
        if i >= 0:
            return payload[i:]
    ab = find_annexb(payload)
    if ab:
        return ab
    for sc in (b"\x00\x00\x01\xe0", b"\x00\x00\x01\xe1", b"\x00\x00\x00\x01"):
        i = payload.find(sc)
        if i >= 0 and len(payload) - i > 64:
            return payload[i:]
    return None


def extract_and_save(payload: bytes, out_jpg: Path, out_h264: Path) -> Path | None:
    jpg = find_jpeg(payload)
    if jpg and len(jpg) > 100:
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        out_jpg.write_bytes(jpg)
        return out_jpg
    h264 = strip_to_annexb(payload)
    if h264 and len(h264) > 32:
        out_h264.parent.mkdir(parents=True, exist_ok=True)
        out_h264.write_bytes(h264)
        return out_h264
    return None


def parse_stream(
    wire: bytes,
    *,
    key: str,
    encrypt_mode: int,
    out_jpg: Path,
    out_h264: Path,
    dump_dir: Path | None = None,
) -> Path | None:
    """Parse post-Setup TCP byte stream (may include Play ACK + media)."""
    pos = 0
    msg_i = 0
    saved: Path | None = None
    while pos + MSG_HDR <= len(wire):
        chunk = wire[pos : pos + MSG_HDR]
        try:
            hdr = decrypt_quii_header(chunk, key, encrypt_mode=encrypt_mode)
        except Exception as e:
            print(f"hdr decrypt fail @ {pos}: {e}", flush=True)
            break
        cmd = hdr[0]
        print(
            f"msg[{msg_i}] off={pos} cmd=0x{cmd:02X} hdr={hdr[:16].hex()}",
            flush=True,
        )

        if 0xA0 <= cmd <= 0xA3:
            body_len = media_body_len(hdr)
            total = MSG_HDR + body_len
            print(f"  media body_len={body_len} total={total}", flush=True)
            if pos + total > len(wire):
                print(
                    f"  incomplete media need={total} have={len(wire) - pos}",
                    flush=True,
                )
                break
            body_ct = wire[pos + MSG_HDR : pos + total]
            payload = unpack_media_payload(
                hdr, body_ct, key=key, encrypt_mode=encrypt_mode
            )
            if dump_dir is not None:
                dump_dir.mkdir(parents=True, exist_ok=True)
                (dump_dir / f"media_{msg_i:02d}_hdr.bin").write_bytes(hdr)
                (dump_dir / f"media_{msg_i:02d}_pt.bin").write_bytes(payload)
            print(
                f"  payload len={len(payload)} head={payload[:32].hex()}",
                flush=True,
            )
            has_key = any(
                x in payload
                for x in (
                    b"\x00\x00\x00\x01\x67",
                    b"\x00\x00\x00\x01\x27",
                    b"\x00\x00\x01\x67",
                    b"\x00\x00\x01\x27",
                    b"\x00\x00\x00\x01\x65",
                    b"\x00\x00\x00\x01\x25",
                    b"\x00\x00\x01\x65",
                    b"\x00\x00\x01\x25",
                    b"\xff\xd8\xff",
                )
            )
            if has_key or saved is None:
                cand = extract_and_save(payload, out_jpg, out_h264)
                if cand:
                    saved = cand
                    print(
                        f"  SAVED {saved} ({'keyframe' if has_key else 'non-key'})",
                        flush=True,
                    )
                    if has_key:
                        return saved
            pos += total
            msg_i += 1
            continue

        # Control / 0xFE / Play ACK: param_len at +9
        param_len = struct.unpack_from("<H", hdr, 9)[0]
        if cmd == 0xFE:
            # OnRecvCommand also peeks uint16 at +0x0B (extra field)
            extra = struct.unpack_from("<H", hdr, 0x0B)[0]
            print(f"  FE param_len={param_len} field_0B={extra}", flush=True)
        total = MSG_HDR + param_len
        if pos + total > len(wire):
            print(
                f"  incomplete ctrl need={total} have={len(wire) - pos}",
                flush=True,
            )
            break
        body_ct = wire[pos + MSG_HDR : pos + total]
        body_pt = b""
        if param_len and encrypt_mode and param_len % 16 == 0:
            try:
                body_pt = decrypt_data(body_ct, key, encrypt_mode=encrypt_mode)
                print(f"  ctrl body pt head={body_pt[:48].hex()}", flush=True)
            except Exception as e:
                print(f"  ctrl body decrypt fail: {e}", flush=True)
                body_pt = body_ct
        elif param_len:
            body_pt = body_ct
        if dump_dir is not None:
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / f"ctrl_{msg_i:02d}_hdr.bin").write_bytes(hdr)
            if body_pt:
                (dump_dir / f"ctrl_{msg_i:02d}_body.bin").write_bytes(body_pt)
        # Sometimes JPEG is smuggled in control — scan
        if body_pt:
            saved = extract_and_save(body_pt, out_jpg, out_h264)
            if saved:
                print(f"  SAVED from ctrl {saved}", flush=True)
                return saved
        pos += total
        msg_i += 1

    if saved is not None:
        print(f"using non-keyframe candidate {saved}", flush=True)
    return saved


def live_capture(
    *,
    host: str,
    port: int,
    key: str,
    read_bytes: int,
    read_timeout: float,
    out_rx: Path,
) -> tuple[bytes, int]:
    pwd = _password()
    user = settings.device_user
    print(f"connect {host}:{port}", flush=True)
    time.sleep(1.0)
    sock = socket.create_connection((host, port), 5)
    try:
        sock.sendall(pack_setup())
        setup_rx = recv_exact(sock, 32, 4.0)
        print(f"setup-rx {setup_rx.hex()}", flush=True)
        if len(setup_rx) < 32 or setup_rx[0] != 0xA9 or setup_rx[9] != 0:
            raise RuntimeError("setup failed")
        enc_mode = setup_rx[0x0A]
        sha_mode = setup_rx[0x0B]
        print(f"enc_mode={enc_mode} sha_mode={sha_mode}", flush=True)

        play = pack_play_encrypted(
            user,
            pwd,
            key,
            encrypt_mode=enc_mode,
            sha_mode=sha_mode,
            idc=1,
            ids=1,
            play_arg=1,
        )
        print(f"play-tx len={len(play)}", flush=True)
        sock.sendall(play)

        data = bytearray()
        deadline = time.time() + read_timeout
        while len(data) < read_bytes and time.time() < deadline:
            chunk = recv_some(sock, min(8192, read_bytes - len(data)), 1.5)
            if not chunk:
                if data:
                    # brief grace for more media
                    chunk = recv_some(sock, 8192, 0.8)
                    if not chunk:
                        break
                else:
                    continue
            data.extend(chunk)
            print(f"rx total={len(data)}", flush=True)
        out_rx.write_bytes(bytes(data))
        print(f"saved raw rx {out_rx} ({len(data)} bytes)", flush=True)
        return bytes(data), enc_mode
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QUII capture one media frame")
    ap.add_argument("--offline", type=Path, default=None, help="parse existing rx bin")
    ap.add_argument("--live", action="store_true", help="ONE short live session")
    ap.add_argument("--host", default=None, help="defaults to DEVICE_IP from .env")
    ap.add_argument("--port", type=int, default=None, help="defaults to DEVICE_PORT")
    ap.add_argument("--key", default=None, help="defaults to DEVICE_STREAM_KEY")
    ap.add_argument("--encrypt-mode", type=int, default=2)
    ap.add_argument("--read-bytes", type=int, default=256 * 1024)
    ap.add_argument("--read-timeout", type=float, default=8.0)
    ap.add_argument("--out-rx", type=Path, default=DEFAULT_RX)
    ap.add_argument("--out-jpg", type=Path, default=DEFAULT_JPG)
    ap.add_argument("--out-h264", type=Path, default=DEFAULT_H264)
    ap.add_argument("--dump-dir", type=Path, default=HERE / "frame_dump")
    args = ap.parse_args(argv)

    enc_mode = args.encrypt_mode
    host = args.host or settings.device_ip
    port = args.port if args.port is not None else settings.device_port
    key = args.key or settings.device_stream_key

    if args.offline is not None:
        path = args.offline
        if not path.is_file():
            print(f"missing {path}", flush=True)
            return 2
        wire = path.read_bytes()
        print(f"offline {path} len={len(wire)}", flush=True)
    elif args.live:
        wire, enc_mode = live_capture(
            host=host,
            port=port,
            key=key,
            read_bytes=args.read_bytes,
            read_timeout=args.read_timeout,
            out_rx=args.out_rx,
        )
    else:
        if DEFAULT_RX.is_file() and DEFAULT_RX.stat().st_size > 64:
            print(f"auto-offline {DEFAULT_RX}", flush=True)
            wire = DEFAULT_RX.read_bytes()
        else:
            print(
                "No capture file. Pass --offline PATH or --live (one session).",
                flush=True,
            )
            return 2

    saved = parse_stream(
        wire,
        key=key,
        encrypt_mode=enc_mode,
        out_jpg=args.out_jpg,
        out_h264=args.out_h264,
        dump_dir=args.dump_dir,
    )
    if saved:
        print(f"OK saved={saved} size={saved.stat().st_size}", flush=True)
        return 0
    print("NO_FRAME saved — see dumps / blockers", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
