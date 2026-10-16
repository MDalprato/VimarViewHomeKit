"""
QUII control-plane crypto (offline RE of CQUIIStreamBase).

Matches liblive_player.so:
  EncryptData  @ 0x6938a8  — OpenSSL AES_cbc_encrypt
  SHA / SHACheck — OpenSSL SHA256
  SetIvec      @ 0x691c64  — IV = 16 * ASCII '0'
  GetKeyLen    — mode1=128-bit, mode2=256-bit
  GetEncryptMod — mode1/2 → block size 16

See re/PROTOCOL_WIRE.md §10.
"""
from __future__ import annotations

import hashlib
import struct
import time
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MSG_HDR = 0x20
AES_BLOCK = 16
IV_ASCII_ZEROS = b"0" * AES_BLOCK  # SetIvec: 16 x 0x30


def get_encrypt_mod(encrypt_mode: int) -> int:
    """CQUIIStreamBase::GetEncryptMod — padding / CBC block size."""
    if encrypt_mode == 0:
        return 1
    if encrypt_mode in (1, 2):
        return AES_BLOCK
    return AES_BLOCK  # default in binary is also 16


def get_key_len_bits(encrypt_mode: int) -> int:
    """CQUIIStreamBase::GetKeyLen — bits passed to AES_set_*_key."""
    if encrypt_mode == 0:
        return 0
    if encrypt_mode == 1:
        return 128
    if encrypt_mode == 2:
        return 256
    return 128


def get_sha_len(sha_mode: int) -> int:
    """CQUIIStreamBase::GetSHALen."""
    if sha_mode == 0:
        return 0
    if sha_mode == 1:
        return 32
    return 32


