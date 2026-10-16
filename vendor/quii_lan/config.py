"""
Load device settings from environment / optional `.env` file.

Never hard-code real credentials. Copy `.env.example` → `.env` locally.
"""
from __future__ import annotations

import os
from pathlib import Path

# Package root (quii-lan-client/) — parent of quii_lan/
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required setting {name}. "
            f"Copy .env.example to .env and fill in your device values."
        )
    return val


def _opt(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


class Settings:
    """Resolved at access time so importing the package does not require .env."""

    @property
    def device_ip(self) -> str:
        return _require("DEVICE_IP")

    @property
    def device_port(self) -> int:
        return int(_opt("DEVICE_PORT", "34567"))

    @property
    def device_user(self) -> str:
        # Protocol LAN username used by Qualvision / Quvii OEM apps.
        return _opt("DEVICE_USER", "adminapp2")

    @property
    def device_auth_code(self) -> bytes:
        # From device QR JSON field "c" (plain authCode, not the SHA256).
        return _require("DEVICE_AUTH_CODE").encode("utf-8")

    @property
    def device_stream_key(self) -> str:
        # From CGI get.device.streamkey → <key>…</key>
        return _require("DEVICE_STREAM_KEY")

    @property
    def talk_idc(self) -> int:
        return int(_opt("TALK_IDC", "0"))

    @property
    def web_host(self) -> str:
        return _opt("WEB_HOST", "127.0.0.1")

    @property
    def web_port(self) -> int:
        return int(_opt("WEB_PORT", "8080"))

    def password_sha256_hex(self) -> str:
        import hashlib

        return hashlib.sha256(self.device_auth_code).hexdigest()


settings = Settings()
