import struct
from .quii_crypto import MSG_HDR, decrypt_data, decrypt_media_data

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