def pad_ext_data_len(body_len: int, *, encrypt_mode: int, sha_mode: int) -> int:
    """
    CQUIIStreamBase::GetExtDataLen with include_sha=True.
    Returns padded (body + SHA) length, or 0 if encrypt off / empty.
    """
    total = body_len + get_sha_len(sha_mode)
    if total == 0 or encrypt_mode == 0:
        return 0
    mod = get_encrypt_mod(encrypt_mode)
    return ((total + mod - 1) // mod) * mod


def quii_sha(data: bytes, *, sha_mode: int = 1) -> bytes:
    """
    CQUIIStreamBase::SHA — when sha_mode==1, OpenSSL SHA256(data).
    No key / HMAC. Returns b'' if sha_mode==0.
    """
    if sha_mode != 1:
        return b""
    return hashlib.sha256(data).digest()


def sha_check(data: bytes, digest: bytes, *, sha_mode: int = 1) -> bool:
    """CQUIIStreamBase::SHACheck — recompute and memcmp."""
    if sha_mode == 0:
        return True
    expect = quii_sha(data, sha_mode=sha_mode)
    return len(digest) >= len(expect) and digest[: len(expect)] == expect


def _aes_key_bytes(key_material: bytes | str, encrypt_mode: int) -> bytes:
    """
    Raw first key_bits/8 bytes of SetKey C-string (no native hex-decode).

    TODO: confirm Java/CGI never hex-decodes streamkey before SetKey.
    If the CGI value is hex text of length 64, callers may need
    bytes.fromhex(...) before passing here — native EncryptData does not.
    """
    if isinstance(key_material, str):
        key_material = key_material.encode("ascii", "strict")
    n = get_key_len_bits(encrypt_mode) // 8
    if n == 0:
        raise ValueError("encrypt_mode=0 has no AES key")
    if len(key_material) < n:
        raise ValueError(
            f"key material length {len(key_material)} < {n} bytes "
            f"required for encrypt_mode={encrypt_mode}"
        )
    return key_material[:n]


def encrypt_data(
    plaintext: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    iv: bytes = IV_ASCII_ZEROS,
) -> bytes:
    """
    CQUIIStreamBase::EncryptData encrypt path (dir=0).

    AES-CBC, IV default SetIvec ('0'*16). Length must be multiple of 16.
    """
    if len(plaintext) % AES_BLOCK != 0:
        raise ValueError(
            f"EncryptData length {len(plaintext)} not multiple of {AES_BLOCK}"
        )
    if encrypt_mode == 0:
        return plaintext
    key = _aes_key_bytes(key_material, encrypt_mode)
    if len(iv) != AES_BLOCK:
        raise ValueError("IV must be 16 bytes")
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def decrypt_data(
    ciphertext: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    iv: bytes = IV_ASCII_ZEROS,
) -> bytes:
    """EncryptData decrypt path (dir=1) — uses SetKey arg2 in the binary."""
    if len(ciphertext) % AES_BLOCK != 0:
        raise ValueError(
            f"decrypt length {len(ciphertext)} not multiple of {AES_BLOCK}"
        )
    if encrypt_mode == 0:
        return ciphertext
    key = _aes_key_bytes(key_material, encrypt_mode)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def decrypt_media_data(
    ciphertext: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    media_encrypt_on: bool = True,
    iv: bytes = IV_ASCII_ZEROS,
) -> bytes:
    """
    EncryptMediaData decrypt path (dir=1).

    Uses SetKey arg3 (this+0x388). Same AES-CBC / SetIvec as EncryptData.
    When media_encrypt_on is False (this+0x2a4==0), returns a copy (memcpy).
    playVideoEx passes the same dataEncodeKey for SetKey arg1/arg2/arg3.
    """
    if not media_encrypt_on:
        return bytes(ciphertext)
    return decrypt_data(
        ciphertext, key_material, encrypt_mode=encrypt_mode, iv=iv
    )


def decrypt_quii_header(
    wire32: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
) -> bytes:
    """OnRecv: decrypt first 32 wire bytes with EncryptData(dir=1)."""
    if encrypt_mode == 0:
        return wire32[:MSG_HDR]
    return decrypt_data(wire32[:MSG_HDR], key_material, encrypt_mode=encrypt_mode)


def encrypt_quii_packet(
    clear: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
) -> bytes:
    """
    OnSendPlay / KeepAlive wire transform when encrypt_mode != 0:

      wire = AES_CBC(hdr_32) || AES_CBC(rest)
      with SetIvec() before EACH chunk (independent IVs, both '0'*16).
    """
    if encrypt_mode == 0:
        return clear
    if len(clear) < MSG_HDR:
        raise ValueError("clear packet shorter than msgheader")
    rest = clear[MSG_HDR:]
    if len(rest) % AES_BLOCK != 0:
        raise ValueError("payload (after header) not block-aligned")
    ct_hdr = encrypt_data(
        clear[:MSG_HDR], key_material, encrypt_mode=encrypt_mode
    )
    ct_rest = encrypt_data(rest, key_material, encrypt_mode=encrypt_mode)
    return ct_hdr + ct_rest


def build_play_body(
    username: str,
    password: str,
    *,
    custom_id: bytes = b"",
    client_id: bytes = b"",
) -> bytes:
    body = username.encode() + b"&&" + password.encode() + b"\x00"
    body += custom_id + b"\x00"
    if client_id:
        body += client_id + b"\x00"
    return body


def build_play_clear(
    username: str,
    password: str,
    *,
    idc: int = 1,
    ids: int = 1,
    play_arg: int = 1,
    inner: int = 0,
    custom_id: bytes = b"",
    client_id: bytes = b"",
    timestamp: Optional[int] = None,
    encrypt_mode: int = 2,
    sha_mode: int = 1,
) -> bytes:
    """
    Cleartext Play buffer (header||body||SHA||zero_pad) as built before EncryptData.
    ids: value AFTER client's ParseURL adjust (URL ids-1).
    """
    body = build_play_body(
        username, password, custom_id=custom_id, client_id=client_id
    )
    body_len = len(body)
    sha_len = get_sha_len(sha_mode)
    if encrypt_mode != 0:
        padded = pad_ext_data_len(
            body_len, encrypt_mode=encrypt_mode, sha_mode=sha_mode
        )
    else:
        padded = body_len + sha_len

    buf = bytearray(MSG_HDR + padded)  # zero-filled
    buf[0] = 0x01
    ts = int(time.time()) if timestamp is None else int(timestamp)
    # TODO: confirm CQVTime::GetTime shape (may not be Unix epoch).
    struct.pack_into("<Q", buf, 1, ts & 0xFFFFFFFFFFFFFFFF)
    param_len = padded if encrypt_mode != 0 else 0
    # When encrypt off, OnSendPlay still writes body_len at +0x0B;
    # param_len at +9 comes from GetExtDataLen which returns 0 → stored 0.
    struct.pack_into("<H", buf, 9, param_len & 0xFFFF)
    struct.pack_into("<H", buf, 0x0B, body_len & 0xFFFF)
    struct.pack_into("<H", buf, 0x0D, idc & 0xFFFF)
    buf[0x0F] = play_arg & 0xFF
    buf[0x10] = ids & 0xFF
    buf[0x11] = 1 if inner else 0

    buf[MSG_HDR : MSG_HDR + body_len] = body
    if sha_len:
        digest = quii_sha(bytes(buf[: MSG_HDR + body_len]), sha_mode=sha_mode)
        buf[MSG_HDR + body_len : MSG_HDR + body_len + sha_len] = digest
    return bytes(buf)


def pack_play_encrypted(
    username: str,
    password: str,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    sha_mode: int = 1,
    **kwargs,
) -> bytes:
    """Full Play wire bytes for encrypt_mode!=0 (default mode 2 + sha 1)."""
    clear = build_play_clear(
        username,
        password,
        encrypt_mode=encrypt_mode,
        sha_mode=sha_mode,
        **kwargs,
    )
    return encrypt_quii_packet(clear, key_material, encrypt_mode=encrypt_mode)


def pack_open_encrypted(
    username: str,
    password: str,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    sha_mode: int = 1,
    idc: int = 0,
    **kwargs,
) -> bytes:
    """
    CQUIITalk::SendOpen — same user&&pass body as Play, wire opcode 0x0B.
    """
    clear = bytearray(
        build_play_clear(
            username,
            password,
            encrypt_mode=encrypt_mode,
            sha_mode=sha_mode,
            idc=idc,
            ids=0,
            play_arg=1,
            **kwargs,
        )
    )
    clear[0] = 0x0B
    return encrypt_quii_packet(bytes(clear), key_material, encrypt_mode=encrypt_mode)


def pack_talk_request_encrypted(
    key_material: bytes | str,
    *,
    opcode: int,
    idc: int = 0,
    arg: int = 1,
    sample_rate: int = 8000,
    encrypt_mode: int = 2,
    sha_mode: int = 1,
    timestamp: Optional[int] = None,
) -> bytes:
    """
    CQUIITalk::OnSendRequest — empty body, opcode 0x0C (start) or 0x0D.

    Header fields (talk dialect):
      +9  u16 = padded (body+SHA) length  (0 body → 32 when sha+encrypt)
      +0B u16 = idc
      +0D u8  = arg (SendPlay passes 1)
      +0E u8  = 0 (for 0x0C)
      +0F u16 = sample_rate (for 0x0C; ctor default 8000)
    """
    body_len = 0
    sha_len = get_sha_len(sha_mode)
    if encrypt_mode != 0:
        padded = pad_ext_data_len(
            body_len, encrypt_mode=encrypt_mode, sha_mode=sha_mode
        )
    else:
        padded = body_len + sha_len

    buf = bytearray(MSG_HDR + padded)
    buf[0] = opcode & 0xFF
    ts = int(time.time()) if timestamp is None else int(timestamp)
    struct.pack_into("<Q", buf, 1, ts & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<H", buf, 9, padded & 0xFFFF)
    struct.pack_into("<H", buf, 0x0B, idc & 0xFFFF)
    buf[0x0D] = arg & 0xFF
    if opcode == 0x0C:
        buf[0x0E] = 0
        struct.pack_into("<H", buf, 0x0F, sample_rate & 0xFFFF)
    if sha_len:
        digest = quii_sha(bytes(buf[:MSG_HDR]), sha_mode=sha_mode)
        buf[MSG_HDR : MSG_HDR + sha_len] = digest
    return encrypt_quii_packet(bytes(buf), key_material, encrypt_mode=encrypt_mode)


def pack_talk_audio_encrypted(
    alaw_payload: bytes,
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    sample_rate: int = 8000,
    enc_body_prefix: int = 32,
    media_flag: int = 0,
    timestamp: Optional[int] = None,
) -> bytes:
    """
    CQUIITalk::OnSendData (mode 0) — wire opcode 0xA2.

    Body (clear):
      u32  0xF0010000
      u8   codec tag (0x0E for G.711 A-law / FrameGetCodec==4)
      u8   rate code (2=8k, 4=16k)
      u16  payload length
      bytes alaw payload

    Encrypt: AES(header 32) || AES(body[:32]) || body[32:]
    (enc_body_prefix defaults to 32 from CQUIITalk ctor this+0x788)
    """
    if sample_rate == 8000:
        rate_code = 2
    elif sample_rate == 16000:
        rate_code = 4
    else:
        rate_code = 2

    plen = len(alaw_payload)
    body = bytearray(8 + plen)
    struct.pack_into("<I", body, 0, 0xF0010000)
    body[4] = 0x0E  # codec 4 → tag 0x0E
    body[5] = rate_code & 0xFF
    struct.pack_into("<H", body, 6, plen & 0xFFFF)
    body[8:] = alaw_payload
    body_len = len(body)
    if body_len < enc_body_prefix:
        raise ValueError(
            f"talk audio body {body_len} < enc prefix {enc_body_prefix}"
        )

    hdr = bytearray(MSG_HDR)
    hdr[0] = 0xA2
    ts = int(time.time()) if timestamp is None else int(timestamp)
    struct.pack_into("<Q", hdr, 1, ts & 0xFFFFFFFFFFFFFFFF)
    if encrypt_mode:
        struct.pack_into("<H", hdr, 9, enc_body_prefix & 0xFFFF)
    struct.pack_into("<I", hdr, 0x0B, body_len & 0xFFFFFFFF)
    hdr[0x0F] = media_flag & 0xFF

    if encrypt_mode == 0:
        return bytes(hdr) + bytes(body)

    ct_hdr = encrypt_data(bytes(hdr), key_material, encrypt_mode=encrypt_mode)
    ct_prefix = encrypt_data(
        bytes(body[:enc_body_prefix]), key_material, encrypt_mode=encrypt_mode
    )
    return ct_hdr + ct_prefix + bytes(body[enc_body_prefix:])


def pack_setup(*, talk: bool = False) -> bytes:
    """
    Live Setup: 0xA9 + zeros.
    Talk Setup (CQUIITalk::SendSetup): also sets hdr[9] = 2.
    """
    buf = bytearray(MSG_HDR)
    buf[0] = 0xA9
    if talk:
        buf[9] = 2
    return bytes(buf)


def pack_keepalive_encrypted(
    key_material: bytes | str,
    *,
    encrypt_mode: int = 2,
    sha_mode: int = 1,
    timestamp: Optional[int] = None,
) -> bytes:
    """KeepAlive wire opcode 0x00 (empty body + optional SHA pad)."""
    body_len = 0
    sha_len = get_sha_len(sha_mode)
    if encrypt_mode != 0:
        padded = pad_ext_data_len(
            body_len, encrypt_mode=encrypt_mode, sha_mode=sha_mode
        )
    else:
        padded = body_len + sha_len
    buf = bytearray(MSG_HDR + padded)
    buf[0] = 0x00
    ts = int(time.time()) if timestamp is None else int(timestamp)
    struct.pack_into("<Q", buf, 1, ts & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<H", buf, 9, padded if encrypt_mode else 0)
    struct.pack_into("<H", buf, 0x0B, 0)
    if sha_len:
        digest = quii_sha(bytes(buf[:MSG_HDR]), sha_mode=sha_mode)
        buf[MSG_HDR : MSG_HDR + sha_len] = digest
    return encrypt_quii_packet(bytes(buf), key_material, encrypt_mode=encrypt_mode)


# ---------------------------------------------------------------------------
# self-check / unit-style tests (no network)
# ---------------------------------------------------------------------------
def _selftest() -> None:
    assert get_key_len_bits(2) == 256
    assert get_encrypt_mod(2) == 16
    assert get_sha_len(1) == 32

    # Dummy test material only — not a real device key.
    key = b"0123456789abcdef0123456789abcdef"
    assert len(key) == 32

    # AES round-trip one block
    pt = b"\x00" * 16
    ct = encrypt_data(pt, key, encrypt_mode=2)
    assert decrypt_data(ct, key, encrypt_mode=2) == pt
    assert ct != pt

    # IV is ASCII zeros
    assert IV_ASCII_ZEROS == b"0000000000000000"

    # SHA over header||body
    clear = build_play_clear(
        "testuser",
        "deadbeef",
        timestamp=0,
        encrypt_mode=2,
        sha_mode=1,
        idc=1,
        ids=1,
    )
    body_len = struct.unpack_from("<H", clear, 0x0B)[0]
    padded = struct.unpack_from("<H", clear, 9)[0]
    assert padded % 16 == 0
    assert padded >= body_len + 32
    digest = clear[MSG_HDR + body_len : MSG_HDR + body_len + 32]
    assert digest == hashlib.sha256(clear[: MSG_HDR + body_len]).digest()
    assert sha_check(clear[: MSG_HDR + body_len], digest)

    wire = encrypt_quii_packet(clear, key, encrypt_mode=2)
    assert len(wire) == len(clear)
    # Independent CBC chunks: decrypt header alone
    hdr_pt = decrypt_data(wire[:32], key, encrypt_mode=2)
    assert hdr_pt == clear[:32]
    rest_pt = decrypt_data(wire[32:], key, encrypt_mode=2)
    assert rest_pt == clear[32:]

    # pad helper
    assert pad_ext_data_len(1, encrypt_mode=2, sha_mode=1) == 48  # 1+32 → 48

    print("quii_crypto selftest OK")
    print(f"  sample clear len={len(clear)} wire len={len(wire)}")
    print(f"  body_len={body_len} param_len(padded)={padded}")
    print(f"  sha={digest.hex()}")


if __name__ == "__main__":
    _selftest()
